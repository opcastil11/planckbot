"""CRUD for experiments."""

import json
import sqlite3
from datetime import datetime, timezone

from planckbot.db.models import Experiment


class ExperimentManager:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, **kwargs) -> Experiment:
        exp = Experiment(**kwargs)
        row = exp.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO experiments ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return exp

    def get(self, exp_id: str) -> Experiment | None:
        cur = self.conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,))
        row = cur.fetchone()
        return Experiment.from_row(row) if row else None

    def list_all(self, status: str | None = None, limit: int = 100) -> list[Experiment]:
        if status:
            cur = self.conn.execute(
                "SELECT * FROM experiments WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [Experiment.from_row(r) for r in cur.fetchall()]

    def update_status(self, exp_id: str, status: str):
        now = datetime.now(timezone.utc).isoformat()
        if status == "running":
            self.conn.execute(
                "UPDATE experiments SET status = ?, started_at = ? WHERE id = ?",
                (status, now, exp_id),
            )
        elif status in ("completed", "failed", "cancelled"):
            self.conn.execute(
                "UPDATE experiments SET status = ?, completed_at = ? WHERE id = ?",
                (status, now, exp_id),
            )
        else:
            self.conn.execute(
                "UPDATE experiments SET status = ? WHERE id = ?", (status, exp_id)
            )
        self.conn.commit()

    def record_metrics(self, exp_id: str, metrics: dict):
        self.conn.execute(
            "UPDATE experiments SET metrics = ? WHERE id = ?",
            (json.dumps(metrics), exp_id),
        )
        self.conn.commit()

    def update_observations(self, exp_id: str, observations: str):
        self.conn.execute(
            "UPDATE experiments SET observations = ? WHERE id = ?",
            (observations, exp_id),
        )
        self.conn.commit()

    def update_checkpoint(self, exp_id: str, checkpoint_id: str):
        self.conn.execute(
            "UPDATE experiments SET checkpoint_id = ? WHERE id = ?",
            (checkpoint_id, exp_id),
        )
        self.conn.commit()

    def delete(self, exp_id: str):
        self.conn.execute("DELETE FROM experiments WHERE id = ?", (exp_id,))
        self.conn.commit()

    def count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM experiments")
        return cur.fetchone()[0]

    def compare(self, ids: list[str]) -> list[Experiment]:
        placeholders = ", ".join("?" for _ in ids)
        cur = self.conn.execute(
            f"SELECT * FROM experiments WHERE id IN ({placeholders})", ids
        )
        return [Experiment.from_row(r) for r in cur.fetchall()]
