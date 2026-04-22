"""Pluggable triple sources: load tool-call triples from external systems."""

from planckbot.ingest.base import TripleSource
from planckbot.ingest.manual import ManualSource
from planckbot.ingest.orquesta import OrquestaSource

__all__ = ["TripleSource", "ManualSource", "OrquestaSource"]
