"""Blocking-loop cron daemon.

The daemon is the single execution site for scheduled jobs. Start one process
(from the CLI or systemd user unit) and it will keep `next_run_at` honest
forever. File-locked so a second daemon started by accident can't double-fire.
"""

from __future__ import annotations

import fcntl
import logging
import signal
import sqlite3
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from planckbot.cron.jobs import JobContext, JobRegistry, default_registry
from planckbot.cron.store import CronStore


logger = logging.getLogger(__name__)


class Daemon:
    """Simple polling scheduler.

    Dispatches one job at a time. Jobs that take a long time will push other
    jobs' fire times later; for the PlanckBot workload (autolabel in seconds,
    retrain gated behind `min_new_labeled`) that's acceptable. Throwing a
    thread-pool at this is a fine follow-up but currently over-engineered.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        tick_seconds: float = 2.0,
        registry: JobRegistry | None = None,
        lock_path: str | Path | None = None,
    ):
        self.conn = conn
        self.store = CronStore(conn)
        self.registry = registry or default_registry()
        self.tick_seconds = tick_seconds
        self.lock_path = Path(lock_path) if lock_path else Path("/tmp/planckbot-cron.lock")
        self._stopping = False
        self._lock_fh = None

    def _acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Open in append mode so we don't truncate a live owner's pid.
        self._lock_fh = open(self.lock_path, "a+")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                f"another PlanckBot cron daemon already holds {self.lock_path}"
            )
        self._lock_fh.seek(0)
        self._lock_fh.truncate()
        import os
        self._lock_fh.write(f"{os.getpid()}\n")
        self._lock_fh.flush()

    def _release_lock(self) -> None:
        if self._lock_fh is not None:
            try:
                fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
            finally:
                self._lock_fh.close()
                self._lock_fh = None

    def stop(self, *_) -> None:
        self._stopping = True

    def run(self) -> None:
        """Block forever (or until SIGTERM/SIGINT) dispatching due jobs."""
        self._acquire_lock()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info("cron daemon up, tick=%.1fs, lock=%s",
                    self.tick_seconds, self.lock_path)
        try:
            while not self._stopping:
                self._tick()
                # Sleep in small increments so stop() kicks in quickly.
                slept = 0.0
                while slept < self.tick_seconds and not self._stopping:
                    time.sleep(0.25)
                    slept += 0.25
        finally:
            self._release_lock()
            logger.info("cron daemon down")

    def _tick(self) -> None:
        """Run every due job once."""
        now = datetime.now(timezone.utc)
        try:
            due = self.store.list_due(now)
        except sqlite3.Error:
            logger.exception("failed to list due jobs")
            return

        for job in due:
            self.run_job(job.id)

    def run_job(self, job_id: str) -> tuple[str, str]:
        """Dispatch a single job by id. Returns (status, output)."""
        job = self.store.get(job_id)
        if job is None:
            return ("error", f"job {job_id} not found")
        fn = self.registry.get(job.job_type)
        if fn is None:
            output = f"unknown job_type={job.job_type!r}"
            self.store.mark_run(job.id, status="error", output=output)
            return ("error", output)

        ctx = JobContext(conn=self.conn, params=job.params)
        try:
            output = fn(ctx)
            self.store.mark_run(job.id, status="ok", output=output or "")
            return ("ok", output or "")
        except Exception as e:
            output = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            logger.warning("job %s (%s) failed: %s", job.name, job.job_type, e)
            self.store.mark_run(job.id, status="error", output=output)
            return ("error", output)
