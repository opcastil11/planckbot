"""Cross-process event bus for the /activity live-log page.

The proxy runs as a subprocess of Claude Code; the cron daemon is its
own long-lived process; the UI is a third. All three share the same
SQLite DB, so we use a simple append-only `activity_events` table as
the bus: each component calls `log_event(conn, source, kind, message,
**meta)` at interesting moments, and the UI polls `since_id` to render
a live feed.

This is NOT structured logging — it's a "what is PlanckBot doing right
now?" feed meant for humans. Don't log anything here that needs to be
machine-queryable later (use the real tables for that).

Rotation is app-side: `trim_events` keeps only the newest `keep` rows.
Call it occasionally — e.g. once every 100 inserts or from a cron job.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


# Bounded default so a runaway loop can't fill the disk. UI only shows
# the last few hundred anyway; keeping 10k on disk is plenty of history.
DEFAULT_MAX_ROWS = 10_000

VALID_SOURCES: frozenset[str] = frozenset({
    "proxy", "cron", "synth", "training", "ui",
})


@dataclass(frozen=True)
class ActivityEvent:
    id: int
    ts: datetime
    source: str
    kind: str
    message: str
    meta: dict[str, Any]
    project_id: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(
    conn: sqlite3.Connection,
    source: str,
    kind: str,
    message: str,
    *,
    project_id: str | None = None,
    meta: dict[str, Any] | None = None,
    commit: bool = True,
) -> int:
    """Append one event to `activity_events`. Returns the new row id.

    Safe to call on any connection that's had `migrate()` run on it.
    Fails soft: on any SQLite error (table missing on a pre-v7 DB,
    locked DB, etc.) returns -1 rather than raising — activity logging
    is best-effort, never a reason to fail the real work the caller
    was doing.
    """
    if source not in VALID_SOURCES:
        # Keep this loud — a typo in a new call site would silently
        # produce un-filterable events in the UI.
        raise ValueError(
            f"unknown activity source {source!r}; must be one of "
            f"{sorted(VALID_SOURCES)}"
        )
    try:
        cur = conn.execute(
            "INSERT INTO activity_events "
            "(ts, source, kind, message, meta, project_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                source,
                kind,
                message,
                json.dumps(meta) if meta else None,
                project_id,
            ),
        )
        if commit:
            conn.commit()
        return int(cur.lastrowid or -1)
    except sqlite3.OperationalError:
        # Pre-v7 DB or locked — swallow.
        return -1


def list_events(
    conn: sqlite3.Connection,
    *,
    since_id: int = 0,
    limit: int = 200,
    source: str | None = None,
    project_id: str | None = None,
) -> list[ActivityEvent]:
    """Return events newer than `since_id`, newest last (chronological).

    The UI uses `since_id` to fetch only rows it hasn't rendered yet —
    cheap incremental polling without dragging the whole table across.
    """
    where = ["id > ?"]
    params: list[Any] = [since_id]
    if source is not None:
        where.append("source = ?")
        params.append(source)
    if project_id is not None:
        where.append("project_id = ?")
        params.append(project_id)
    sql = (
        "SELECT id, ts, source, kind, message, meta, project_id "
        "FROM activity_events WHERE " + " AND ".join(where) +
        " ORDER BY id ASC LIMIT ?"
    )
    params.append(limit)
    return [_row_to_event(row) for row in conn.execute(sql, params)]


def recent_events(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    source: str | None = None,
    project_id: str | None = None,
) -> list[ActivityEvent]:
    """Return the newest `limit` events, chronological (oldest first).

    Used on first page load so the user sees the tail of recent activity
    before the live poller takes over.
    """
    where: list[str] = []
    params: list[Any] = []
    if source is not None:
        where.append("source = ?")
        params.append(source)
    if project_id is not None:
        where.append("project_id = ?")
        params.append(project_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, ts, source, kind, message, meta, project_id "
        "FROM activity_events" + clause +
        " ORDER BY id DESC LIMIT ?"
    )
    params.append(limit)
    rows = list(conn.execute(sql, params))
    rows.reverse()   # chronological for display
    return [_row_to_event(r) for r in rows]


def trim_events(
    conn: sqlite3.Connection,
    *,
    keep: int = DEFAULT_MAX_ROWS,
) -> int:
    """Delete all but the newest `keep` rows. Returns rows deleted.

    Cheap: a single DELETE with a correlated subquery. Safe to call
    during normal operation — if the table doesn't exist yet (pre-v7),
    we return 0.
    """
    try:
        cur = conn.execute(
            "DELETE FROM activity_events WHERE id <= ("
            "  SELECT MAX(id) - ? FROM activity_events"
            ")",
            (keep,),
        )
        conn.commit()
        return cur.rowcount or 0
    except sqlite3.OperationalError:
        return 0


def count_events(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM activity_events"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


# --- internal --------------------------------------------------------------


def _row_to_event(row: Iterable[Any]) -> ActivityEvent:
    rid, ts, source, kind, message, meta, project_id = row
    try:
        ts_dt = datetime.fromisoformat(ts)
    except ValueError:
        ts_dt = datetime.now(timezone.utc)
    meta_dict: dict[str, Any] = {}
    if meta:
        try:
            parsed = json.loads(meta)
            if isinstance(parsed, dict):
                meta_dict = parsed
        except json.JSONDecodeError:
            pass
    return ActivityEvent(
        id=int(rid),
        ts=ts_dt,
        source=source,
        kind=kind,
        message=message,
        meta=meta_dict,
        project_id=project_id,
    )
