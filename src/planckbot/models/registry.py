"""Multi-adapter memory management.

At >~20 registered tools, loading every per-tool LoRA adapter into
memory at startup becomes wasteful — each adapter is ~10 MB and the
base SmolLM2-135M is ~270 MB, so 50 adapters is 500 MB of extra RSS
for potentially idle tools.

This module is the seam for an LRU cache of adapters sharing a single
base model. The `AdapterRegistry` below maintains at most `max_active`
loaded adapters and evicts the least recently used when a new one is
requested.

For the reference implementation we don't actually swap LoRA weights
in/out (that requires a PEFT internals dance); instead we cache
fully-loaded models per tool and evict them. This is a simpler
correctness-first implementation; moving to true LoRA weight-swapping
is a one-screen refactor when we need to scale past ~100 tools.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class RegistryEntry:
    tool_name: str
    checkpoint_id: str
    loaded_at: str       # ISO8601 — handy for diagnostics
    base_model: str
    adapter_path: str


class AdapterRegistry:
    """LRU cache of loaded adapters keyed by tool_name.

    Thread-safe via a coarse lock. The lock is held only across the
    dict mutation + eviction; the actual model load happens inside the
    lock only if you pass `loader=` — callers who do their own loading
    can reserve a slot first, drop the lock, and load.
    """

    def __init__(self, max_active: int = 8):
        self.max_active = max(1, int(max_active))
        self._entries: OrderedDict[str, RegistryEntry] = OrderedDict()
        self._models: dict[str, object] = {}
        self._lock = threading.RLock()

    def size(self) -> int:
        return len(self._entries)

    def has(self, tool_name: str) -> bool:
        return tool_name in self._entries

    def get(self, tool_name: str):
        """Return the cached model object (or None). Touches LRU order."""
        with self._lock:
            if tool_name not in self._entries:
                return None
            self._entries.move_to_end(tool_name)
            return self._models.get(tool_name)

    def put(
        self,
        tool_name: str,
        checkpoint_id: str,
        model: object,
        base_model: str = "",
        adapter_path: str = "",
    ) -> None:
        """Insert or refresh a loaded model. Evicts the LRU entry if
        we're at capacity."""
        from datetime import datetime, timezone
        with self._lock:
            if tool_name in self._entries:
                self._entries.move_to_end(tool_name)
                self._models[tool_name] = model
                return
            while len(self._entries) >= self.max_active:
                oldest_name, _ = self._entries.popitem(last=False)
                self._models.pop(oldest_name, None)
            self._entries[tool_name] = RegistryEntry(
                tool_name=tool_name,
                checkpoint_id=checkpoint_id,
                loaded_at=datetime.now(timezone.utc).isoformat(),
                base_model=base_model,
                adapter_path=adapter_path,
            )
            self._models[tool_name] = model

    def evict(self, tool_name: str) -> bool:
        """Remove an entry (e.g. after the checkpoint was deactivated)."""
        with self._lock:
            had = tool_name in self._entries
            self._entries.pop(tool_name, None)
            self._models.pop(tool_name, None)
            return had

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._models.clear()

    def entries(self) -> list[RegistryEntry]:
        """Snapshot of currently-loaded adapters, LRU-first (oldest → newest)."""
        with self._lock:
            return list(self._entries.values())
