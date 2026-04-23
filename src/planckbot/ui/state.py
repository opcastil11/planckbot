"""Shared application state singleton."""

import sqlite3
from planckbot.config import config
from planckbot.db.engine import get_connection
from planckbot.experiments.manager import ExperimentManager
from planckbot.models.checkpoints import CheckpointManager
from planckbot.paper.log import PaperLog
from planckbot.tools.registry import ToolRegistry
from planckbot.tools.triples import TriplesStore
from planckbot.tools.builtin import register_builtins
from planckbot.cron import CronStore, default_registry as default_cron_registry
from planckbot.synth import GapReportStore, SynthesizedToolStore


class AppState:
    """Singleton holding DB connection and all managers."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.config = config
        self.conn: sqlite3.Connection = get_connection(config.db_path)

        # Managers
        self.experiments = ExperimentManager(self.conn)
        self.triples = TriplesStore(self.conn)
        self.checkpoints = CheckpointManager(self.conn)
        self.paper_log = PaperLog(self.conn)

        # Tool registry
        self.registry = ToolRegistry()
        register_builtins(self.registry)

        # Cron
        self.cron = CronStore(self.conn)
        self.cron_registry = default_cron_registry()

        # Layer D
        self.gaps = GapReportStore(self.conn)
        self.synth = SynthesizedToolStore(self.conn)

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        if cls._instance and hasattr(cls._instance, 'conn'):
            try:
                cls._instance.conn.close()
            except Exception:
                pass
        cls._instance = None


def get_state() -> AppState:
    return AppState()
