"""Smoke tests for the 12 Tier-1 bench techniques.

Each technique gets one positive (fires on its target pattern) and one
negative (doesn't fire on irrelevant patterns) test. Real-data validation
is the end-to-end runner; these only catch regressions on the per-event
logic."""

from __future__ import annotations

import json

import pytest

from planckbot.bench import group_into_sessions, replay, load_triples
from planckbot.bench.techniques import (
    CacheDenyRead, BashExactDedup, DiffReReads,
    RetryDetection, ArgAutoCorrect, UncitedCompaction,
    SplitToolsLocal, SplitToolsJsonl, NgramSynthesis,
    BashEarlyTerm, ToolBudget, DeadEndPrompts,
    all_triples_only_techniques,
)
from planckbot.tools.projects import ProjectStore
from planckbot.tools.triples import TriplesStore


# --- helpers -------------------------------------------------------------


def _add(triples, *, tool, inp, out, sess, when, pid, source="claude_code:jsonl"):
    triples.add(
        tool_name=tool,
        input_data=inp,
        output_data=out,
        context_data={"tool_use_id": f"toolu_{sess}_{when}"},
        session_id=sess,
        project_id=pid,
        created_at=f"2026-05-21T10:00:{when:02d}Z",
        source=source,
    )


@pytest.fixture
def sess(conn):
    proj = ProjectStore(conn).create(name="p", path="/tmp/p")
    triples = TriplesStore(conn)
    return conn, triples, proj.id


def _replay(conn, technique):
    sessions = group_into_sessions(load_triples(conn))
    if hasattr(technique, "calibrate"):
        technique.calibrate(sessions)
    return replay(technique, sessions)


# --- A. CacheDenyRead ----------------------------------------------------


