"""Manual triple source: load from a local JSON file.

Useful for seeding experiments from a fixture, sharing triples between
machines, or bootstrapping before any live source is wired up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from planckbot.ingest.base import TripleRecord, TripleSource


class ManualSource(TripleSource):
    name = "manual"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def fetch(
        self, limit: int = 1000, since: Optional[str] = None
    ) -> Iterator[TripleRecord]:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text())
        if not isinstance(raw, list):
            raise ValueError(f"{self.path}: expected a JSON array of triples")
        for i, item in enumerate(raw[:limit]):
            if "tool_name" not in item:
                raise ValueError(f"{self.path}[{i}]: missing 'tool_name'")
            yield TripleRecord(
                tool_name=item["tool_name"],
                input_data=item.get("input_data") or item.get("input"),
                output_data=item.get("output_data") or item.get("output"),
                context_data=item.get("context_data") or item.get("context"),
                session_id=item.get("session_id"),
                filtered_output=item.get("filtered_output"),
            )
