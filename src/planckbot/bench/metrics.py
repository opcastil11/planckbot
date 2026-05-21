"""Bench metrics: token counting, aggregation, CSV export.

Token counting reuses the project-wide approximation in
`experiments/metrics.py` so bench numbers stay comparable with the rest of
the system (cost estimates, training logs, dashboard widgets).
"""

from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from planckbot.experiments.metrics import count_tokens_approx


def token_count(text: str | None) -> int:
    """Project-wide approximate token count. ~4 chars/token. Idempotent
    with the values stored in `triples.input_tokens`/`output_tokens`."""
    if text is None:
        return 0
    return count_tokens_approx(text)


# --- shape of one replay row ---------------------------------------------


@dataclass
class BenchResult:
    """One aggregated row: a technique's performance on a slice."""
    technique: str
    slice_key: str            # e.g. project_id, tool_name, "ALL"
    slice_kind: str           # "project" | "tool" | "overall"
    triples: int
    triples_affected: int
    tokens_output_total: int  # baseline tokens in this slice
    tokens_saved: int
    tokens_at_risk: int

    @property
    def affected_pct(self) -> float:
        return 100 * self.triples_affected / self.triples if self.triples else 0.0

    @property
    def savings_pct(self) -> float:
        return (
            100 * self.tokens_saved / self.tokens_output_total
            if self.tokens_output_total else 0.0
        )

    @property
    def risk_pct(self) -> float:
        return (
            100 * self.tokens_at_risk / max(self.tokens_saved, 1)
        )

    def to_row(self) -> dict:
        return {
            "technique": self.technique,
            "slice_kind": self.slice_kind,
            "slice_key": self.slice_key,
            "triples": self.triples,
            "triples_affected": self.triples_affected,
            "tokens_output_total": self.tokens_output_total,
            "tokens_saved": self.tokens_saved,
            "tokens_at_risk": self.tokens_at_risk,
            "affected_pct": round(self.affected_pct, 2),
            "savings_pct": round(self.savings_pct, 2),
            "risk_pct": round(self.risk_pct, 2),
        }


# --- aggregation ---------------------------------------------------------


def _accumulate(rows: Iterable[dict], key_fn) -> dict[str, dict]:
    buckets: dict[str, dict] = {}
    for r in rows:
        k = key_fn(r)
        b = buckets.setdefault(k, {
            "triples": 0,
            "triples_affected": 0,
            "tokens_output_total": 0,
            "tokens_saved": 0,
            "tokens_at_risk": 0,
        })
        b["triples"] += 1
        b["triples_affected"] += int(r.get("affected", 0))
        b["tokens_output_total"] += int(r.get("output_tokens", 0))
        b["tokens_saved"] += int(r.get("tokens_saved", 0))
        b["tokens_at_risk"] += int(r.get("tokens_at_risk", 0))
    return buckets


def aggregate(
    replay_rows: Iterable[dict],
    technique_name: str | None = None,
) -> list[BenchResult]:
    """Roll per-triple replay rows into overall + per-project + per-tool
    BenchResult rows. Pass `technique_name` to override the technique
    label (otherwise inferred from the first row).
    """
    rows = list(replay_rows)
    if not rows:
        return []
    tech = technique_name or rows[0].get("technique", "?")

    out: list[BenchResult] = []

    overall = _accumulate(rows, lambda r: "ALL")
    for k, b in overall.items():
        out.append(BenchResult(
            technique=tech, slice_kind="overall", slice_key=k, **b,
        ))

    by_project = _accumulate(rows, lambda r: str(r.get("project_id") or "_unscoped"))
    for k, b in by_project.items():
        out.append(BenchResult(
            technique=tech, slice_kind="project", slice_key=k, **b,
        ))

    by_tool = _accumulate(rows, lambda r: str(r.get("tool_name") or "?"))
    for k, b in by_tool.items():
        out.append(BenchResult(
            technique=tech, slice_kind="tool", slice_key=k, **b,
        ))

    return out


# --- CSV writers ---------------------------------------------------------


def write_results_csv(
    results: Iterable[BenchResult] | Iterable[dict],
    path: Path | str,
) -> int:
    """Write replay rows OR aggregated BenchResult rows to CSV.

    If items are BenchResult, calls `to_row()`. If items are dicts, writes
    them as-is. Header is derived from the first row.
    """
    items = list(results)
    if not items:
        Path(path).write_text("")
        return 0
    rows = [r.to_row() if isinstance(r, BenchResult) else r for r in items]
    fieldnames = list(rows[0].keys())
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# --- DB-side helpers used by techniques ----------------------------------


def fetch_session_neighbors(
    conn: sqlite3.Connection,
    session_id: str,
) -> list[tuple[str, str]]:
    """Convenience: (created_at, tool_name) pairs in a session. Used by
    techniques that want to compute structural session metrics without
    re-loading every triple body."""
    cur = conn.execute(
        "SELECT created_at, tool_name FROM triples "
        "WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    )
    return [(r["created_at"], r["tool_name"]) for r in cur.fetchall()]


def tool_volume_summary(
    conn: sqlite3.Connection,
    project_id: str | None = None,
) -> list[dict]:
    """Per-tool counts + total output tokens. Useful as a baseline ranking
    before any technique is run — tells you where the token budget is."""
    args: list = []
    where = ""
    if project_id is not None:
        where = " WHERE project_id = ?"
        args.append(project_id)
    cur = conn.execute(
        f"SELECT tool_name, COUNT(*) AS n, "
        f"COALESCE(SUM(output_tokens), 0) AS tokens "
        f"FROM triples{where} GROUP BY tool_name ORDER BY tokens DESC",
        args,
    )
    return [
        {"tool_name": r["tool_name"], "n": r["n"], "tokens": r["tokens"]}
        for r in cur.fetchall()
    ]
