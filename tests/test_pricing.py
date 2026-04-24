"""Tests for the cost-estimation helpers."""

from __future__ import annotations

import pytest

from planckbot.pricing import (
    DEFAULT_MODEL,
    MODELS,
    estimate_cost,
    get_model,
    list_models,
)


def test_default_model_exists():
    assert DEFAULT_MODEL in MODELS


def test_list_models_sorted_descending():
    ms = list_models()
    rates = [m["per_million_input_usd"] for m in ms]
    assert rates == sorted(rates, reverse=True)


def test_get_model_known_id():
    m = get_model("claude-sonnet-4-6")
    assert m["id"] == "claude-sonnet-4-6"
    assert m["per_million_input_usd"] == 3.00


def test_get_model_unknown_falls_back():
    m = get_model("totally-made-up-model")
    assert m["id"] == DEFAULT_MODEL


def test_get_model_env_override(monkeypatch):
    monkeypatch.setenv("PLANCKBOT_PRICING_MODEL", "claude-haiku-4-5")
    m = get_model()
    assert m["id"] == "claude-haiku-4-5"


def test_estimate_cost_positive_savings():
    q = estimate_cost(1_000_000, "claude-opus-4-7")
    # 1M tokens at $15/M = $15 saved
    assert q.cost_usd == pytest.approx(15.0)
    assert q.tokens == 1_000_000
    assert "USD" in q.cost_str


def test_estimate_cost_negative_savings():
    q = estimate_cost(-500_000, "claude-opus-4-7")
    # negative tokens → negative cost
    assert q.cost_usd < 0
    assert q.cost_str.startswith("-")


def test_cost_str_tiny_amount_shown_in_millicents():
    q = estimate_cost(100, "claude-haiku-4-5")
    # 100 tokens × $1/M = $0.0001 → 0.010¢
    assert "¢" in q.cost_str


def test_cost_str_cents_range():
    q = estimate_cost(100_000, "claude-haiku-4-5")
    # 100k × $1/M = $0.10 → 10.0¢
    assert "¢" in q.cost_str


def test_cost_str_dollar_range():
    q = estimate_cost(10_000_000, "claude-opus-4-7")
    assert "USD" in q.cost_str


def test_cost_str_large_dollar_range():
    # 1B tokens at $15/M = $15 000
    q = estimate_cost(1_000_000_000, "claude-opus-4-7")
    assert "15,000" in q.cost_str or "15000" in q.cost_str


def test_every_model_has_required_fields():
    for mid, meta in MODELS.items():
        assert "label" in meta
        assert "per_million_input_usd" in meta
        assert "description" in meta
        assert isinstance(meta["per_million_input_usd"], (int, float))
        assert meta["per_million_input_usd"] > 0
