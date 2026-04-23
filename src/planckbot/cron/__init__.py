"""Background job scheduler for PlanckBot.

The cron engine has three pieces:

- `CronStore` — CRUD over the `cron_jobs` table (schema v3).
- `JobRegistry` — maps a `job_type` string to a function `(params, ctx) -> str`
  that does the actual work. Adding a new job type is a one-function change.
- `Daemon` — a blocking loop that polls `CronStore.list_due(now)`, dispatches
  each due job via the registry, and writes the outcome back to the row.

The UI and CLI talk to the store and the registry; they never spin up their
own schedulers. The daemon is the sole execution point (file-locked so a
second daemon can't double-fire).
"""

from planckbot.cron.store import CronStore
from planckbot.cron.jobs import JobContext, JobRegistry, default_registry
from planckbot.cron.daemon import Daemon

__all__ = ["CronStore", "JobContext", "JobRegistry", "default_registry", "Daemon"]
