"""Tests for ToolCacheStore + schema v8.

Covers: schema migration, put/lookup happy path, mtime staleness, dirty
flag, hit recording, session purge, cross-session isolation.
"""

from __future__ import annotations

import os
import time

import pytest

from planckbot.db.engine import get_connection
from planckbot.tools.cache import ToolCacheStore


@pytest.fixture
def store(conn):
    return ToolCacheStore(conn)


# --- schema --------------------------------------------------------------


def test_schema_v8_creates_tool_cache(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_cache'"
    )
    assert cur.fetchone() is not None


def test_schema_v8_indexes_exist(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tool_cache'"
    )
    names = {row["name"] for row in cur.fetchall()}
    assert "idx_tool_cache_lookup" in names
    assert "idx_tool_cache_path" in names


# --- put / lookup happy path --------------------------------------------


def test_put_and_lookup_returns_entry(store):
    e = store.put(
        session_id="s1", tool_name="Read", cache_key="/x.py",
        content="line1\nline2",
    )
    assert e.id
    assert e.content_tokens > 0

    hit = store.lookup(
        session_id="s1", tool_name="Read", cache_key="/x.py",
        verify_mtime=False,
    )
    assert hit is not None
    assert hit.content == "line1\nline2"


def test_lookup_miss_returns_none(store):
    assert store.lookup(
        session_id="empty", tool_name="Read", cache_key="/anything",
        verify_mtime=False,
    ) is None


def test_put_same_key_replaces(store):
    store.put(session_id="s1", tool_name="Read", cache_key="/x", content="v1")
    store.put(session_id="s1", tool_name="Read", cache_key="/x", content="v2")
    hit = store.lookup(
        session_id="s1", tool_name="Read", cache_key="/x", verify_mtime=False,
    )
    assert hit.content == "v2"
    # No duplicates left behind
    assert store.count(session_id="s1") == 1


# --- session isolation ---------------------------------------------------


def test_cache_is_session_scoped(store):
    store.put(session_id="A", tool_name="Read", cache_key="/x", content="vA")
    store.put(session_id="B", tool_name="Read", cache_key="/x", content="vB")

    a = store.lookup(
        session_id="A", tool_name="Read", cache_key="/x", verify_mtime=False,
    )
    b = store.lookup(
        session_id="B", tool_name="Read", cache_key="/x", verify_mtime=False,
    )
    assert a.content == "vA"
    assert b.content == "vB"


# --- mtime staleness -----------------------------------------------------


def test_mtime_match_returns_entry(tmp_path, store):
    p = tmp_path / "real.py"
    p.write_text("hello")
    mtime = p.stat().st_mtime_ns

    store.put(
        session_id="s1", tool_name="Read", cache_key=str(p),
        content="cached body", file_path=str(p), file_mtime_ns=mtime,
    )
    hit = store.lookup(
        session_id="s1", tool_name="Read", cache_key=str(p),
        verify_mtime=True,
    )
    assert hit is not None


def test_mtime_mismatch_returns_none(tmp_path, store):
    p = tmp_path / "real.py"
    p.write_text("hello")
    mtime = p.stat().st_mtime_ns
    store.put(
        session_id="s1", tool_name="Read", cache_key=str(p),
        content="cached", file_path=str(p), file_mtime_ns=mtime,
    )

    time.sleep(0.01)
    p.write_text("changed")

    miss = store.lookup(
        session_id="s1", tool_name="Read", cache_key=str(p),
        verify_mtime=True,
    )
    assert miss is None


def test_missing_file_returns_none(tmp_path, store):
    p = tmp_path / "ghost.py"
    store.put(
        session_id="s1", tool_name="Read", cache_key=str(p),
        content="...", file_path=str(p), file_mtime_ns=12345,
    )
    miss = store.lookup(
        session_id="s1", tool_name="Read", cache_key=str(p),
        verify_mtime=True,
    )
    assert miss is None


