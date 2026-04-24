"""Tests for Layer B's reference-tracking signal."""

import pytest

from planckbot.ingest.reference_tracker import (
    extract_referenced_lines,
    label_triple_from_reference,
)


# --- extract_referenced_lines -----------------------------------------------


def test_extract_keeps_matching_lines():
    output = "[DIR] src\n[DIR] node_modules\n[FILE] README.md"
    reference = "I looked at [DIR] src and then [FILE] README.md — the rest was noise."
    kept = extract_referenced_lines(output, reference)
    assert kept == "[DIR] src\n[FILE] README.md"


def test_extract_returns_none_when_nothing_matches():
    output = "[DIR] a\n[DIR] b"
    reference = "unrelated text that doesn't mention anything"
    assert extract_referenced_lines(output, reference) is None


def test_extract_skips_short_lines():
    # Even though `{` appears in the reference, it's below min_line_len
    output = "{\nreal_line_here\n}"
    reference = "real_line_here and some braces { }"
    kept = extract_referenced_lines(output, reference, min_line_len=4)
    assert kept == "real_line_here"


def test_extract_handles_empty_inputs():
    assert extract_referenced_lines("", "reference") is None
    assert extract_referenced_lines("line", "") is None


def test_extract_preserves_line_ordering():
    output = "z-line\na-line\nm-line"
    reference = "m-line appears first, then a-line, then z-line"
    kept = extract_referenced_lines(output, reference)
    # Must preserve ORIGINAL output order, not reference order
    assert kept == "z-line\na-line\nm-line"


def test_extract_preserves_internal_whitespace():
    output = "    [FILE]   spaced.py\n    [DIR] other"
    reference = "[FILE]   spaced.py was cited"
    kept = extract_referenced_lines(output, reference)
    assert kept == "    [FILE]   spaced.py"


# --- label_triple_from_reference -------------------------------------------


def test_label_triple_writes_filtered_output(triples_store):
    t = triples_store.add(
        tool_name="list_directory",
        input_data="i",
        output_data="[DIR] src\n[DIR] node_modules\n[FILE] README.md",
    )
    kept = label_triple_from_reference(
        triples_store, t.id,
        "We kept [DIR] src and [FILE] README.md",
    )
    assert kept == "[DIR] src\n[FILE] README.md"

    reloaded = triples_store.get(t.id)
    assert reloaded.filtered_output == "[DIR] src\n[FILE] README.md"
    assert reloaded.filtered_tokens is not None


def test_label_triple_noop_when_already_labeled(triples_store):
    t = triples_store.add(
        tool_name="t", input_data="i", output_data="a\nb\nc",
        filtered_output="manual-label",
    )
    # Reference would match "a" but we should NOT overwrite without force
    kept = label_triple_from_reference(triples_store, t.id, "a is referenced")
    assert kept == "manual-label"
    assert triples_store.get(t.id).filtered_output == "manual-label"


def test_label_triple_force_overwrites(triples_store):
    t = triples_store.add(
        tool_name="t", input_data="i", output_data="alpha\nbeta",
        filtered_output="stale",
    )
    kept = label_triple_from_reference(
        triples_store, t.id, "alpha was useful", force=True
    )
    assert kept == "alpha"
    assert triples_store.get(t.id).filtered_output == "alpha"


def test_label_triple_raises_on_missing_id(triples_store):
    with pytest.raises(ValueError, match="not found"):
        label_triple_from_reference(triples_store, "no-such-id", "ref")


def test_label_triple_returns_none_when_no_match(triples_store):
    t = triples_store.add(
        tool_name="t", input_data="i", output_data="only\nthese\nlines",
    )
    assert label_triple_from_reference(
        triples_store, t.id, "completely unrelated reference"
    ) is None
    # filtered_output must stay NULL so future runs can still label it
    assert triples_store.get(t.id).filtered_output is None


# --- TriplesStore.list_unlabeled -------------------------------------------


def test_list_unlabeled_excludes_labeled(triples_store):
    triples_store.add(tool_name="t", input_data="i", output_data="o1",
                      filtered_output="already")
    t2 = triples_store.add(tool_name="t", input_data="i", output_data="o2")
    rows = triples_store.list_unlabeled(tool_name="t")
    assert [r.id for r in rows] == [t2.id]


def test_list_unlabeled_orders_by_most_recent(triples_store):
    first = triples_store.add(tool_name="t", input_data="i", output_data="first")
    second = triples_store.add(tool_name="t", input_data="i", output_data="second")
    rows = triples_store.list_unlabeled(tool_name="t")
    assert rows[0].id == second.id
    assert rows[1].id == first.id


def test_list_unlabeled_respects_tool_filter(triples_store):
    ta = triples_store.add(tool_name="a", input_data="i", output_data="o")
    tb = triples_store.add(tool_name="b", input_data="i", output_data="o")
    rows = triples_store.list_unlabeled(tool_name="a")
    assert [r.id for r in rows] == [ta.id]


# --- match_mode: semantic -------------------------------------------------


def test_semantic_mode_falls_back_when_dep_missing(monkeypatch):
    """If sentence-transformers isn't installed, semantic mode must
    degrade gracefully to token matching — never crash."""
    # Simulate missing dep by making the import raise
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("faked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    out = extract_referenced_lines(
        "keep_this\ndrop_that", "keep_this is useful",
        match_mode="semantic",
    )
    # Should have matched via the token fallback
    assert out == "keep_this"


def test_unknown_match_mode_raises():
    import pytest
    with pytest.raises(ValueError, match="unknown match_mode"):
        extract_referenced_lines("a\nb", "c", match_mode="not-a-real-mode")
