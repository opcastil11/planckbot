"""Tests for tool registry, triples store, and builtin tools."""

import json

from planckbot.tools.registry import ToolRegistry
from planckbot.tools.builtin import register_builtins


# --- ToolRegistry tests ---

def test_register_and_get():
    reg = ToolRegistry()
    reg.register("echo", fn=lambda text="": text, description="Echo tool")
    tool = reg.get("echo")
    assert tool is not None
    assert tool.name == "echo"


def test_list_tools():
    reg = ToolRegistry()
    reg.register("a", fn=lambda: None)
    reg.register("b", fn=lambda: None)
    assert len(reg.list_tools()) == 2


def test_execute():
    reg = ToolRegistry()
    reg.register("add", fn=lambda a=0, b=0: a + b)
    result = reg.execute("add", a=2, b=3)
    assert result == 5


def test_execute_unknown_tool():
    reg = ToolRegistry()
    try:
        reg.execute("nonexistent")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_names():
    reg = ToolRegistry()
    reg.register("x", fn=lambda: None)
    assert "x" in reg.names()


# --- Builtin tools ---

def test_builtins_registered(registry):
    names = registry.names()
    assert "file_search" in names
    assert "read_file" in names
    assert "dummy_api" in names


def test_file_search_returns_json(registry):
    result = registry.execute("file_search", query="pyproject", path=".", glob="*.toml")
    data = json.loads(result)
    assert "results" in data
    assert data["total_results"] >= 0


def test_read_file_existing(registry):
    result = registry.execute("read_file", path="pyproject.toml")
    data = json.loads(result)
    assert "content" in data
    assert "planckbot" in data["content"]


def test_read_file_nonexistent(registry):
    result = registry.execute("read_file", path="/nonexistent/file.txt")
    data = json.loads(result)
    assert "error" in data


def test_dummy_api_users(registry):
    result = registry.execute("dummy_api", endpoint="users")
    data = json.loads(result)
    assert data["status"] == "success"
    assert len(data["data"]) == 20


def test_dummy_api_metrics(registry):
    result = registry.execute("dummy_api", endpoint="metrics")
    data = json.loads(result)
    assert "cpu_usage" in data["data"]


# --- TriplesStore tests ---

def test_add_triple(triples_store):
    t = triples_store.add(
        tool_name="file_search",
        input_data={"query": "test"},
        output_data={"results": []},
    )
    assert t.tool_name == "file_search"
    assert t.input_tokens is not None


def test_get_triple(triples_store):
    t = triples_store.add(tool_name="test", input_data="in", output_data="out")
    loaded = triples_store.get(t.id)
    assert loaded is not None
    assert loaded.tool_name == "test"


def test_get_by_tool(triples_store):
    triples_store.add(tool_name="a", input_data="1", output_data="2")
    triples_store.add(tool_name="a", input_data="3", output_data="4")
    triples_store.add(tool_name="b", input_data="5", output_data="6")
    assert len(triples_store.get_by_tool("a")) == 2
    assert len(triples_store.get_by_tool("b")) == 1


def test_count_by_tool(triples_store):
    triples_store.add(tool_name="x", input_data="1", output_data="2")
    triples_store.add(tool_name="x", input_data="3", output_data="4")
    counts = triples_store.count_by_tool()
    assert counts["x"] == 2


def test_count_total(triples_store):
    assert triples_store.count_total() == 0
    triples_store.add(tool_name="t", input_data="i", output_data="o")
    assert triples_store.count_total() == 1


def test_update_filtered(triples_store):
    t = triples_store.add(tool_name="t", input_data="i", output_data="o" * 100)
    triples_store.update_filtered(t.id, "short")
    loaded = triples_store.get(t.id)
    assert loaded.filtered_output == "short"
    assert loaded.filtered_tokens is not None


def test_delete_triple(triples_store):
    t = triples_store.add(tool_name="t", input_data="i", output_data="o")
    triples_store.delete(t.id)
    assert triples_store.get(t.id) is None


def test_list_all_triples(triples_store):
    triples_store.add(tool_name="t", input_data="1", output_data="2")
    triples_store.add(tool_name="t", input_data="3", output_data="4")
    assert len(triples_store.list_all()) == 2


def test_token_savings_empty(triples_store):
    savings = triples_store.token_savings("proxy:intervene")
    assert savings == {"raw_tokens": 0, "filtered_tokens": 0, "saved": 0, "intervene_count": 0}


def test_token_savings_ignores_unfiltered(triples_store):
    # Intervene triple without filtered_output is excluded
    triples_store.add(
        tool_name="t",
        input_data="i",
        output_data="x" * 100,
        source="proxy:intervene",
    )
    savings = triples_store.token_savings("proxy:intervene")
    assert savings["intervene_count"] == 0


def test_token_savings_positive_and_negative(triples_store):
    # Compressing triple: output 100 chars → filtered 20 chars (saves tokens)
    t1 = triples_store.add(
        tool_name="t",
        input_data="i",
        output_data="a" * 400,
        source="proxy:intervene",
    )
    triples_store.update_filtered(t1.id, "a" * 40)

    # Regressing triple: filtered is LONGER than raw (costs tokens)
    t2 = triples_store.add(
        tool_name="t",
        input_data="i",
        output_data="b" * 40,
        source="proxy:intervene",
    )
    triples_store.update_filtered(t2.id, "b" * 400)

    # observe triples must be excluded
    triples_store.add(
        tool_name="t",
        input_data="i",
        output_data="c" * 200,
        source="proxy:observe",
        filtered_output="c",
    )

    savings = triples_store.token_savings("proxy:intervene")
    assert savings["intervene_count"] == 2
    # With the ~4-chars-per-token heuristic, t1 saves 90 tokens, t2 loses 90.
    # Net should be ~0. The exact numbers depend on the heuristic; just check the
    # sign/magnitude invariants.
    assert savings["raw_tokens"] > 0
    assert savings["filtered_tokens"] > 0
    # t1.raw - t1.filt  is positive, t2.raw - t2.filt is negative → they cancel
    assert abs(savings["saved"]) <= 5
