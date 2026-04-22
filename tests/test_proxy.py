"""Tests for the runtime proxy layer."""

import json

import pytest

from planckbot.proxy import (
    InterveneMode,
    ObserveMode,
    PlanckProxy,
    SuggestMode,
    planck_tool,
)


# -- Fixtures --------------------------------------------------------------

@pytest.fixture
def fake_search_tool():
    """A toy tool whose output contains both kept-and-dropped lines."""
    def _tool(query: str):
        return (
            f"src/auth/{query}.py\n"
            f"src/tests/test_{query}.py\n"
            f"node_modules/@foo/{query}/index.js\n"
            f"dist/{query}.min.js"
        )
    return _tool


def _make_predictor(predicted: str, confidence: float):
    """Build a stub predictor that always returns a fixed (text, confidence)."""
    calls = []
    def _predict(tool_name, input_str, output_str, strategy):
        calls.append((tool_name, input_str, output_str, strategy))
        return predicted, confidence
    _predict.calls = calls  # type: ignore[attr-defined]
    return _predict


# -- Observe mode ----------------------------------------------------------

def test_observe_returns_raw_and_does_not_call_predictor(triples_store, fake_search_tool):
    predictor = _make_predictor("should not be used", confidence=0.99)
    proxy = PlanckProxy(triples_store, mode=ObserveMode(), predictor=predictor)
    wrapped = proxy.wrap(fake_search_tool, tool_name="file_search")

    result = wrapped(query="auth")

    assert result.intervened is False
    assert result.final_output == result.raw_output
    assert result.predicted_output is None
    assert predictor.calls == []
    # Triple recorded
    assert triples_store.count_total() == 1


def test_observe_records_triple_with_source(triples_store, fake_search_tool):
    proxy = PlanckProxy(triples_store, mode=ObserveMode())
    wrapped = proxy.wrap(fake_search_tool, tool_name="file_search")
    wrapped(query="auth")

    t = triples_store.list_all()[0]
    assert t.source == "proxy:observe"
    assert t.tool_name == "file_search"


# -- Suggest mode ----------------------------------------------------------

def test_suggest_returns_raw_but_records_prediction(triples_store, fake_search_tool):
    predicted = "src/auth/auth.py\nsrc/tests/test_auth.py"
    predictor = _make_predictor(predicted, confidence=0.95)
    proxy = PlanckProxy(triples_store, mode=SuggestMode(), predictor=predictor)
    wrapped = proxy.wrap(fake_search_tool, tool_name="file_search")

    result = wrapped(query="auth")

    assert result.intervened is False
    assert result.final_output == result.raw_output      # raw still returned
    assert result.predicted_output == predicted          # but prediction captured
    assert result.confidence == 0.95
    assert len(predictor.calls) == 1

    # The stored triple's filtered_output should be the prediction
    t = triples_store.list_all()[0]
    assert t.filtered_output == predicted


def test_suggest_with_no_predictor_is_noop(triples_store, fake_search_tool):
    proxy = PlanckProxy(triples_store, mode=SuggestMode(), predictor=None)
    wrapped = proxy.wrap(fake_search_tool, tool_name="file_search")
    result = wrapped(query="auth")
    assert result.intervened is False
    assert result.predicted_output is None


# -- Intervene mode --------------------------------------------------------

def test_intervene_replaces_output_when_confident(triples_store, fake_search_tool):
    predicted = "src/auth/auth.py"
    predictor = _make_predictor(predicted, confidence=0.95)
    proxy = PlanckProxy(
        triples_store,
        mode=InterveneMode(confidence_threshold=0.90),
        predictor=predictor,
    )
    wrapped = proxy.wrap(fake_search_tool, tool_name="file_search")

    result = wrapped(query="auth")

    assert result.intervened is True
    assert result.final_output == predicted
    assert result.final_output != result.raw_output


def test_intervene_passes_through_below_threshold(triples_store, fake_search_tool):
    predictor = _make_predictor("nope", confidence=0.50)
    proxy = PlanckProxy(
        triples_store,
        mode=InterveneMode(confidence_threshold=0.90),
        predictor=predictor,
    )
    wrapped = proxy.wrap(fake_search_tool, tool_name="file_search")

    result = wrapped(query="auth")

    assert result.intervened is False
    assert result.final_output == result.raw_output
    assert result.confidence == 0.50
    assert result.predicted_output == "nope"


def test_intervene_never_modifies_side_effect_tools(triples_store):
    def _bash(command: str):
        return "executed: " + command

    predictor = _make_predictor("rm -rf /", confidence=0.99)
    proxy = PlanckProxy(
        triples_store,
        mode=InterveneMode(confidence_threshold=0.5),
        predictor=predictor,
    )
    wrapped = proxy.wrap(_bash, tool_name="Bash")

    result = wrapped(command="ls /tmp")

    # Confidence is high + threshold is low, but Bash is in SIDE_EFFECT_TOOLS.
    assert result.intervened is False
    assert result.final_output == "executed: ls /tmp"
    # The predictor still ran so we have training data
    assert result.confidence == 0.99


def test_intervene_logs_triple_with_prediction(triples_store, fake_search_tool):
    predicted = "src/auth/auth.py"
    predictor = _make_predictor(predicted, confidence=0.97)
    proxy = PlanckProxy(
        triples_store,
        mode=InterveneMode(confidence_threshold=0.90),
        predictor=predictor,
    )
    wrapped = proxy.wrap(fake_search_tool, tool_name="file_search")
    wrapped(query="auth")

    t = triples_store.list_all()[0]
    assert t.source == "proxy:intervene"
    assert t.filtered_output == predicted


# -- Decorator form --------------------------------------------------------

def test_planck_tool_decorator(triples_store, fake_search_tool):
    predictor = _make_predictor("predicted", confidence=0.95)
    proxy = PlanckProxy(triples_store, mode=InterveneMode(), predictor=predictor)

    @planck_tool(tool_name="file_search", proxy=proxy)
    def search(query: str):
        return fake_search_tool(query)

    result = search(query="auth")
    assert result.intervened is True
    assert result.final_output == "predicted"
    assert search.__planck_tool_name__ == "file_search"


# -- Positional vs. keyword args ------------------------------------------

def test_proxy_records_positional_arg(triples_store, fake_search_tool):
    proxy = PlanckProxy(triples_store, mode=ObserveMode())
    wrapped = proxy.wrap(fake_search_tool, tool_name="file_search")
    result = wrapped("auth")  # positional
    # input_data stored as the single arg
    assert result.input_data == "auth"
    t = triples_store.list_all()[0]
    assert t.input_data == "auth"


def test_proxy_records_multi_kwargs(triples_store):
    def _tool(a, b):
        return f"{a}-{b}"
    proxy = PlanckProxy(triples_store, mode=ObserveMode())
    wrapped = proxy.wrap(_tool, tool_name="my_tool")
    result = wrapped(a="x", b="y")
    assert result.final_output == "x-y"
    t = triples_store.list_all()[0]
    assert json.loads(t.input_data) == {"a": "x", "b": "y"}


# -- Latency captured ------------------------------------------------------

def test_proxy_captures_latency(triples_store):
    import time
    def _slow():
        time.sleep(0.05)
        return "done"

    proxy = PlanckProxy(triples_store, mode=ObserveMode())
    wrapped = proxy.wrap(_slow, tool_name="slow")
    result = wrapped()
    assert result.latency_ms >= 40  # ~50ms ± scheduling slop
