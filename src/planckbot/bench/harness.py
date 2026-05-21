"""Benchmark harness: load + group triples, drive Technique replay.

Reads from `data/planckbot.db` (or any sqlite3 connection passed in), filters
by project / tool / secret-flag / session, groups into temporally-ordered
sessions, and runs a `Technique` over each triple. Techniques observe each
triple plus its session neighbors and report tokens saved + at risk.

Design notes:
- Unit of evaluation is a SESSION, not a triple. Cache / dedup / compaction
  only make sense in flow.
- Triples flagged with `had_secrets=True` are skipped by default — they
  carry `[REDACTED:...]` markers that would distort compression metrics.
- Token counts come from the stored `input_tokens` / `output_tokens` columns
  (populated at ingest time via `count_tokens_approx`). Techniques can
  override via metrics.token_count for finer-grained measurements.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from planckbot.db.models import Triple


# --- data shapes ----------------------------------------------------------


@dataclass
class TripleEvent:
    """A triple plus its position inside the session it belongs to."""
    triple: Triple
    session_id: str
    position: int      # 0-indexed within session
    session_size: int  # total triples in this session

    # Parsed context, lazy. None if no context_data or unparseable.
    _ctx: dict | None = field(default=None, repr=False)

    def context(self) -> dict:
        if self._ctx is not None:
            return self._ctx
        raw = self.triple.context_data
        if not raw:
            self._ctx = {}
            return self._ctx
        try:
            parsed = json.loads(raw)
            self._ctx = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            self._ctx = {}
        return self._ctx

    @property
    def had_secrets(self) -> bool:
        return bool(self.context().get("had_secrets"))


@dataclass
class SessionView:
    """All triples in one session, sorted ascending by created_at."""
    session_id: str
    project_id: str | None
    events: list[TripleEvent]

    def __iter__(self) -> Iterator[TripleEvent]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def prev_events(self, position: int) -> list[TripleEvent]:
        return self.events[:position]

    def next_events(self, position: int) -> list[TripleEvent]:
        return self.events[position + 1:]


# --- Technique ABC --------------------------------------------------------


@dataclass
class TechniqueResult:
    """Per-triple outcome from applying a Technique.

    Fields are the comparable axes across techniques. `tokens_saved` is the
    primary headline; `tokens_at_risk` flags content that was dropped but
    might have been needed downstream (proxy for false-positive rate).
    """
    affected: bool = False
    tokens_saved: int = 0
    tokens_at_risk: int = 0
    notes: str = ""


class Technique(ABC):
    """A candidate optimization. Stateful is allowed (e.g. a cache that
    accumulates across a session); the harness resets it between sessions
    via `reset_session()`. The harness does NOT reset across projects, so
    techniques that learn cross-session (embedding cache) survive."""

    name: str = "<unnamed>"

    def reset_session(self, session: SessionView) -> None:
        """Called once at the start of each session before any apply()."""
        pass

    @abstractmethod
    def apply(
        self,
        event: TripleEvent,
        session: SessionView,
    ) -> TechniqueResult:
        """Evaluate the technique on `event` given the session it sits in.

        Implementations should NOT mutate `event.triple` — return savings
        estimates instead. Use `session.prev_events(event.position)` to look
        at history; use `session.next_events(...)` for risk assessment
        (proxy for "was this content cited later?").
        """
        ...


# --- loading + grouping ---------------------------------------------------


def load_triples(
    conn: sqlite3.Connection,
    *,
    project_ids: Iterable[str] | None = None,
    tool_names: Iterable[str] | None = None,
    include_secrets: bool = False,
    session_ids: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[Triple]:
    """Pull triples from the DB with bench-relevant filters applied.

    Defaults: skip secret-flagged triples, no limit. Order is ascending by
    `created_at` so callers can stream chronologically.
    """
    clauses: list[str] = []
    args: list = []
    if project_ids is not None:
        ids = list(project_ids)
        if not ids:
            return []
        clauses.append(
            "project_id IN (" + ",".join("?" for _ in ids) + ")"
        )
        args.extend(ids)
    if tool_names is not None:
        names = list(tool_names)
        if not names:
            return []
        clauses.append(
            "tool_name IN (" + ",".join("?" for _ in names) + ")"
        )
        args.extend(names)
    if session_ids is not None:
        sids = list(session_ids)
        if not sids:
            return []
        clauses.append(
            "session_id IN (" + ",".join("?" for _ in sids) + ")"
        )
        args.extend(sids)
    if not include_secrets:
        clauses.append(
            "(context_data IS NULL "
            "OR context_data NOT LIKE '%\"had_secrets\": true%')"
        )

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM triples{where} ORDER BY created_at ASC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    cur = conn.execute(sql, args)
    return [Triple.from_row(r) for r in cur.fetchall()]


def group_into_sessions(triples: Iterable[Triple]) -> list[SessionView]:
    """Bucket triples by session_id, sorted by created_at ascending within.

    Triples with `session_id=None` are grouped under a synthetic key
    `"_no_session_<project_id>"` so legacy rows don't all collapse together.
    """
    buckets: dict[str, list[Triple]] = {}
    proj_for_session: dict[str, str | None] = {}
    for t in triples:
        sid = t.session_id or f"_no_session_{t.project_id or 'null'}"
        buckets.setdefault(sid, []).append(t)
        # Capture project_id on first encounter; subsequent triples of the
        # same session should share it (sessions don't cross projects).
        proj_for_session.setdefault(sid, t.project_id)

    sessions: list[SessionView] = []
    for sid, items in buckets.items():
        items.sort(key=lambda x: x.created_at or "")
        events = [
            TripleEvent(
                triple=t, session_id=sid,
                position=i, session_size=len(items),
            )
            for i, t in enumerate(items)
        ]
        sessions.append(SessionView(
            session_id=sid,
            project_id=proj_for_session[sid],
            events=events,
        ))
    sessions.sort(key=lambda s: (s.project_id or "", s.session_id))
    return sessions


# --- replay driver --------------------------------------------------------


def replay(
    technique: Technique,
    sessions: Iterable[SessionView],
) -> list[dict]:
    """Run `technique` across every event in every session.

    Returns one dict per event with the per-triple result fields plus
    identifying metadata. The dicts are flat, ready to be DataFrame'd or
    written via metrics.write_results_csv.

    Side-effect: the technique's `reset_session()` is called at session
    boundaries so it can clear per-session state (caches, counters).
    """
    rows: list[dict] = []
    for s in sessions:
        technique.reset_session(s)
        for ev in s.events:
            res = technique.apply(ev, s)
            rows.append({
                "technique": technique.name,
                "project_id": s.project_id,
                "session_id": s.session_id,
                "position": ev.position,
                "session_size": ev.session_size,
                "triple_id": ev.triple.id,
                "tool_name": ev.triple.tool_name,
                "input_tokens": ev.triple.input_tokens or 0,
                "output_tokens": ev.triple.output_tokens or 0,
                "affected": int(bool(res.affected)),
                "tokens_saved": int(res.tokens_saved),
                "tokens_at_risk": int(res.tokens_at_risk),
                "notes": res.notes,
            })
    return rows
