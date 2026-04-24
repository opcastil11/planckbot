"""Pattern detector for Layer D — turns usage traces into tool-gap hypotheses.

Algorithm (intentionally simple so we can reason about the output):

1. Load triples in chronological order.
2. Slide a time window (default 30s) and collect every N-gram of consecutive
   tool names that fit inside the window.
3. Count how often each N-gram appears.
4. Any N-gram that repeats at least `min_occurrences` times becomes a
   candidate gap. We record one `SequenceMatch` per candidate with pointers
   to up to `max_examples` concrete triple IDs so the synthesizer can look
   at real inputs/outputs when generating code.

We don't do any statistical tests (chi-sq, mutual info, …) here. They'd be
meaningful at 100k+ triples; at the volume we realistically have, exact
repetition counting is the right signal.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from planckbot.db.models import GapReport, Triple


@dataclass
class SequenceMatch:
    sequence: tuple[str, ...]
    occurrences: int
    example_triple_ids: list[str] = field(default_factory=list)


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_tool_sequences(
    triples: Iterable[Triple],
    *,
    ngram_min: int = 2,
    ngram_max: int = 3,
    window_seconds: float = 30.0,
    min_occurrences: int = 2,
    max_examples: int = 5,
) -> list[SequenceMatch]:
    """Return every (N-gram → count) pair that repeats at least
    `min_occurrences` times within the given time window.

    Only triples with source starting with 'proxy:' are considered — manual
    fixtures would bias the detector.
    """
    filtered: list[Triple] = []
    for t in triples:
        if not (t.source or "").startswith("proxy:"):
            continue
        if t.tool_name:
            filtered.append(t)
    filtered.sort(key=lambda t: t.created_at)

    buckets: dict[tuple[str, ...], list[str]] = {}
    n = len(filtered)
    for i in range(n):
        t_i = filtered[i]
        ts_i = _parse_ts(t_i.created_at)
        if ts_i is None:
            continue
        for length in range(ngram_min, ngram_max + 1):
            if i + length > n:
                break
            window_end = filtered[i + length - 1]
            ts_end = _parse_ts(window_end.created_at)
            if ts_end is None:
                continue
            if (ts_end - ts_i).total_seconds() > window_seconds:
                continue
            seq = tuple(filtered[i + k].tool_name for k in range(length))
            # Single-tool N-grams are noise (just "called read_file a lot").
            if len(set(seq)) < 2:
                continue
            buckets.setdefault(seq, []).append(t_i.id)

    matches: list[SequenceMatch] = []
    for seq, ids in buckets.items():
        if len(ids) >= min_occurrences:
            matches.append(
                SequenceMatch(
                    sequence=seq,
                    occurrences=len(ids),
                    example_triple_ids=ids[:max_examples],
                )
            )
    matches.sort(key=lambda m: -m.occurrences)
    return matches


# --- persistence ------------------------------------------------------------


class GapReportStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, report: GapReport) -> GapReport:
        row = report.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO gap_reports ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return report

    def get(self, report_id: str) -> GapReport | None:
        cur = self.conn.execute(
            "SELECT * FROM gap_reports WHERE id = ?", (report_id,)
        )
        row = cur.fetchone()
        return GapReport.from_row(row) if row else None

    def list_open(
        self, limit: int = 50, project_id: str | None = None
    ) -> list[GapReport]:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT * FROM gap_reports WHERE status = 'open' "
                "ORDER BY occurrences DESC, created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM gap_reports WHERE status = 'open' "
                "  AND (project_id = ? OR project_id IS NULL) "
                "ORDER BY occurrences DESC, created_at DESC LIMIT ?",
                (project_id, limit),
            )
        return [GapReport.from_row(r) for r in cur.fetchall()]

    def list_all(
        self, limit: int = 50, project_id: str | None = None
    ) -> list[GapReport]:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT * FROM gap_reports ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM gap_reports "
                "WHERE project_id = ? OR project_id IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
        return [GapReport.from_row(r) for r in cur.fetchall()]

    def set_status(self, report_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE gap_reports SET status = ? WHERE id = ?",
            (status, report_id),
        )
        self.conn.commit()

    def count(self, project_id: str | None = None) -> int:
        if project_id is None:
            return self.conn.execute(
                "SELECT COUNT(*) FROM gap_reports"
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM gap_reports "
            "WHERE project_id = ? OR project_id IS NULL",
            (project_id,),
        ).fetchone()[0]

    def find_by_sequence(self, sequence: tuple[str, ...]) -> GapReport | None:
        """Dedup helper: the detector runs on a cron so we don't want to
        multiply identical reports each tick."""
        payload = json.dumps(list(sequence))
        cur = self.conn.execute(
            "SELECT * FROM gap_reports WHERE tool_sequence = ? AND status = 'open' "
            "ORDER BY created_at DESC LIMIT 1",
            (payload,),
        )
        row = cur.fetchone()
        return GapReport.from_row(row) if row else None


def build_gap_reports(
    conn: sqlite3.Connection,
    matches: list[SequenceMatch],
    *,
    project_id: str | None = None,
) -> tuple[int, int]:
    """Persist `matches` as gap reports. Returns (created, updated).

    If an open report for the same sequence already exists, increment its
    occurrences instead of inserting a duplicate. When `project_id` is
    supplied, new reports are tagged to it (existing ones keep their
    original project tag — a pattern that spans two projects shouldn't
    accidentally rewrite history).
    """
    store = GapReportStore(conn)
    created = updated = 0
    for m in matches:
        existing = store.find_by_sequence(m.sequence)
        if existing is not None:
            # Update existing record: keep the earlier created_at, but bump
            # the counts and refresh the example list.
            conn.execute(
                "UPDATE gap_reports SET occurrences = ?, example_triple_ids = ? "
                "WHERE id = ?",
                (
                    m.occurrences,
                    json.dumps(m.example_triple_ids),
                    existing.id,
                ),
            )
            conn.commit()
            updated += 1
        else:
            report = GapReport(
                tool_sequence=list(m.sequence),
                occurrences=m.occurrences,
                example_triple_ids=m.example_triple_ids,
                proposed_name="_".join(m.sequence) + "_combined",
                proposed_description=(
                    f"Candidate tool: combines {' → '.join(m.sequence)} "
                    f"(observed {m.occurrences} times)."
                ),
                project_id=project_id,
            )
            store.insert(report)
            created += 1
    return created, updated
