"""CRUD for the `projects` table.

A project is a named target folder that PlanckBot watches. At most one
project has `is_active = 1` at a time — its `path` is the one baked into
the planckbot-fs MCP entry in `~/.claude.json`. Switching active project
(`ProjectStore.set_active(name)`) is the DB-side of what the CLI
`planckbot project switch` does; the CLI also rewrites `~/.claude.json`
so Claude Code actually hits the new folder on restart.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from planckbot.db.models import Project


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- create / read -----------------------------------------------------

    def create(
        self,
        name: str,
        path: str | Path,
        *,
        description: str | None = None,
        activate: bool = False,
    ) -> Project:
        """Insert a new project row. `path` must be absolute.

        Raises ValueError if a project with the same name already exists or
        if the path isn't absolute. Does NOT check the path exists on disk
        — tests and remote workflows may create projects for paths that
        aren't mounted locally.
        """
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            raise ValueError(
                f"project path must be absolute, got {path!r}"
            )
        if self.by_name(name) is not None:
            raise ValueError(f"a project named {name!r} already exists")

        project = Project(
            name=name,
            path=str(resolved),
            description=description,
            is_active=1 if activate else 0,
        )
        row = project.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO projects ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        if activate:
            # Enforce single-active invariant on insert too.
            self.conn.execute(
                "UPDATE projects SET is_active = 0 WHERE id != ?",
                (project.id,),
            )
        self.conn.commit()
        return project

    def get(self, project_id: str) -> Project | None:
        cur = self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        )
        row = cur.fetchone()
        return Project.from_row(row) if row else None

    def by_name(self, name: str) -> Project | None:
        cur = self.conn.execute(
            "SELECT * FROM projects WHERE name = ?", (name,)
        )
        row = cur.fetchone()
        return Project.from_row(row) if row else None

    def by_path(self, path: str | Path) -> Project | None:
        """Lookup by absolute path. Useful for `planckbot init` which
        wants to re-activate rather than duplicate on re-runs."""
        resolved = str(Path(path).expanduser())
        cur = self.conn.execute(
            "SELECT * FROM projects WHERE path = ? ORDER BY created_at ASC",
            (resolved,),
        )
        row = cur.fetchone()
        return Project.from_row(row) if row else None

    def list_all(self) -> list[Project]:
        cur = self.conn.execute(
            "SELECT * FROM projects ORDER BY is_active DESC, created_at ASC"
        )
        return [Project.from_row(r) for r in cur.fetchall()]

    def get_active(self) -> Project | None:
        cur = self.conn.execute(
            "SELECT * FROM projects WHERE is_active = 1 "
            "ORDER BY created_at ASC LIMIT 1"
        )
        row = cur.fetchone()
        return Project.from_row(row) if row else None

    def count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM projects"
        ).fetchone()[0]

    # --- mutate ------------------------------------------------------------

    def set_active(self, project_id: str) -> Project:
        """Mark one project active, all others inactive. Also bumps
        last_used_at on the newly-active row so the UI can sort by recency."""
        project = self.get(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")
        self.conn.execute("UPDATE projects SET is_active = 0")
        self.conn.execute(
            "UPDATE projects SET is_active = 1, last_used_at = ? WHERE id = ?",
            (_now_iso(), project_id),
        )
        self.conn.commit()
        project.is_active = 1
        project.last_used_at = _now_iso()
        return project

    def deactivate_all(self) -> None:
        """Leave every project inactive. Used by `planckbot uninstall`
        so a reinstall doesn't silently resume the previous target."""
        self.conn.execute("UPDATE projects SET is_active = 0")
        self.conn.commit()

    def rename(self, project_id: str, new_name: str) -> Project:
        if self.by_name(new_name) is not None:
            raise ValueError(
                f"another project is already named {new_name!r}"
            )
        project = self.get(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")
        self.conn.execute(
            "UPDATE projects SET name = ? WHERE id = ?",
            (new_name, project_id),
        )
        self.conn.commit()
        project.name = new_name
        return project

    def update_path(self, project_id: str, new_path: str | Path) -> Project:
        """Change the watched folder of an existing project. Useful when a
        repo moves on disk."""
        resolved = Path(new_path).expanduser()
        if not resolved.is_absolute():
            raise ValueError(
                f"project path must be absolute, got {new_path!r}"
            )
        project = self.get(project_id)
        if project is None:
            raise ValueError(f"project not found: {project_id}")
        self.conn.execute(
            "UPDATE projects SET path = ? WHERE id = ?",
            (str(resolved), project_id),
        )
        self.conn.commit()
        project.path = str(resolved)
        return project

    def touch(self, project_id: str) -> None:
        """Update last_used_at; called when a proxy session starts against
        this project."""
        self.conn.execute(
            "UPDATE projects SET last_used_at = ? WHERE id = ?",
            (_now_iso(), project_id),
        )
        self.conn.commit()

    def delete(
        self,
        project_id: str,
        *,
        cascade: bool = False,
    ) -> None:
        """Delete the project row.

        With `cascade=True`, also purges triples / checkpoints / cron jobs /
        synthesized tools / gap reports / experiments tagged to this
        project. Without cascade those rows are re-parented to NULL
        `project_id` (i.e. become legacy / unscoped) so the foreign-key
        constraint stays satisfied. This keeps history intact and lets a
        future `planckbot project create --adopt-legacy` pick them up.
        """
        tables = (
            "triples",
            "model_checkpoints",
            "cron_jobs",
            "synthesized_tools",
            "gap_reports",
            "experiments",
        )
        for table in tables:
            if cascade:
                self.conn.execute(
                    f"DELETE FROM {table} WHERE project_id = ?",
                    (project_id,),
                )
            else:
                self.conn.execute(
                    f"UPDATE {table} SET project_id = NULL "
                    "WHERE project_id = ?",
                    (project_id,),
                )
        self.conn.execute(
            "DELETE FROM projects WHERE id = ?", (project_id,)
        )
        self.conn.commit()

    # --- legacy helpers ----------------------------------------------------

    def adopt_legacy(self, project_id: str) -> dict[str, int]:
        """Reassign every row with NULL `project_id` to this project.

        Returns a {table_name: rows_touched} dict so the CLI can report
        what happened. Used by `planckbot project create --adopt-legacy`:
        when a user upgrades from a pre-v6 install, the first project they
        create usually wants to own the pre-existing triples/checkpoints
        rather than leave them stranded as "(legacy)".
        """
        touched: dict[str, int] = {}
        for table in (
            "triples",
            "model_checkpoints",
            "cron_jobs",
            "synthesized_tools",
            "gap_reports",
            "experiments",
        ):
            cur = self.conn.execute(
                f"UPDATE {table} SET project_id = ? WHERE project_id IS NULL",
                (project_id,),
            )
            touched[table] = cur.rowcount or 0
        self.conn.commit()
        return touched
