"""Per-session tool-output cache for the PreToolUse cache-deny path.

Backs the A.cache_deny_read technique (Tier-1 bench winner: 13% ceiling).
The cache is session-scoped — entries from session X never serve session Y
even if they hit the same path, because Claude's reasoning context differs
and we don't want cross-session contamination.

Lookup is by (session_id, tool_name, cache_key). The hook short-circuits a
Read when:
  1. An entry exists for the key.
  2. The entry is not `dirty` (no Edit/Write hit the same path since).
  3. For file-backed tools, the on-disk `mtime_ns` matches what we stored.

When any of those fail, the cache returns None and the tool runs normally;
the new output is written back.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

from planckbot.db.models import ToolCacheEntry
from planckbot.experiments.metrics import count_tokens_approx


class ToolCacheStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- write ----------------------------------------------------------

    def put(
        self,
        *,
        session_id: str,
        tool_name: str,
        cache_key: str,
        content: str,
        file_path: str | None = None,
        file_mtime_ns: int | None = None,
        project_id: str | None = None,
    ) -> ToolCacheEntry:
        """Insert or replace a cache entry. (session_id, tool_name,
        cache_key) is unique by convention — older entries with the same
        triple are deleted to keep lookup deterministic."""
        self.conn.execute(
            "DELETE FROM tool_cache WHERE session_id = ? "
            "AND tool_name = ? AND cache_key = ?",
            (session_id, tool_name, cache_key),
        )
        entry = ToolCacheEntry(
            session_id=session_id,
            project_id=project_id,
            tool_name=tool_name,
            cache_key=cache_key,
            file_path=file_path,
            file_mtime_ns=file_mtime_ns,
            content=content,
            content_tokens=count_tokens_approx(content) if content else 0,
        )
        row = entry.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO tool_cache ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return entry

    # --- read -----------------------------------------------------------

    def lookup(
        self,
        *,
        session_id: str,
        tool_name: str,
        cache_key: str,
        verify_mtime: bool = True,
    ) -> Optional[ToolCacheEntry]:
        """Return a fresh, non-dirty cache entry for the key, or None.

        Verifies the on-disk mtime against `file_mtime_ns` when
        `verify_mtime=True` and the entry has a `file_path`. The check is
        done via `os.stat`; if the file is missing or unstattable we treat
        the cache as stale (returns None).
        """
        cur = self.conn.execute(
            "SELECT * FROM tool_cache WHERE session_id = ? "
            "AND tool_name = ? AND cache_key = ? LIMIT 1",
            (session_id, tool_name, cache_key),
        )
        row = cur.fetchone()
        if row is None:
            return None
        entry = ToolCacheEntry.from_row(row)
        if entry.dirty:
            return None
        if verify_mtime and entry.file_path and entry.file_mtime_ns is not None:
            try:
                current = os.stat(entry.file_path).st_mtime_ns
            except OSError:
                return None
            if current != entry.file_mtime_ns:
                return None
        return entry

    # --- invalidation ---------------------------------------------------

    def mark_dirty(
        self,
        *,
        session_id: str,
        file_path: str,
    ) -> int:
        """Mark every entry in this session whose `file_path` matches as
        dirty. Called by the PreToolUse hook when an Edit/Write hits a
        path. Returns the number of rows affected."""
        cur = self.conn.execute(
            "UPDATE tool_cache SET dirty = 1 "
            "WHERE session_id = ? AND file_path = ? AND dirty = 0",
            (session_id, file_path),
        )
        self.conn.commit()
        return cur.rowcount

    def record_hit(self, entry_id: str) -> None:
        """Bump hits counter + last_hit_at. Called when the hook serves
        from cache."""
        from datetime import datetime, timezone
        self.conn.execute(
            "UPDATE tool_cache SET hits = hits + 1, last_hit_at = ? "
            "WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), entry_id),
        )
        self.conn.commit()

    # --- introspection / housekeeping -----------------------------------

    def count(self, session_id: str | None = None) -> int:
        if session_id is None:
            cur = self.conn.execute("SELECT COUNT(*) FROM tool_cache")
        else:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM tool_cache WHERE session_id = ?",
                (session_id,),
            )
        return cur.fetchone()[0]

    def list_for_session(
        self, session_id: str, limit: int = 200
    ) -> list[ToolCacheEntry]:
        cur = self.conn.execute(
            "SELECT * FROM tool_cache WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        )
        return [ToolCacheEntry.from_row(r) for r in cur.fetchall()]

    def stats_summary(self) -> dict:
        """Aggregate hit/miss telemetry across the whole table."""
        cur = self.conn.execute(
            "SELECT COUNT(*) AS entries, "
            "COALESCE(SUM(hits), 0) AS total_hits, "
            "COALESCE(SUM(content_tokens), 0) AS tokens_cached "
            "FROM tool_cache"
        )
        row = cur.fetchone()
        return {
            "entries": row["entries"],
            "total_hits": row["total_hits"],
            "tokens_cached": row["tokens_cached"],
        }

    def purge_session(self, session_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM tool_cache WHERE session_id = ?", (session_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def purge_older_than(self, iso_ts: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM tool_cache WHERE created_at < ?", (iso_ts,),
        )
        self.conn.commit()
        return cur.rowcount
