"""CRUD for the `tool_versions` table.

A ToolVersion is an immutable snapshot of a tool's source, identified by the
sha256 of its source. Triples and checkpoints reference a tool_version_id so
that an adapter trained against one revision is never silently applied to a
different revision. See docs/PLANCKBOT_CONCEPT.md §7.
"""

import sqlite3

from planckbot.db.models import ToolVersion


class ToolVersionStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, version: ToolVersion) -> ToolVersion:
        row = version.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO tool_versions ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return version

    def get(self, version_id: str) -> ToolVersion | None:
        cur = self.conn.execute(
            "SELECT * FROM tool_versions WHERE id = ?", (version_id,)
        )
        row = cur.fetchone()
        return ToolVersion.from_row(row) if row else None

    def latest(self, tool_name: str) -> ToolVersion | None:
        """Most recent version for a tool (by created_at)."""
        cur = self.conn.execute(
            "SELECT * FROM tool_versions WHERE tool_name = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (tool_name,),
        )
        row = cur.fetchone()
        return ToolVersion.from_row(row) if row else None

    def by_hash(self, tool_name: str, code_hash: str) -> ToolVersion | None:
        cur = self.conn.execute(
            "SELECT * FROM tool_versions WHERE tool_name = ? AND code_hash = ?",
            (tool_name, code_hash),
        )
        row = cur.fetchone()
        return ToolVersion.from_row(row) if row else None

    def list_for_tool(self, tool_name: str) -> list[ToolVersion]:
        cur = self.conn.execute(
            "SELECT * FROM tool_versions WHERE tool_name = ? ORDER BY created_at ASC",
            (tool_name,),
        )
        return [ToolVersion.from_row(r) for r in cur.fetchall()]

    def count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM tool_versions")
        return cur.fetchone()[0]
