"""Tests for the multi-adapter LRU registry."""

from __future__ import annotations

from planckbot.models.registry import AdapterRegistry


class _FakeModel:
    """Stand-in — the registry just stores and returns opaque objects."""
    def __init__(self, name: str):
        self.name = name


def test_registry_holds_and_returns_model():
    r = AdapterRegistry(max_active=4)
    m = _FakeModel("a")
    r.put("tool_a", "ckpt-1", m)
    assert r.get("tool_a") is m
    assert r.has("tool_a")
    assert r.size() == 1


def test_registry_evicts_lru_at_capacity():
    r = AdapterRegistry(max_active=2)
    r.put("a", "ck-a", _FakeModel("a"))
    r.put("b", "ck-b", _FakeModel("b"))
    # Touch 'a' so 'b' becomes LRU
    _ = r.get("a")
    r.put("c", "ck-c", _FakeModel("c"))
    assert r.has("a")
    assert r.has("c")
    assert not r.has("b")
    assert r.size() == 2


def test_registry_update_existing_does_not_evict():
    r = AdapterRegistry(max_active=2)
    r.put("a", "ck-a", _FakeModel("a1"))
    r.put("b", "ck-b", _FakeModel("b1"))
    r.put("a", "ck-a", _FakeModel("a2"))  # update, no eviction
    assert r.has("a") and r.has("b")


def test_registry_evict_returns_true_when_present():
    r = AdapterRegistry(max_active=2)
    r.put("a", "ck-a", _FakeModel("a"))
    assert r.evict("a") is True
    assert r.evict("a") is False  # already gone


def test_registry_get_missing_returns_none():
    r = AdapterRegistry(max_active=2)
    assert r.get("nope") is None


def test_registry_clear_resets():
    r = AdapterRegistry(max_active=5)
    for name in ("a", "b", "c"):
        r.put(name, f"ck-{name}", _FakeModel(name))
    r.clear()
    assert r.size() == 0
    assert r.get("a") is None


def test_registry_entries_in_lru_order():
    r = AdapterRegistry(max_active=5)
    r.put("a", "1", _FakeModel("a"))
    r.put("b", "2", _FakeModel("b"))
    r.put("c", "3", _FakeModel("c"))
    # Touch a — now LRU order is b, c, a
    _ = r.get("a")
    names = [e.tool_name for e in r.entries()]
    assert names == ["b", "c", "a"]


def test_registry_max_active_floor_is_one():
    """Passing 0 or negative capacity collapses to 1, not blow-up."""
    r = AdapterRegistry(max_active=0)
    r.put("a", "1", _FakeModel("a"))
    r.put("b", "2", _FakeModel("b"))
    # Capacity clamped to 1 → only the most recent remains
    assert r.size() == 1
    assert r.has("b")
    assert not r.has("a")


# --- authoring ------------------------------------------------------------


def test_authoring_falls_back_to_template_without_api_key(monkeypatch):
    from planckbot.db.models import GapReport
    from planckbot.synth.authoring import author_tool_for_gap

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gap = GapReport(
        tool_sequence=["list_directory", "read_file"],
        occurrences=4,
        example_triple_ids=[],  # no examples → deterministic template path
        proposed_name="list_and_read",
        proposed_description="Merged listing + read",
    )
    # conn is unused when examples is empty and we hit template path
    authored = author_tool_for_gap(gap, conn=None, use_api=False)

    assert authored.source == "template"
    assert "def list_and_read" in authored.code
    assert "import subprocess" not in authored.code
    # AST-gated by synthesize_tool downstream; here we just check it's
    # parseable.
    import ast
    ast.parse(authored.code)


def test_authoring_respects_name_override(monkeypatch):
    from planckbot.db.models import GapReport
    from planckbot.synth.authoring import author_tool_for_gap

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gap = GapReport(tool_sequence=["a", "b"], example_triple_ids=[])
    authored = author_tool_for_gap(
        gap, conn=None, name="my_custom_name", use_api=False,
    )
    assert authored.name == "my_custom_name"
    assert "def my_custom_name" in authored.code
