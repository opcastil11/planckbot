"""Shared application state singleton."""

import sqlite3
from planckbot.config import config
from planckbot.db.engine import get_connection
from planckbot.db.models import Project
from planckbot.experiments.manager import ExperimentManager
from planckbot.models.checkpoints import CheckpointManager
from planckbot.paper.log import PaperLog
from planckbot.tools.projects import ProjectStore
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

        # v6: project scoping. A UI "lens" override lets the user peek at
        # another project from the switcher without flipping the DB's
        # is_active row. `_lens_override` = None → follow the DB's active
        # project; = "__all__" → unscoped view; = "<uuid>" → force that id.
        self.projects = ProjectStore(self.conn)
        self._lens_override: str | None = None

        # Activity feed: how far the user has read. We compare this
        # against `SELECT MAX(id) FROM activity_events` to render an
        # "unseen" badge on the sidebar. Reset every time the user
        # visits the /activity page. In-memory is fine — a UI restart
        # is rare and the cost of a freshly-zeroed badge is low.
        self.last_seen_activity_id: int = 0

    def active_project(self) -> Project | None:
        """The project whose id should scope queries on every page.

        Always hits the DB (projects are mutable from the CLI between
        pageloads) so we stay coherent with whatever `planckbot project
        switch` last wrote."""
        if self._lens_override == "__all__":
            return None
        if self._lens_override:
            return self.projects.get(self._lens_override)
        return self.projects.get_active()

    def active_project_id(self) -> str | None:
        """Shortcut returning the id (or None for unscoped / no project)."""
        if self._lens_override == "__all__":
            return None
        p = self.active_project()
        return p.id if p else None

    def set_lens(self, override: str | None) -> None:
        """Set the UI-only project lens. `None` follows the DB's active
        project; `"__all__"` shows unscoped data; `"<uuid>"` pins a
        specific project. Does NOT update the DB or rewrite
        ~/.claude.json — that's what `planckbot project switch` is for.
        """
        self._lens_override = override

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
