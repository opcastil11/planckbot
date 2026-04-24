"""CRUD for the `cron_jobs` table."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from planckbot.db.models import CronJob


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CronStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, job: CronJob) -> CronJob:
        """Insert a new job. next_run_at defaults to now+interval."""
        if job.next_run_at is None:
            job.next_run_at = (
                datetime.now(timezone.utc) + timedelta(seconds=job.interval_seconds)
            ).isoformat()
        row = job.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO cron_jobs ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return job

    def get(self, job_id: str) -> CronJob | None:
        cur = self.conn.execute("SELECT * FROM cron_jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return CronJob.from_row(row) if row else None

    def by_name(self, name: str) -> CronJob | None:
        cur = self.conn.execute("SELECT * FROM cron_jobs WHERE name = ?", (name,))
        row = cur.fetchone()
        return CronJob.from_row(row) if row else None

    def list_all(self, project_id: str | None = None) -> list[CronJob]:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT * FROM cron_jobs ORDER BY created_at DESC"
            )
        else:
            # Include NULL-project jobs: those are "global" and should be
            # visible in every project's view (e.g. a machine-wide retrain
            # signal doesn't want to be duplicated per project).
            cur = self.conn.execute(
                "SELECT * FROM cron_jobs "
                "WHERE project_id = ? OR project_id IS NULL "
                "ORDER BY created_at DESC",
                (project_id,),
            )
        return [CronJob.from_row(r) for r in cur.fetchall()]

    def list_due(self, now: datetime | None = None) -> list[CronJob]:
        """Enabled jobs whose next_run_at is in the past (or NULL)."""
        now_iso = (now or datetime.now(timezone.utc)).isoformat()
        cur = self.conn.execute(
            "SELECT * FROM cron_jobs "
            "WHERE enabled = 1 AND (next_run_at IS NULL OR next_run_at <= ?) "
            "ORDER BY next_run_at ASC",
            (now_iso,),
        )
        return [CronJob.from_row(r) for r in cur.fetchall()]

    def set_enabled(self, job_id: str, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE cron_jobs SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, job_id),
        )
        self.conn.commit()

    def delete(self, job_id: str) -> None:
        self.conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
        self.conn.commit()

    def mark_run(
        self,
        job_id: str,
        *,
        status: str,
        output: str,
        now: datetime | None = None,
    ) -> None:
        """Record a completed run. Advances next_run_at by the job's interval."""
        job = self.get(job_id)
        if job is None:
            return
        now = now or datetime.now(timezone.utc)
        next_run = (now + timedelta(seconds=job.interval_seconds)).isoformat()
        # Truncate output so the DB row doesn't balloon.
        truncated = (output or "")[:4000]
        self.conn.execute(
            "UPDATE cron_jobs "
            "SET last_run_at = ?, next_run_at = ?, last_status = ?, last_output = ? "
            "WHERE id = ?",
            (now.isoformat(), next_run, status, truncated, job_id),
        )
        self.conn.commit()

    def reschedule_now(self, job_id: str) -> None:
        """Make the job fire on the next daemon tick."""
        self.conn.execute(
            "UPDATE cron_jobs SET next_run_at = ? WHERE id = ?",
            (_now_iso(), job_id),
        )
        self.conn.commit()

    def count(self, project_id: str | None = None) -> int:
        if project_id is None:
            return self.conn.execute(
                "SELECT COUNT(*) FROM cron_jobs"
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM cron_jobs "
            "WHERE project_id = ? OR project_id IS NULL",
            (project_id,),
        ).fetchone()[0]
