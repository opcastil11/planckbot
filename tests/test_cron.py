"""Tests for the cron engine (store, job registry, daemon dispatch)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from planckbot.cron.daemon import Daemon
from planckbot.cron.jobs import JobContext, JobRegistry, default_registry
from planckbot.cron.store import CronStore
from planckbot.db.models import CronJob


@pytest.fixture
def cron_store(conn):
    return CronStore(conn)


# --- CronStore --------------------------------------------------------------


def test_add_sets_next_run_at(cron_store):
    j = CronJob(name="j1", job_type="noop", interval_seconds=60)
    cron_store.add(j)
    loaded = cron_store.get(j.id)
    assert loaded is not None
    assert loaded.next_run_at is not None


def test_by_name(cron_store):
    j = CronJob(name="hello", job_type="noop", interval_seconds=30)
    cron_store.add(j)
    assert cron_store.by_name("hello").id == j.id
    assert cron_store.by_name("missing") is None


def test_list_due_filters_enabled_and_time(cron_store):
    now = datetime.now(timezone.utc)
    # Past next_run → due
    past = CronJob(
        name="past", job_type="noop", interval_seconds=60,
        next_run_at=(now - timedelta(seconds=10)).isoformat(),
    )
    # Future next_run → not due
    future = CronJob(
        name="future", job_type="noop", interval_seconds=60,
        next_run_at=(now + timedelta(hours=1)).isoformat(),
    )
    # Disabled & past → not due
    disabled = CronJob(
        name="disabled", job_type="noop", interval_seconds=60, enabled=0,
        next_run_at=(now - timedelta(seconds=10)).isoformat(),
    )
    cron_store.add(past)
    cron_store.add(future)
    cron_store.add(disabled)

    due = cron_store.list_due(now)
    assert [j.name for j in due] == ["past"]


def test_set_enabled_and_delete(cron_store):
    j = CronJob(name="togglable", job_type="noop", interval_seconds=60)
    cron_store.add(j)
    cron_store.set_enabled(j.id, False)
    assert cron_store.get(j.id).enabled == 0

    cron_store.delete(j.id)
    assert cron_store.get(j.id) is None


def test_mark_run_advances_next_run_at(cron_store):
    j = CronJob(name="run", job_type="noop", interval_seconds=120)
    cron_store.add(j)
    marker = datetime(2099, 1, 1, tzinfo=timezone.utc)
    cron_store.mark_run(j.id, status="ok", output="hello", now=marker)

    loaded = cron_store.get(j.id)
    assert loaded.last_status == "ok"
    assert loaded.last_output == "hello"
    assert loaded.last_run_at == marker.isoformat()
    assert loaded.next_run_at == (marker + timedelta(seconds=120)).isoformat()


def test_mark_run_truncates_huge_output(cron_store):
    j = CronJob(name="big", job_type="noop", interval_seconds=60)
    cron_store.add(j)
    huge = "x" * 10_000
    cron_store.mark_run(j.id, status="ok", output=huge)
    loaded = cron_store.get(j.id)
    assert len(loaded.last_output) <= 4000


def test_reschedule_now_makes_job_due(cron_store):
    j = CronJob(
        name="later", job_type="noop", interval_seconds=3600,
        next_run_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    cron_store.add(j)
    assert cron_store.list_due() == []
    cron_store.reschedule_now(j.id)
    assert [d.id for d in cron_store.list_due()] == [j.id]


# --- JobRegistry -----------------------------------------------------------


def test_default_registry_has_expected_types():
    reg = default_registry()
    assert set(reg.types()) == {
        "noop", "autolabel", "retrain", "conversation_scanner",
        "detect_tool_gaps",
    }


def test_noop_job_returns_message():
    reg = default_registry()
    fn = reg.get("noop")
    result = fn(JobContext(conn=None, params={"message": "ping"}))
    assert "ping" in result


def test_autolabel_requires_tool_and_reference():
    reg = default_registry()
    fn = reg.get("autolabel")
    with pytest.raises(ValueError, match="tool"):
        fn(JobContext(conn=None, params={}))


def test_retrain_requires_tool():
    reg = default_registry()
    fn = reg.get("retrain")
    with pytest.raises(ValueError, match="tool"):
        fn(JobContext(conn=None, params={}))


def test_retrain_skips_when_not_enough_new_labeled(conn):
    """If no new labeled triples accumulated for a tool, retrain no-ops."""
    reg = default_registry()
    fn = reg.get("retrain")
    out = fn(JobContext(conn=conn, params={"tool": "no_such_tool", "min_new_labeled": 5}))
    assert "skip" in out


# --- Daemon ----------------------------------------------------------------


def test_daemon_run_job_success(conn, cron_store):
    j = CronJob(
        name="noop-job", job_type="noop",
        interval_seconds=60, params={"message": "hi"},
    )
    cron_store.add(j)
    daemon = Daemon(conn, registry=default_registry())
    status, output = daemon.run_job(j.id)
    assert status == "ok"
    assert "hi" in output

    loaded = cron_store.get(j.id)
    assert loaded.last_status == "ok"
    assert loaded.last_run_at is not None


def test_daemon_run_job_unknown_type(conn, cron_store):
    j = CronJob(name="weird", job_type="does_not_exist", interval_seconds=60)
    cron_store.add(j)
    daemon = Daemon(conn, registry=default_registry())
    status, output = daemon.run_job(j.id)
    assert status == "error"
    assert "unknown job_type" in output

    loaded = cron_store.get(j.id)
    assert loaded.last_status == "error"


def test_daemon_run_job_captures_exception(conn, cron_store):
    reg = JobRegistry()

    def _boom(_ctx):
        raise RuntimeError("kaboom")

    reg.register("boom", _boom)
    j = CronJob(name="b", job_type="boom", interval_seconds=60)
    cron_store.add(j)

    daemon = Daemon(conn, registry=reg)
    status, output = daemon.run_job(j.id)
    assert status == "error"
    assert "kaboom" in output

    loaded = cron_store.get(j.id)
    assert loaded.last_status == "error"


def test_daemon_run_job_nonexistent(conn):
    daemon = Daemon(conn, registry=default_registry())
    status, output = daemon.run_job("no-such-id")
    assert status == "error"
    assert "not found" in output


def test_daemon_lock_prevents_double_run(conn, tmp_path):
    lock = tmp_path / "d.lock"
    d1 = Daemon(conn, registry=default_registry(), lock_path=lock)
    d1._acquire_lock()
    try:
        d2 = Daemon(conn, registry=default_registry(), lock_path=lock)
        with pytest.raises(RuntimeError, match="already holds"):
            d2._acquire_lock()
    finally:
        d1._release_lock()
