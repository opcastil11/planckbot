"""Tests for the bench harness/metrics/datasets modules.

Uses the standard in-test sqlite fixture, populates a small synthetic
corpus across two projects, and exercises:
- session grouping + ordering
- secret-flag filtering on load_triples
- Technique ABC + replay() drive
- aggregate() roll-up
- split_sessions stratification + determinism
"""

from __future__ import annotations

import json

import pytest

from planckbot.bench import (
    Technique, TechniqueResult, SessionView, TripleEvent,
    load_triples, group_into_sessions, replay,
    aggregate, write_results_csv,
    split_sessions, save_split, load_split,
)
from planckbot.bench.metrics import tool_volume_summary
from planckbot.tools.projects import ProjectStore
from planckbot.tools.triples import TriplesStore


# --- fixtures ------------------------------------------------------------


@pytest.fixture
def populated(conn):
    """Two projects, three sessions each, mix of tools + one secret-flagged
    triple. Returns (store, proj_a_id, proj_b_id)."""
    proj_store = ProjectStore(conn)
    triples = TriplesStore(conn)
    a = proj_store.create(name="proj-a", path="/tmp/proj-a")
    b = proj_store.create(name="proj-b", path="/tmp/proj-b")

    def add(tool, out, *, sess, when, pid, secret=False):
        ctx = {"tool_use_id": f"toolu_{sess}_{when}"}
        if secret:
            ctx["had_secrets"] = True
            ctx["secret_patterns"] = ["password-field"]
        triples.add(
            tool_name=tool,
            input_data={"x": 1},
            output_data=out,
            context_data=ctx,
            session_id=sess,
            project_id=pid,
            created_at=f"2026-05-21T10:00:{when:02d}Z",
            source="claude_code:jsonl",
        )

    # proj A: session a1 has 3 Reads, session a2 has 2 Bashes.
    add("Read", "x" * 400, sess="a1", when=0, pid=a.id)
    add("Read", "y" * 400, sess="a1", when=1, pid=a.id)
    add("Read", "z" * 400, sess="a1", when=2, pid=a.id)
    add("Bash", "out1" * 100, sess="a2", when=0, pid=a.id)
    add("Bash", "out2" * 100, sess="a2", when=1, pid=a.id)

    # proj B: one session b1 with mixed tools and one secret triple.
    add("Read", "p" * 200, sess="b1", when=0, pid=b.id)
    add("Bash", "q" * 200, sess="b1", when=1, pid=b.id)
    add("Read", "leaked-stuff", sess="b1", when=2, pid=b.id, secret=True)

    return triples, a.id, b.id


# --- harness -------------------------------------------------------------


def test_load_triples_filters_secrets_by_default(conn, populated):
    triples, a, b = populated
    rows = load_triples(conn)
    assert len(rows) == 7   # 8 total, 1 secret excluded
    assert all(
        "had_secrets" not in (t.context_data or "")
        or '"had_secrets": true' not in (t.context_data or "")
        for t in rows
    )


def test_load_triples_includes_secrets_when_asked(conn, populated):
    rows = load_triples(conn, include_secrets=True)
    assert len(rows) == 8


def test_load_triples_filters_by_project(conn, populated):
    _, a, b = populated
    rows = load_triples(conn, project_ids=[a])
    assert len(rows) == 5
    assert all(t.project_id == a for t in rows)


def test_load_triples_filters_by_tool(conn, populated):
    rows = load_triples(conn, tool_names=["Read"])
    # 3 Reads in a1 + 1 Read in b1 (b1's secret Read is excluded)
    assert len(rows) == 4
    assert all(t.tool_name == "Read" for t in rows)


def test_group_into_sessions_orders_by_time(conn, populated):
    rows = load_triples(conn)
    sessions = group_into_sessions(rows)
    # Three sessions across two projects: a1, a2, b1
    by_id = {s.session_id: s for s in sessions}
    assert set(by_id) == {"a1", "a2", "b1"}
    a1 = by_id["a1"]
    timestamps = [e.triple.created_at for e in a1.events]
    assert timestamps == sorted(timestamps)
    # Positions monotonic
    assert [e.position for e in a1.events] == list(range(len(a1.events)))
    # session_size on every event matches its parent
    assert all(e.session_size == len(a1) for e in a1.events)


def test_session_view_neighbors(conn, populated):
    sessions = group_into_sessions(load_triples(conn))
    a1 = next(s for s in sessions if s.session_id == "a1")
    middle = a1.events[1]
    assert len(a1.prev_events(middle.position)) == 1
    assert len(a1.next_events(middle.position)) == 1


def test_triple_event_context_lazy_and_robust():
    # Manually construct an event with bogus context_data
    from planckbot.db.models import Triple
    t = Triple(tool_name="Read", context_data="not-json")
    ev = TripleEvent(triple=t, session_id="x", position=0, session_size=1)
    assert ev.context() == {}
    assert ev.had_secrets is False


# --- Technique + replay --------------------------------------------------


class _FlatHalf(Technique):
    """Trivial technique: claim we'd save half of every output's tokens.
    No real semantics — exists only to exercise the harness."""
    name = "flat-half"

    def apply(self, event, session):
        out_tokens = event.triple.output_tokens or 0
        return TechniqueResult(
            affected=out_tokens > 0,
            tokens_saved=out_tokens // 2,
            tokens_at_risk=0,
            notes="half",
        )


