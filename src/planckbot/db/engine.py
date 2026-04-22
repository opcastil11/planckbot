"""SQLite connection factory with WAL mode and migrations."""

import sqlite3
from pathlib import Path

from planckbot.db.migrations import migrate


def get_connection(db_path: Path | str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)
    return conn
