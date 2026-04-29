"""Base protocol for triple sources.

A TripleSource pulls tool-call records from some external system (a hosted
agent platform, a local log file, an MCP proxy's buffer, etc.) and translates
them into the shape PlanckBot trains on: (tool_name, input, output) plus
optional context.

Concrete sources live alongside this module: `manual.py`, `orquesta.py`, etc.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Iterator, Optional

from planckbot.tools.triples import TriplesStore


class TripleRecord(dict):
    """Intermediate dict yielded by `fetch()`, with a known shape.

    Keys:
        tool_name:    str          — tool identifier
        input_data:   Any           — tool input (will be JSON-encoded if dict)
        output_data:  Any           — tool output (will be JSON-encoded if dict)
        context_data: Any | None    — surrounding conversation context
        session_id:   str | None    — foreign session/prompt id from the source
    """


class TripleSource(ABC):
    """A source of training triples. Subclass and implement `fetch()`."""

    name: str = "base"

    @abstractmethod
    def fetch(
        self, limit: int = 1000, since: Optional[str] = None
    ) -> Iterator[TripleRecord]:
        """Yield raw triple records from the external system.

        `since` is an opaque cursor (usually an ISO timestamp) — sources may
        ignore it for small/static inputs.
        """

    def ingest(
        self,
        store: TriplesStore,
        limit: int = 1000,
        since: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> int:
        """Pull records from `fetch()` and write them to the triples store.

        `project_id`, when supplied, tags every imported triple to that
        project. An `orquesta` ingest pointed at a project's traffic can
        thus feed directly into that project's adapter without polluting
        cross-project stats.
        """
        count = 0
        for rec in self.fetch(limit=limit, since=since):
            store.add(
                tool_name=rec["tool_name"],
                input_data=rec["input_data"],
                output_data=rec["output_data"],
                context_data=rec.get("context_data"),
                session_id=rec.get("session_id"),
                filtered_output=rec.get("filtered_output"),
                source=self.name,
                project_id=project_id,
                created_at=rec.get("created_at"),
            )
            count += 1
        return count