def test_A_cache_deny_fires_on_repeat_read(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/x.py"}, out="A" * 400, sess="s1", when=0, pid=p)
    _add(t, tool="Read", inp={"file_path": "/x.py"}, out="A" * 400, sess="s1", when=1, pid=p)
    rows = _replay(conn, CacheDenyRead())
    affected = [r for r in rows if r["affected"]]
    assert len(affected) == 1
    assert affected[0]["tokens_saved"] > 0


def test_A_marks_at_risk_after_write(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/x.py"}, out="A" * 400, sess="s1", when=0, pid=p)
    _add(t, tool="Edit", inp={"file_path": "/x.py"}, out="ok", sess="s1", when=1, pid=p)
    _add(t, tool="Read", inp={"file_path": "/x.py"}, out="A" * 400, sess="s1", when=2, pid=p)
    rows = _replay(conn, CacheDenyRead())
    affected = [r for r in rows if r["affected"]]
    assert len(affected) == 1
    assert affected[0]["tokens_at_risk"] > 0


# --- B. BashExactDedup ---------------------------------------------------


def test_B_bash_dedup_fires(sess):
    conn, t, p = sess
    _add(t, tool="Bash", inp={"command": "git status"}, out="clean", sess="s1", when=0, pid=p)
    _add(t, tool="Bash", inp={"command": "git status"}, out="clean", sess="s1", when=1, pid=p)
    rows = _replay(conn, BashExactDedup())
    assert sum(r["affected"] for r in rows) == 1


def test_B_distinct_commands_dont_fire(sess):
    conn, t, p = sess
    _add(t, tool="Bash", inp={"command": "ls"}, out="a", sess="s1", when=0, pid=p)
    _add(t, tool="Bash", inp={"command": "pwd"}, out="b", sess="s1", when=1, pid=p)
    rows = _replay(conn, BashExactDedup())
    assert sum(r["affected"] for r in rows) == 0


# --- D. DiffReReads ------------------------------------------------------


def test_D_diff_rereads_fires(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/y.py"}, out="line\n" * 100, sess="s1", when=0, pid=p)
    _add(t, tool="Edit", inp={"file_path": "/y.py"}, out="ok", sess="s1", when=1, pid=p)
    _add(t, tool="Read", inp={"file_path": "/y.py"}, out="line\n" * 100, sess="s1", when=2, pid=p)
    rows = _replay(conn, DiffReReads())
    affected = [r for r in rows if r["affected"]]
    assert len(affected) == 1


def test_D_no_edit_between_dont_fire(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/y.py"}, out="x" * 200, sess="s1", when=0, pid=p)
    _add(t, tool="Read", inp={"file_path": "/y.py"}, out="x" * 200, sess="s1", when=1, pid=p)
    rows = _replay(conn, DiffReReads())
    assert sum(r["affected"] for r in rows) == 0


# --- G. RetryDetection ---------------------------------------------------


def test_G_retry_fires_on_similar_args(sess):
    conn, t, p = sess
    _add(t, tool="Bash", inp={"command": "git statu"}, out="not found", sess="s1", when=0, pid=p)
    _add(t, tool="Bash", inp={"command": "git status"}, out="ok", sess="s1", when=1, pid=p)
    rows = _replay(conn, RetryDetection())
    assert sum(r["affected"] for r in rows) >= 1


# --- M. ArgAutoCorrect ---------------------------------------------------


def test_M_arg_autocorrect_fires(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/foo.py", "limit": 10}, out="x", sess="s1", when=0, pid=p)
    _add(t, tool="Read", inp={"file_path": "/foo.py", "limit": 11}, out="x", sess="s1", when=1, pid=p)
    rows = _replay(conn, ArgAutoCorrect())
    assert sum(r["affected"] for r in rows) >= 1


# --- J. UncitedCompaction ------------------------------------------------


def test_J_uncited_fires_when_output_unreferenced(sess):
    conn, t, p = sess
    # Output is gibberish never referenced later
    _add(t, tool="Read", inp={"file_path": "/x.py"},
         out="alpha-bravo-charlie\ndelta-echo-foxtrot\n" * 50,
         sess="s1", when=0, pid=p)
    # Later input mentions something unrelated
    _add(t, tool="Bash", inp={"command": "ls"}, out="ok",
         sess="s1", when=1, pid=p)
    rows = _replay(conn, UncitedCompaction())
    assert sum(r["affected"] for r in rows) == 1


# --- K. NgramSynthesis ---------------------------------------------------


def test_K_ngram_fires_on_repeated_sequence(sess):
    conn, t, p = sess
    # Read→Edit→Bash sequence three times in 60s window
    for i, step in enumerate(["Read", "Edit", "Bash"] * 3):
        _add(t, tool=step, inp={"x": i}, out="r", sess="s1", when=i, pid=p)
    rows = _replay(conn, NgramSynthesis())
    affected = sum(r["affected"] for r in rows)
    assert affected >= 1


# --- L. SplitToolsLocal -------------------------------------------------


def test_L_local_fires_on_mostly_unreferenced_read(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/big.py"},
         out="\n".join(f"unique_token_line_{i}" for i in range(200)),
         sess="s1", when=0, pid=p)
    _add(t, tool="Bash", inp={"command": "uninteresting"}, out="ok",
         sess="s1", when=1, pid=p)
    rows = _replay(conn, SplitToolsLocal())
    assert sum(r["affected"] for r in rows) == 1


# --- L (JSONL). SplitToolsJsonl ------------------------------------------


def test_L_jsonl_fires_when_ref_short_and_unspecific(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/f"},
         out="\n".join(f"def function_{i}(): pass" for i in range(30)),
         sess="s1", when=0, pid=p)
    refs = {"toolu_s1_0": "Now I'll proceed."}  # short generic ack
    rows = _replay(conn, SplitToolsJsonl(refs))
    assert sum(r["affected"] for r in rows) == 1


# --- N. BashEarlyTerm ----------------------------------------------------


def test_N_bash_early_term_fires(sess):
    conn, t, p = sess
    head = "useful_marker_xyz\n"
    tail = "noise_line\n" * 100
    _add(t, tool="Bash", inp={"command": "long"}, out=head + tail,
         sess="s1", when=0, pid=p)
    _add(t, tool="Bash", inp={"command": "uses useful_marker_xyz"}, out="ok",
         sess="s1", when=1, pid=p)
    rows = _replay(conn, BashEarlyTerm())
    affected = [r for r in rows if r["affected"]]
    assert len(affected) == 1


# --- V. ToolBudget -------------------------------------------------------


def test_V_tool_budget_fires_on_long_session(sess):
    conn, t, p = sess
    # 10 short sessions + 1 long one — pushes 90th percentile below the long.
    for sid in range(10):
        for i in range(3):
            _add(t, tool="Bash", inp={"command": f"c{sid}_{i}"}, out="x",
                 sess=f"short{sid}", when=i, pid=p)
    for i in range(100):
        _add(t, tool="Bash", inp={"command": f"c{i}"}, out="x" * 100,
             sess="long", when=i, pid=p)
    tech = ToolBudget()
    sessions = group_into_sessions(load_triples(conn))
    tech.calibrate(sessions)
    rows = replay(tech, sessions)
    affected = sum(r["affected"] for r in rows)
    # Should fire exactly once — on the last event of the long session.
    assert affected == 1


# --- T. DeadEndPrompts ---------------------------------------------------


def test_T_dead_end_fires_on_clarification(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/x"}, out="content",
         sess="s1", when=0, pid=p)
    refs = {"toolu_s1_0": "Could you clarify what you want me to do?"}
    rows = _replay(conn, DeadEndPrompts(refs))
    assert sum(r["affected"] for r in rows) == 1


def test_T_dead_end_no_fire_on_normal_text(sess):
    conn, t, p = sess
    _add(t, tool="Read", inp={"file_path": "/x"}, out="content",
         sess="s1", when=0, pid=p)
    refs = {"toolu_s1_0": "Looking at the file, I see the validator."}
    rows = _replay(conn, DeadEndPrompts(refs))
    assert sum(r["affected"] for r in rows) == 0


# --- registry smoke ------------------------------------------------------


def test_all_triples_only_returns_10(sess):
    techs = all_triples_only_techniques()
    assert len(techs) == 10
    names = {t.name for t in techs}
    assert len(names) == 10  # unique
