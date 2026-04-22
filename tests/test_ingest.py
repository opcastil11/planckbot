"""Tests for the ingest layer (triple sources)."""

import json

from planckbot.ingest import ManualSource, OrquestaSource


# -- ManualSource ----------------------------------------------------------

def test_manual_empty_file(tmp_path, triples_store):
    path = tmp_path / "empty.json"
    path.write_text("[]")
    src = ManualSource(path)
    count = src.ingest(triples_store)
    assert count == 0
    assert triples_store.count_total() == 0


def test_manual_roundtrip(tmp_path, triples_store):
    path = tmp_path / "triples.json"
    path.write_text(json.dumps([
        {
            "tool_name": "file_search",
            "input_data": {"query": "auth", "path": "src/"},
            "output_data": {"results": ["a.py", "b.py"]},
        },
        {
            "tool_name": "read_file",
            "input_data": {"path": "README.md"},
            "output_data": "file contents here",
            "session_id": "prompt_123",
        },
    ]))
    src = ManualSource(path)
    count = src.ingest(triples_store)
    assert count == 2

    stored = triples_store.list_all()
    assert {t.tool_name for t in stored} == {"file_search", "read_file"}
    read_triple = next(t for t in stored if t.tool_name == "read_file")
    assert read_triple.session_id == "prompt_123"
    assert read_triple.source == "manual"


def test_manual_accepts_short_keys(tmp_path, triples_store):
    """Accepts both 'input'/'output' and 'input_data'/'output_data'."""
    path = tmp_path / "short.json"
    path.write_text(json.dumps([
        {"tool_name": "t", "input": "a", "output": "b"},
    ]))
    assert ManualSource(path).ingest(triples_store) == 1


def test_manual_rejects_missing_tool_name(tmp_path, triples_store):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"input": "a", "output": "b"}]))
    src = ManualSource(path)
    try:
        src.ingest(triples_store)
        assert False, "should have raised"
    except ValueError:
        pass


def test_manual_missing_file_is_empty(tmp_path, triples_store):
    src = ManualSource(tmp_path / "does-not-exist.json")
    assert src.ingest(triples_store) == 0


# -- OrquestaSource --------------------------------------------------------

def _fake_row(category: str, use_id: str, prompt_id: str, extra: dict) -> dict:
    key = "tool_call" if category == "tool_call" else "tool_result"
    details = {key: {"tool_use_id": use_id, **extra}}
    return {
        "id": f"log_{use_id}_{category}",
        "prompt_id": prompt_id,
        "sequence": 1,
        "timestamp": "2026-04-22T12:00:00Z",
        "details": details,
    }


def _make_fake_http(responses: dict[str, list]):
    """Build an http_get that returns stored responses keyed by URL substring."""
    def _get(url: str, headers: dict):
        for key, body in responses.items():
            if key in url:
                return body
        return []
    return _get


def test_orquesta_pairs_call_with_result(triples_store):
    calls = [
        _fake_row("tool_call", "use_1", "prompt_A",
                  {"name": "Read", "parameters": {"file_path": "/tmp/x"}}),
        _fake_row("tool_call", "use_2", "prompt_A",
                  {"name": "Bash", "parameters": {"command": "ls"}}),
    ]
    results = [
        _fake_row("tool_result", "use_1", "prompt_A",
                  {"output": "hello world", "success": True}),
        _fake_row("tool_result", "use_2", "prompt_A",
                  {"output": "a.py b.py", "success": True}),
    ]
    http = _make_fake_http({
        "category=eq.tool_call": calls,
        "category=eq.tool_result": results,
    })
    src = OrquestaSource("https://fake.supabase", "svc_key", http_get=http)
    count = src.ingest(triples_store)
    assert count == 2

    stored = triples_store.list_all()
    names = {t.tool_name for t in stored}
    assert names == {"Read", "Bash"}
    for t in stored:
        assert t.source == "orquesta"
        assert t.session_id == "prompt_A"


def test_orquesta_skips_unmatched_calls(triples_store):
    calls = [_fake_row("tool_call", "use_1", "p1",
                       {"name": "Read", "parameters": {}})]
    results = []  # no matching result
    http = _make_fake_http({
        "category=eq.tool_call": calls,
        "category=eq.tool_result": results,
    })
    src = OrquestaSource("https://fake", "k", http_get=http)
    assert src.ingest(triples_store) == 0


def test_orquesta_skips_error_results(triples_store):
    calls = [_fake_row("tool_call", "use_e", "p",
                       {"name": "Bash", "parameters": {"command": "oops"}})]
    results = [_fake_row("tool_result", "use_e", "p",
                         {"error": "command not found", "success": False})]
    http = _make_fake_http({
        "category=eq.tool_call": calls,
        "category=eq.tool_result": results,
    })
    src = OrquestaSource("https://fake", "k", http_get=http)
    assert src.ingest(triples_store) == 0


def test_orquesta_respects_limit(triples_store):
    calls = [
        _fake_row("tool_call", f"use_{i}", "p",
                  {"name": "Read", "parameters": {"file_path": f"/f{i}"}})
        for i in range(10)
    ]
    results = [
        _fake_row("tool_result", f"use_{i}", "p",
                  {"output": f"contents {i}", "success": True})
        for i in range(10)
    ]
    http = _make_fake_http({
        "category=eq.tool_call": calls,
        "category=eq.tool_result": results,
    })
    src = OrquestaSource("https://fake", "k", http_get=http)
    assert src.ingest(triples_store, limit=3) == 3


def test_orquesta_filters_by_project(triples_store):
    calls = [
        _fake_row("tool_call", "u1", "prompt_in_project",
                  {"name": "X", "parameters": {}}),
        _fake_row("tool_call", "u2", "prompt_other",
                  {"name": "Y", "parameters": {}}),
    ]
    results = [
        _fake_row("tool_result", "u1", "prompt_in_project",
                  {"output": "in", "success": True}),
        _fake_row("tool_result", "u2", "prompt_other",
                  {"output": "out", "success": True}),
    ]
    prompts_for_project = [{"id": "prompt_in_project"}]
    http = _make_fake_http({
        "prompts?": prompts_for_project,
        "category=eq.tool_call": calls,
        "category=eq.tool_result": results,
    })
    src = OrquestaSource(
        "https://fake", "k", project_id="proj_abc", http_get=http,
    )
    assert src.ingest(triples_store) == 1
    assert triples_store.list_all()[0].tool_name == "X"
