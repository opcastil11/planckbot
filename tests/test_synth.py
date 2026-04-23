"""Tests for Layer D: pattern detector, synthesize_tool, and hot-reload wiring."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from planckbot.db.models import GapReport, SynthesizedTool, Triple
from planckbot.synth.detector import (
    GapReportStore,
    build_gap_reports,
    find_tool_sequences,
)
from planckbot.synth.meta import (
    SynthesizedToolStore,
    activate_tool,
    deactivate_tool,
    load_function_from_code,
    synthesize_tool,
)


# --- detector ---------------------------------------------------------------


def _mk_triple(name: str, ts: datetime, source: str = "proxy:observe") -> Triple:
    return Triple(
        tool_name=name,
        input_data="{}",
        output_data="ok",
        source=source,
        created_at=ts.isoformat(),
    )


def test_find_sequences_picks_up_repeated_bigram():
    t0 = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    triples = [
        _mk_triple("list_directory", t0),
        _mk_triple("read_file", t0 + timedelta(seconds=2)),
        _mk_triple("list_directory", t0 + timedelta(seconds=20)),
        _mk_triple("read_file", t0 + timedelta(seconds=22)),
    ]
    matches = find_tool_sequences(triples, ngram_min=2, ngram_max=2)
    bigrams = {m.sequence: m.occurrences for m in matches}
    assert ("list_directory", "read_file") in bigrams
    assert bigrams[("list_directory", "read_file")] == 2


def test_find_sequences_ignores_single_tool_ngram():
    """Calling the same tool many times isn't a "gap" — it's just usage."""
    t0 = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    triples = [
        _mk_triple("read_file", t0 + timedelta(seconds=i)) for i in range(6)
    ]
    matches = find_tool_sequences(triples, ngram_min=2, ngram_max=2)
    assert matches == []


def test_find_sequences_respects_window():
    t0 = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    triples = [
        _mk_triple("a", t0),
        _mk_triple("b", t0 + timedelta(seconds=120)),  # outside 30s window
        _mk_triple("a", t0 + timedelta(seconds=300)),
        _mk_triple("b", t0 + timedelta(seconds=420)),
    ]
    matches = find_tool_sequences(
        triples, ngram_min=2, ngram_max=2, window_seconds=30, min_occurrences=1,
    )
    assert all(m.sequence != ("a", "b") for m in matches)


def test_find_sequences_skips_manual_source():
    t0 = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    triples = [
        _mk_triple("a", t0, source="manual"),
        _mk_triple("b", t0 + timedelta(seconds=1), source="manual"),
        _mk_triple("a", t0 + timedelta(seconds=10), source="manual"),
        _mk_triple("b", t0 + timedelta(seconds=11), source="manual"),
    ]
    assert find_tool_sequences(triples) == []


def test_find_sequences_requires_min_occurrences():
    t0 = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    triples = [
        _mk_triple("a", t0),
        _mk_triple("b", t0 + timedelta(seconds=1)),
    ]
    assert find_tool_sequences(triples, min_occurrences=2) == []


# --- GapReportStore ---------------------------------------------------------


def test_gap_store_insert_and_list(conn):
    store = GapReportStore(conn)
    r = GapReport(
        tool_sequence=["a", "b"], occurrences=5,
        example_triple_ids=["x1", "x2"],
    )
    store.insert(r)
    assert store.count() == 1
    loaded = store.get(r.id)
    assert loaded.tool_sequence == ["a", "b"]
    assert loaded.example_triple_ids == ["x1", "x2"]
    assert [x.id for x in store.list_open()] == [r.id]


def test_build_gap_reports_dedups_by_sequence(conn):
    from planckbot.synth.detector import SequenceMatch
    matches = [
        SequenceMatch(sequence=("a", "b"), occurrences=3,
                      example_triple_ids=["t1"]),
    ]
    created, updated = build_gap_reports(conn, matches)
    assert (created, updated) == (1, 0)

    matches2 = [
        SequenceMatch(sequence=("a", "b"), occurrences=7,
                      example_triple_ids=["t9"]),
    ]
    created, updated = build_gap_reports(conn, matches2)
    assert (created, updated) == (0, 1)

    # Only ONE row for sequence (a,b), with the bumped count
    store = GapReportStore(conn)
    assert store.count() == 1
    r = store.list_all()[0]
    assert r.occurrences == 7
    assert r.example_triple_ids == ["t9"]


# --- SynthesizedToolStore ---------------------------------------------------


def test_synth_store_roundtrip(conn):
    store = SynthesizedToolStore(conn)
    t = SynthesizedTool(
        name="echo", description="echoes input",
        input_schema={"msg": "string"},
        code="def echo(msg): return msg",
    )
    store.insert(t)
    loaded = store.by_name("echo")
    assert loaded.input_schema == {"msg": "string"}
    assert loaded.code == "def echo(msg): return msg"


def test_synth_store_list_active(conn):
    store = SynthesizedToolStore(conn)
    store.insert(SynthesizedTool(
        name="a", code="def a(): pass", status="draft"
    ))
    store.insert(SynthesizedTool(
        name="b", code="def b(): pass", status="active"
    ))
    store.insert(SynthesizedTool(
        name="c", code="def c(): pass", status="retired"
    ))
    assert [x.name for x in store.list_active()] == ["b"]


# --- synthesize_tool / activate / deactivate --------------------------------


def test_synthesize_tool_writes_file_and_row(conn, tmp_path: Path):
    code = (
        "import json\n\n"
        "def combo(path):\n"
        "    return json.dumps({'path': path})\n"
    )
    result = synthesize_tool(
        name="combo",
        description="test combo",
        input_schema={"path": "string"},
        code=code,
        conn=conn,
        synth_dir=tmp_path,
    )
    assert (tmp_path / "combo.py").exists()
    store = SynthesizedToolStore(conn)
    loaded = store.by_name("combo")
    assert loaded.status == "draft"
    assert loaded.source_file_path == str(tmp_path / "combo.py")


def test_synthesize_tool_rejects_bad_name(conn, tmp_path: Path):
    with pytest.raises(ValueError, match="must be"):
        synthesize_tool(
            name="bad name!",
            description="",
            input_schema={},
            code="def f(): pass",
            conn=conn,
            synth_dir=tmp_path,
        )


def test_synthesize_tool_rejects_missing_function(conn, tmp_path: Path):
    with pytest.raises(ValueError, match="top-level function"):
        synthesize_tool(
            name="echo",
            description="",
            input_schema={},
            code="def something_else(): pass",
            conn=conn,
            synth_dir=tmp_path,
        )


def test_synthesize_tool_rejects_forbidden_imports(conn, tmp_path: Path):
    with pytest.raises(ValueError):
        synthesize_tool(
            name="evil",
            description="",
            input_schema={},
            code="import subprocess\ndef evil(): pass",
            conn=conn,
            synth_dir=tmp_path,
        )


def test_synthesize_tool_rejects_duplicate_name(conn, tmp_path: Path):
    code = "def dup(): pass"
    synthesize_tool(
        name="dup", description="", input_schema={}, code=code,
        conn=conn, synth_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="already exists"):
        synthesize_tool(
            name="dup", description="", input_schema={}, code=code,
            conn=conn, synth_dir=tmp_path,
        )


def test_activate_and_deactivate_tool(conn, tmp_path: Path):
    synthesize_tool(
        name="flip",
        description="",
        input_schema={},
        code="def flip(): return 1",
        conn=conn,
        synth_dir=tmp_path,
    )
    activate_tool("flip", conn=conn, hot_reload=False)
    store = SynthesizedToolStore(conn)
    assert store.by_name("flip").status == "active"

    deactivate_tool("flip", conn=conn, hot_reload=False)
    assert store.by_name("flip").status == "retired"


def test_activate_unknown_tool_raises(conn):
    with pytest.raises(ValueError, match="no synthesized tool"):
        activate_tool("nope", conn=conn, hot_reload=False)


# --- load_function_from_code ------------------------------------------------


def test_load_function_executes_safely():
    fn = load_function_from_code(
        "def ident(x):\n    return x + 1\n", "ident"
    )
    assert fn(41) == 42


def test_load_function_missing_symbol():
    with pytest.raises(ValueError, match="callable named"):
        load_function_from_code("x = 1\n", "ident")


# --- cron integration: detect_tool_gaps job ---------------------------------


def test_detect_tool_gaps_job_populates_reports(conn):
    from planckbot.cron.jobs import JobContext, default_registry
    from planckbot.tools.triples import TriplesStore

    store = TriplesStore(conn)
    t0 = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    for i in range(3):
        a = store.add(tool_name="alpha", input_data="{}",
                      output_data="a", source="proxy:observe")
        b = store.add(tool_name="beta", input_data="{}",
                      output_data="b", source="proxy:observe")
        # Force chronological order by rewriting created_at on each
        ts_a = (t0 + timedelta(seconds=i * 60)).isoformat()
        ts_b = (t0 + timedelta(seconds=i * 60 + 5)).isoformat()
        conn.execute("UPDATE triples SET created_at = ? WHERE id = ?",
                     (ts_a, a.id))
        conn.execute("UPDATE triples SET created_at = ? WHERE id = ?",
                     (ts_b, b.id))
    conn.commit()

    reg = default_registry()
    fn = reg.get("detect_tool_gaps")
    assert fn is not None
    out = fn(JobContext(conn=conn, params={"min_occurrences": 2}))
    assert "matches=" in out
    assert "alpha" in out and "beta" in out
    assert GapReportStore(conn).count() >= 1


# --- MCP server tool advertisement ------------------------------------------


def test_tool_from_row_wraps_schema_correctly():
    from planckbot.synth.mcp_server import _tool_from_row
    t = SynthesizedTool(
        name="echo",
        description="echoes",
        input_schema={"msg": "string"},
        code="def echo(msg): return msg",
    )
    mcp_tool = _tool_from_row(t)
    assert mcp_tool.name == "echo"
    assert mcp_tool.inputSchema["type"] == "object"
    assert "msg" in mcp_tool.inputSchema["properties"]
