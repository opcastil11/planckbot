"""Cost estimation for PlanckBot token savings.

Token prices are a moving target — vendors change them every few months.
We store a small curated table of recent per-million-token prices for the
major frontier models (input side only, because PlanckBot savings are
on the LLM's INPUT context via intercepted tool output). Users can
override with env vars or pass explicit rates.

All prices are in USD per million input tokens. Kept conservative —
when in doubt, use the published rate at time of writing; readers can
refresh via their vendor's current pricing page.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# Per-million INPUT token prices, USD, as of 2026-04. Refresh as needed.
# The `description` is what appears in the UI dropdown.
MODELS: dict[str, dict] = {
    "claude-opus-4-7": {
        "label": "Claude Opus 4.7",
        "per_million_input_usd": 15.00,
        "description": "Anthropic flagship; default for agentic coding work.",
    },
    "claude-sonnet-4-6": {
        "label": "Claude Sonnet 4.6",
        "per_million_input_usd": 3.00,
        "description": "Anthropic mid-tier; common in production workloads.",
    },
    "claude-haiku-4-5": {
        "label": "Claude Haiku 4.5",
        "per_million_input_usd": 1.00,
        "description": "Anthropic fastest + cheapest; good for high-volume.",
    },
    "gpt-5": {
        "label": "GPT-5",
        "per_million_input_usd": 10.00,
        "description": "OpenAI flagship; illustrative rate.",
    },
    "gpt-4o-mini": {
        "label": "GPT-4o mini",
        "per_million_input_usd": 0.15,
        "description": "OpenAI cheap tier; illustrative rate.",
    },
}

# The model we price against by default unless the user picks another.
DEFAULT_MODEL = "claude-opus-4-7"


@dataclass
class PriceQuote:
    model_id: str
    model_label: str
    per_million_input_usd: float
    tokens: int
    cost_usd: float

    @property
    def cost_str(self) -> str:
        """Pretty-format the cost. Shows micro-cents for tiny numbers,
        cents for small, dollars otherwise."""
        c = abs(self.cost_usd)
        if c < 0.01:
            # show in milli-cents for transparency at tiny scale
            return f"{self.cost_usd * 100:+.3f}¢"
        if c < 1:
            return f"{self.cost_usd * 100:+.1f}¢"
        if c < 1000:
            return f"{self.cost_usd:+.2f} USD"
        return f"{self.cost_usd:+,.0f} USD"


def get_model(model_id: str | None = None) -> dict:
    """Resolve a model id to its metadata dict. Falls back to default."""
    if model_id and model_id in MODELS:
        return {"id": model_id, **MODELS[model_id]}
    env_override = os.environ.get("PLANCKBOT_PRICING_MODEL")
    if env_override and env_override in MODELS:
        return {"id": env_override, **MODELS[env_override]}
    return {"id": DEFAULT_MODEL, **MODELS[DEFAULT_MODEL]}


def estimate_cost(tokens: int, model_id: str | None = None) -> PriceQuote:
    """Estimate USD cost for `tokens` input tokens at the chosen model's
    per-million rate. Works for positive (saved) or negative (regressed)
    token counts — the quote signs match the tokens.
    """
    model = get_model(model_id)
    rate = float(model["per_million_input_usd"])
    cost = (tokens / 1_000_000.0) * rate
    return PriceQuote(
        model_id=model["id"],
        model_label=model["label"],
        per_million_input_usd=rate,
        tokens=tokens,
        cost_usd=cost,
    )


def list_models() -> list[dict]:
    """Return models for UI dropdowns, with a stable ordering."""
    return [
        {"id": mid, **meta}
        for mid, meta in sorted(
            MODELS.items(),
            key=lambda kv: kv[1]["per_million_input_usd"],
            reverse=True,
        )
    ]