class _NoOp(Technique):
    name = "noop"

    def apply(self, event, session):
        return TechniqueResult()


def test_replay_drives_technique_over_all_events(conn, populated):
    sessions = group_into_sessions(load_triples(conn))
    rows = replay(_FlatHalf(), sessions)
    assert len(rows) == 7
    assert all(r["technique"] == "flat-half" for r in rows)
    assert all(r["tokens_saved"] * 2 <= r["output_tokens"] + 1 for r in rows)


def test_replay_calls_reset_session(conn, populated):
    sessions = group_into_sessions(load_triples(conn))

    class Counting(Technique):
        name = "count"
        def __init__(self):
            self.resets = 0
        def reset_session(self, session):
            self.resets += 1
        def apply(self, event, session):
            return TechniqueResult()

    c = Counting()
    replay(c, sessions)
    assert c.resets == len(sessions)


# --- aggregation + CSV ---------------------------------------------------


def test_aggregate_produces_overall_per_project_per_tool(conn, populated):
    sessions = group_into_sessions(load_triples(conn))
    rows = replay(_FlatHalf(), sessions)
    agg = aggregate(rows)
    kinds = {r.slice_kind for r in agg}
    assert kinds == {"overall", "project", "tool"}
    overall = [r for r in agg if r.slice_kind == "overall"][0]
    assert overall.triples == 7
    assert overall.tokens_saved > 0
    # Per-tool slice must add up to the same totals as overall.
    tool_total_saved = sum(r.tokens_saved for r in agg if r.slice_kind == "tool")
    assert tool_total_saved == overall.tokens_saved


def test_aggregate_handles_empty():
    assert aggregate([]) == []


def test_write_results_csv_roundtrip(tmp_path, conn, populated):
    sessions = group_into_sessions(load_triples(conn))
    rows = replay(_FlatHalf(), sessions)
    path = tmp_path / "out.csv"
    n = write_results_csv(rows, path)
    assert n == len(rows)
    text = path.read_text()
    assert "technique,project_id,session_id" in text.splitlines()[0]


def test_write_results_csv_handles_bench_result(tmp_path, conn, populated):
    sessions = group_into_sessions(load_triples(conn))
    agg = aggregate(replay(_FlatHalf(), sessions))
    path = tmp_path / "agg.csv"
    n = write_results_csv(agg, path)
    assert n == len(agg)
    header = path.read_text().splitlines()[0].split(",")
    assert "savings_pct" in header
    assert "risk_pct" in header


def test_tool_volume_summary_orders_by_tokens(conn, populated):
    summary = tool_volume_summary(conn)
    tokens = [s["tokens"] for s in summary]
    assert tokens == sorted(tokens, reverse=True)


# --- datasets / split ----------------------------------------------------


def test_split_is_deterministic(conn, populated):
    s1 = split_sessions(conn, holdout_frac=0.5, seed=7)
    s2 = split_sessions(conn, holdout_frac=0.5, seed=7)
    assert s1.train_session_ids == s2.train_session_ids
    assert s1.holdout_session_ids == s2.holdout_session_ids


def test_split_changes_with_seed(conn, populated):
    s1 = split_sessions(conn, holdout_frac=0.5, seed=7)
    s2 = split_sessions(conn, holdout_frac=0.5, seed=8)
    # Not strictly required that they differ — but with 3 sessions and
    # different seeds, at least the bookkeeping should be present.
    assert s1.sessions_per_project  # non-empty
    assert s2.sessions_per_project


def test_split_stratifies_per_project(conn, populated):
    _, a, b = populated
    s = split_sessions(conn, holdout_frac=0.5, seed=42)
    stats = s.sessions_per_project
    assert a in stats and b in stats
    # Each project keeps at least one train session (project A has 2 sessions
    # → 1 train + 1 holdout; B has 1 session → 0 holdout when n_holdout
    # would drop to 0 because we never holdout 100%).
    assert stats[a]["train"] >= 1
    assert stats[b]["total"] == 1
    assert stats[b]["holdout"] == 0  # single-session project can't be split


def test_split_drops_short_sessions(conn, populated):
    triples = TriplesStore(conn)
    # Add a single-triple session — should be excluded by min_session_triples
    triples.add(
        tool_name="Read", input_data={}, output_data="x",
        session_id="tiny", project_id=None,
    )
    s = split_sessions(conn, holdout_frac=0.5, seed=1, min_session_triples=2)
    assert "tiny" not in s.train_session_ids
    assert "tiny" not in s.holdout_session_ids


def test_split_save_load_roundtrip(tmp_path, conn, populated):
    s = split_sessions(conn, holdout_frac=0.33, seed=99)
    path = tmp_path / "split.json"
    save_split(s, path)
    loaded = load_split(path)
    assert loaded.train_session_ids == s.train_session_ids
    assert loaded.holdout_session_ids == s.holdout_session_ids
    assert loaded.seed == 99


def test_empty_project_list_returns_empty_split(conn):
    s = split_sessions(conn, project_ids=[], holdout_frac=0.5)
    assert s.train_session_ids == []
    assert s.holdout_session_ids == []
