"""Proxy that wraps a tool callable and (optionally) applies a Planck adapter.

Design:
    mode selects the behavior — Observe / Suggest / Intervene.
    predictor is an injectable callable that turns a (tool_name, input, output,
        strategy) into a (predicted_text, confidence) pair. The real impl is
        adapter-backed; tests pass a stub.

Recording the triple is the same in every mode: every call through the proxy
lands in the TriplesStore so training data keeps accumulating as you use your
tools. That's the "closing the loop" bit — serve also observes.
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from planckbot.db.models import Triple
from planckbot.tools.triples import TriplesStore


Predictor = Callable[[str, str, str, str], tuple[str, float]]
"""Signature: (tool_name, input_str, output_str, strategy) -> (text, confidence)."""


class ProxyMode(Protocol):
    """Strategy object controlling how the proxy behaves per tool call."""

    name: str
    confidence_threshold: float

    def on_result(
        self,
        *,
        tool_name: str,
        input_str: str,
        raw_output: str,
        predictor: Optional[Predictor],
        strategy: str,
    ) -> "ProxyDecision":
        ...


@dataclass
class ProxyDecision:
    """What the mode decided for a single call."""
    final_output: str          # What to return to the caller
    predicted_output: Optional[str] = None
    confidence: Optional[float] = None
    intervened: bool = False    # True iff final_output != raw_output


@dataclass
class ProxyResult:
    """Full record of a proxied call — what the caller sees plus metadata."""
    tool_name: str
    input_data: Any
    raw_output: str
    final_output: str
    predicted_output: Optional[str]
    confidence: Optional[float]
    intervened: bool
    latency_ms: float
    mode: str
    triple: Optional[Triple] = None


# -- Mode implementations --------------------------------------------------

@dataclass
class ObserveMode:
    """Pass-through. Never call the predictor, never modify output."""
    name: str = "observe"
    confidence_threshold: float = 1.0  # unused

    def on_result(self, *, tool_name, input_str, raw_output, predictor, strategy):
        return ProxyDecision(final_output=raw_output)


@dataclass
class SuggestMode:
    """Runs the predictor for logging, but still returns the raw output.

    Useful before you trust a Planck model enough to actually swap outputs —
    you see what it *would* have done in the UI without affecting runtime.
    """
    name: str = "suggest"
    confidence_threshold: float = 0.90

    def on_result(self, *, tool_name, input_str, raw_output, predictor, strategy):
        if predictor is None:
            return ProxyDecision(final_output=raw_output)
        predicted, conf = predictor(tool_name, input_str, raw_output, strategy)
        return ProxyDecision(
            final_output=raw_output,
            predicted_output=predicted,
            confidence=conf,
            intervened=False,
        )


@dataclass
class InterveneMode:
    """Actually apply the adapter's prediction when confidence is high enough."""
    name: str = "intervene"
    confidence_threshold: float = 0.90

    def on_result(self, *, tool_name, input_str, raw_output, predictor, strategy):
        if predictor is None:
            return ProxyDecision(final_output=raw_output)
        predicted, conf = predictor(tool_name, input_str, raw_output, strategy)
        if conf >= self.confidence_threshold:
            return ProxyDecision(
                final_output=predicted,
                predicted_output=predicted,
                confidence=conf,
                intervened=True,
            )
        return ProxyDecision(
            final_output=raw_output,
            predicted_output=predicted,
            confidence=conf,
            intervened=False,
        )


# -- The proxy -------------------------------------------------------------

# Tools with side effects never get their output swapped, regardless of mode.
# The adapter is still run in Suggest/Intervene so we collect training data;
# the decision is just forced to pass-through.
SIDE_EFFECT_TOOLS: frozenset[str] = frozenset({
    "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Bash",  # side-effectful by default; future work can mark specific bash
             # commands as read-only and opt them in.
})


