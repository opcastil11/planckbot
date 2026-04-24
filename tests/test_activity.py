"""Tests for activity.py — the cross-process event-bus helper."""

from __future__ import annotations

import sqlite3

import pytest

from planckbot import activity
from planckbot.db.engine import get_connection
from planckbot.db.migrations import SCHEMA_VERSION


# --- schema ---------------------------------------------------------------


def test_schema_v7_includes_activity_events(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(activity_events)")]
    assert set(cols) >= {"id", "ts", "source", "kind", "message", "meta",
                         "project_id"}


def test_schema_version_advanced_to_7():
    assert SCHEMA_VERSION == 7


# --- log_event ------------------------------------------------------------


def test_log_event_inserts_and_returns_id(conn):
    rid = activity.log_event(conn, "proxy", "rx", "hello")
    assert rid > 0
    row = conn.execute(
        "SELECT source, kind, message FROM activity_events WHERE id = ?",
        (rid,),
    ).fetchone()
    assert tuple(row) == ("proxy", "rx", "hello")


def test_log_event_rejects_unknown_source(conn):
    with pytest.raises(ValueError, match="unknown activity source"):
        activity.log_event(conn, "bogus", "rx", "hi")


def test_log_event_persists_meta_as_json(conn):
    rid = activity.log_event(
        conn, "cron", "job_end", "ran retrain",
        meta={"duration_ms": 1234, "job_name": "retrain-file_search"},
    )
    row = conn.execute(
        "SELECT meta FROM activity_events WHERE id = ?", (rid,),
    ).fetchone()
    import json
    assert json.loads(row[0]) == {
        "duration_ms": 1234, "job_name": "retrain-file_search",
    }


def test_log_event_on_pre_v7_db_returns_negative_one(tmp_path):
    """Activity logging is best-effort. A pre-v7 DB (no activity_events
    table) must not crash the caller — it returns -1."""
    db = tmp_path / "pre_v7.db"
    conn = sqlite3.connect(db)
    # No migrate() — table doesn't exist.
    rid = activity.log_event(conn, "proxy", "rx", "hi")
    assert rid == -1


# --- list / recent --------------------------------------------------------


def test_list_events_incremental_via_since_id(conn):
    ids = [
        activity.log_event(conn, "proxy", "rx", f"m{i}")
        for i in range(5)
    ]
    newer = activity.list_events(conn, since_id=ids[2])
    # Only rows with id > ids[2] (i.e. m3, m4).
    assert [e.message for e in newer] == ["m3", "m4"]


def test_list_events_filters_by_source(conn):
    activity.log_event(conn, "proxy", "rx", "p")
    activity.log_event(conn, "cron", "job_start", "c")
    activity.log_event(conn, "synth", "activate", "s")
    proxy_only = activity.list_events(conn, source="proxy")
    assert len(proxy_only) == 1
    assert proxy_only[0].source == "proxy"


def test_list_events_filters_by_project(conn):
    activity.log_event(conn, "proxy", "rx", "p-a", project_id="aaa")
    activity.log_event(conn, "proxy", "rx", "p-b", project_id="bbb")
    activity.log_event(conn, "proxy", "rx", "p-none")  # no project
    scoped = activity.list_events(conn, project_id="aaa")
    assert [e.message for e in scoped] == ["p-a"]


def test_recent_events_returns_chronological_despite_reverse_query(conn):
    """`recent_events` pulls DESC but must return chronological order
    so the UI can `.append` without shuffling."""
    for i in range(5):
        activity.log_event(conn, "proxy", "rx", f"m{i}")
    rows = activity.recent_events(conn, limit=3)
    # Only the newest 3, but in chronological order.
    assert [e.message for e in rows] == ["m2", "m3", "m4"]


# --- rotation -------------------------------------------------------------


def test_trim_events_keeps_newest_n(conn):
    for i in range(20):
        activity.log_event(conn, "proxy", "rx", f"m{i}")
    deleted = activity.trim_events(conn, keep=5)
    assert deleted == 15
    remaining = activity.recent_events(conn, limit=100)
    assert [e.message for e in remaining] == [
        f"m{i}" for i in range(15, 20)
    ]


def test_trim_events_no_op_on_small_table(conn):
    for i in range(3):
        activity.log_event(conn, "proxy", "rx", f"m{i}")
    # keep=5, only 3 rows → nothing to delete.
    assert activity.trim_events(conn, keep=5) == 0


# --- row decoding ---------------------------------------------------------


def test_row_to_event_decodes_meta_json(conn):
    activity.log_event(
        conn, "proxy", "store", "saved triple",
        meta={"triple_id": "abc-123", "tokens": 81},
    )
    events = activity.recent_events(conn, limit=1)
    assert events[0].meta == {"triple_id": "abc-123", "tokens": 81}


def test_row_to_event_handles_null_meta(conn):
    activity.log_event(conn, "proxy", "rx", "no meta here")
    events = activity.recent_events(conn, limit=1)
    assert events[0].meta == {}


# --- integration: proxy actually logs through the helper ------------------


def test_proxy_emits_rx_upstream_store_events(conn):
    """End-to-end: PlanckProxy on a trivial tool produces the expected
    event trail (rx → upstream → store). This is what drives the
    /activity page."""
    from planckbot.proxy.intercept import ObserveMode, PlanckProxy
    from planckbot.tools.triples import TriplesStore

    store = TriplesStore(conn)
    proxy = PlanckProxy(store=store, mode=ObserveMode())

    def upstream(query: str) -> str:
        return f"result for {query}"

    wrapped = proxy.wrap(upstream, "demo_tool")
    wrapped(query="hello")

    kinds = [e.kind for e in activity.recent_events(
        conn, source="proxy", limit=50,
    )]
    assert "rx" in kinds
    assert "upstream" in kinds
    assert "store" in kinds
    # And in order.
    assert kinds.index("rx") < kinds.index("upstream") < kinds.index("store")