# --- dirty flag ----------------------------------------------------------


def test_mark_dirty_invalidates_lookup(store):
    store.put(
        session_id="s1", tool_name="Read", cache_key="/x",
        content="v1", file_path="/x",
    )
    rows = store.mark_dirty(session_id="s1", file_path="/x")
    assert rows == 1
    assert store.lookup(
        session_id="s1", tool_name="Read", cache_key="/x", verify_mtime=False,
    ) is None


def test_mark_dirty_does_not_cross_sessions(store):
    store.put(
        session_id="A", tool_name="Read", cache_key="/x",
        content="vA", file_path="/x",
    )
    store.put(
        session_id="B", tool_name="Read", cache_key="/x",
        content="vB", file_path="/x",
    )
    store.mark_dirty(session_id="A", file_path="/x")
    # A is dirty, B is not
    assert store.lookup(
        session_id="A", tool_name="Read", cache_key="/x", verify_mtime=False,
    ) is None
    assert store.lookup(
        session_id="B", tool_name="Read", cache_key="/x", verify_mtime=False,
    ) is not None


# --- hit telemetry -------------------------------------------------------


def test_record_hit_increments(store):
    e = store.put(
        session_id="s1", tool_name="Read", cache_key="/x", content="v",
    )
    assert e.hits == 0
    store.record_hit(e.id)
    store.record_hit(e.id)
    cur = store.conn.execute(
        "SELECT hits, last_hit_at FROM tool_cache WHERE id = ?", (e.id,),
    )
    row = cur.fetchone()
    assert row["hits"] == 2
    assert row["last_hit_at"] is not None


def test_stats_summary_aggregates(store):
    e1 = store.put(session_id="s1", tool_name="Read", cache_key="/a", content="a" * 40)
    e2 = store.put(session_id="s1", tool_name="Read", cache_key="/b", content="b" * 100)
    store.record_hit(e1.id)
    store.record_hit(e2.id)
    store.record_hit(e2.id)
    summary = store.stats_summary()
    assert summary["entries"] == 2
    assert summary["total_hits"] == 3
    assert summary["tokens_cached"] > 0


# --- housekeeping --------------------------------------------------------


def test_purge_session_removes_only_that_session(store):
    store.put(session_id="A", tool_name="Read", cache_key="/x", content="v")
    store.put(session_id="B", tool_name="Read", cache_key="/x", content="v")
    n = store.purge_session("A")
    assert n == 1
    assert store.count() == 1
    assert store.count(session_id="B") == 1


def test_purge_older_than(store):
    store.put(session_id="s", tool_name="Read", cache_key="/x", content="v")
    # Same instant or earlier shouldn't delete anything we just inserted.
    n = store.purge_older_than("1970-01-01T00:00:00+00:00")
    assert n == 0
    # Future timestamp should sweep everything.
    n2 = store.purge_older_than("2999-01-01T00:00:00+00:00")
    assert n2 == 1


def test_list_for_session_ordered_newest_first(store):
    a = store.put(session_id="s", tool_name="Read", cache_key="/a", content="x")
    time.sleep(0.001)
    b = store.put(session_id="s", tool_name="Read", cache_key="/b", content="x")
    entries = store.list_for_session("s")
    assert entries[0].cache_key == "/b"
    assert entries[1].cache_key == "/a"


# --- migration idempotency ----------------------------------------------


def test_migrate_is_idempotent(tmp_path):
    """Running migrate twice on the same DB shouldn't error."""
    from planckbot.db.migrations import migrate
    db = tmp_path / "twice.db"
    c1 = get_connection(db)
    # Schema is applied on open already; calling migrate again is no-op
    migrate(c1)
    c1.close()
    c2 = get_connection(db)
    migrate(c2)
    cur = c2.execute("SELECT COUNT(*) FROM tool_cache")
    assert cur.fetchone()[0] == 0
    c2.close()