def _to_str(x: Any) -> str:
    if isinstance(x, str):
        return x
    try:
        import json
        return json.dumps(x, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(x)


class PlanckProxy:
    """Wrap a tool callable so every invocation is observed (and optionally
    modified) by a Planck model."""

    def __init__(
        self,
        store: TriplesStore,
        mode: Optional[ProxyMode] = None,
        predictor: Optional[Predictor] = None,
        strategy: str = "filter_output",
        project_id: Optional[str] = None,
    ):
        self.store = store
        self.mode: ProxyMode = mode or ObserveMode()
        self.predictor = predictor
        self.strategy = strategy
        # v6: triples this proxy records are tagged with this project_id.
        # The MCP server resolves the currently-active project at startup
        # and passes it in; standalone tests and the decorator form can
        # leave it None (legacy/unscoped).
        self.project_id = project_id

    def wrap(self, fn: Callable[..., Any], tool_name: str) -> Callable[..., ProxyResult]:
        """Return a new callable that runs `fn` through the proxy."""

        @functools.wraps(fn)
        def wrapped(*args, **kwargs) -> ProxyResult:
            return self.call(fn, tool_name, *args, **kwargs)

        wrapped.__planck_tool_name__ = tool_name  # type: ignore[attr-defined]
        return wrapped

    def call(
        self,
        fn: Callable[..., Any],
        tool_name: str,
        *args,
        **kwargs,
    ) -> ProxyResult:
        from planckbot import activity

        # Capture the input before we mutate anything.
        input_payload: Any = kwargs if kwargs else (args if len(args) > 1 else (args[0] if args else None))
        input_str = _to_str(input_payload)

        activity.log_event(
            self.store.conn, "proxy", "rx",
            f"{tool_name} call received",
            project_id=self.project_id,
            meta={"tool": tool_name, "mode": self.mode.name},
        )

        # Run the real tool.
        t0 = time.time()
        raw = fn(*args, **kwargs)
        latency_ms = round((time.time() - t0) * 1000, 2)
        raw_str = _to_str(raw)

        activity.log_event(
            self.store.conn, "proxy", "upstream",
            f"{tool_name} → {len(raw_str)} chars in {latency_ms} ms",
            project_id=self.project_id,
            meta={"tool": tool_name, "latency_ms": latency_ms,
                  "chars": len(raw_str)},
        )

        # Decide what to return.
        if tool_name in SIDE_EFFECT_TOOLS:
            decision = ProxyDecision(final_output=raw_str)
            if self.predictor is not None and self.mode.name != "observe":
                # Still observe for training data, but don't swap.
                _, conf = self.predictor(tool_name, input_str, raw_str, self.strategy)
                decision.confidence = conf
        else:
            decision = self.mode.on_result(
                tool_name=tool_name,
                input_str=input_str,
                raw_output=raw_str,
                predictor=self.predictor,
                strategy=self.strategy,
            )
            if decision.intervened:
                activity.log_event(
                    self.store.conn, "proxy", "intervene",
                    f"{tool_name} swapped raw→filtered "
                    f"(conf={decision.confidence:.2f})"
                    if decision.confidence is not None else
                    f"{tool_name} swapped raw→filtered",
                    project_id=self.project_id,
                    meta={"tool": tool_name,
                          "confidence": decision.confidence},
                )

        # Record the triple.
        triple = self.store.add(
            tool_name=tool_name,
            input_data=input_payload if input_payload is not None else "",
            output_data=raw_str,
            source=f"proxy:{self.mode.name}",
            filtered_output=decision.predicted_output,
            project_id=self.project_id,
        )

        activity.log_event(
            self.store.conn, "proxy", "store",
            f"saved triple {triple.id[:8]} ({triple.input_tokens}/"
            f"{triple.output_tokens} tok)",
            project_id=self.project_id,
            meta={"triple_id": triple.id, "tool": tool_name,
                  "input_tokens": triple.input_tokens,
                  "output_tokens": triple.output_tokens},
        )

        return ProxyResult(
            tool_name=tool_name,
            input_data=input_payload,
            raw_output=raw_str,
            final_output=decision.final_output,
            predicted_output=decision.predicted_output,
            confidence=decision.confidence,
            intervened=decision.intervened,
            latency_ms=latency_ms,
            mode=self.mode.name,
            triple=triple,
        )


# -- Decorator convenience -------------------------------------------------

def planck_tool(
    *,
    tool_name: str,
    proxy: PlanckProxy,
):
    """Decorator form: `@planck_tool(tool_name="...", proxy=p)`.

    The decorated function's original signature is preserved; calling it
    returns a ProxyResult — use `.final_output` to get the user-facing value.
    """
    def _decorate(fn):
        return proxy.wrap(fn, tool_name)
    return _decorate
