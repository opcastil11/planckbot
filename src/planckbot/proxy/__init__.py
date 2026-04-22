"""Runtime intervention layer.

Proxy wraps a callable tool, records every call as a triple, and — when a
trained Planck adapter is active — can modify the tool's input or output to
save tokens.

Three modes control how aggressive the proxy is:
    ObserveMode   — log only, never change anything
    SuggestMode   — log + compute the adapter's prediction, but still return
                    the raw result (for A/B measurement)
    InterveneMode — apply the adapter above a confidence threshold
"""

from planckbot.proxy.intercept import (
    InterveneMode,
    ObserveMode,
    PlanckProxy,
    ProxyMode,
    ProxyResult,
    SuggestMode,
    planck_tool,
)

__all__ = [
    "InterveneMode",
    "ObserveMode",
    "PlanckProxy",
    "ProxyMode",
    "ProxyResult",
    "SuggestMode",
    "planck_tool",
]
