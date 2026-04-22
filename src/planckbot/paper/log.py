"""Structured paper log: research journal for experiments."""

import json
import sqlite3
from datetime import datetime, timezone

from planckbot.db.models import PaperEntry


class PaperLog:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_entry(
        self,
        title: str,
        content: str,
        entry_type: str = "observation",
        experiment_id: str | None = None,
        tags: list[str] | None = None,
    ) -> PaperEntry:
        entry = PaperEntry(
            experiment_id=experiment_id,
            entry_type=entry_type,
            title=title,
            content=content,
            tags=tags or [],
        )
        row = entry.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO paper_log ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return entry

    def get(self, entry_id: str) -> PaperEntry | None:
        cur = self.conn.execute("SELECT * FROM paper_log WHERE id = ?", (entry_id,))
        row = cur.fetchone()
        return PaperEntry.from_row(row) if row else None

    def list_entries(
        self,
        entry_type: str | None = None,
        experiment_id: str | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[PaperEntry]:
        query = "SELECT * FROM paper_log WHERE 1=1"
        params: list = []

        if entry_type:
            query += " AND entry_type = ?"
            params.append(entry_type)
        if experiment_id:
            query += " AND experiment_id = ?"
            params.append(experiment_id)
        if tag:
            query += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cur = self.conn.execute(query, params)
        return [PaperEntry.from_row(r) for r in cur.fetchall()]

    def update_entry(self, entry_id: str, content: str):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE paper_log SET content = ?, updated_at = ? WHERE id = ?",
            (content, now, entry_id),
        )
        self.conn.commit()

    def delete(self, entry_id: str):
        self.conn.execute("DELETE FROM paper_log WHERE id = ?", (entry_id,))
        self.conn.commit()

    def count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM paper_log")
        return cur.fetchone()[0]

    def auto_log_experiment_result(self, experiment) -> PaperEntry:
        """Auto-generate a paper log entry when an experiment completes."""
        metrics = experiment.metrics or {}
        savings = metrics.get("token_savings_pct", "N/A")
        accuracy = metrics.get("accuracy", "N/A")

        verdict = "SUPPORTED" if experiment.status == "completed" else "NOT SUPPORTED"

        content = (
            f"**Experiment**: {experiment.name}\n\n"
            f"**Hypothesis**: {experiment.hypothesis}\n\n"
            f"**Configuration**:\n"
            f"```json\n{json.dumps(experiment.config, indent=2)}\n```\n\n"
            f"**Results**:\n"
            f"- Token savings: {savings}%\n"
            f"- Accuracy: {accuracy}\n"
            f"- Status: {experiment.status}\n\n"
            f"**Verdict**: Hypothesis {verdict}\n"
        )

        return self.add_entry(
            title=f"[Auto] {experiment.name} — {experiment.status}",
            content=content,
            entry_type="result",
            experiment_id=experiment.id,
            tags=["auto-generated", experiment.experiment_type],
        )
