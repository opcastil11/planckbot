"""Train / holdout split for bench runs.

Splits happen at the SESSION level — splitting individual triples would
leak context between train and holdout (a Read in train can predict the
Edit two turns later in holdout if both ended up split). Split is
stratified per project so each split sees every project's distribution.

Deterministic by seed. Persisted to JSON so multiple bench runs over time
compare on the same holdout.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Split:
    seed: int
    holdout_frac: float
    train_session_ids: list[str] = field(default_factory=list)
    holdout_session_ids: list[str] = field(default_factory=list)
    # Bookkeeping for inspection.
    sessions_per_project: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "seed": self.seed,
            "holdout_frac": self.holdout_frac,
            "train_session_ids": self.train_session_ids,
            "holdout_session_ids": self.holdout_session_ids,
            "sessions_per_project": self.sessions_per_project,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Split":
        return cls(
            seed=data["seed"],
            holdout_frac=data["holdout_frac"],
            train_session_ids=list(data.get("train_session_ids", [])),
            holdout_session_ids=list(data.get("holdout_session_ids", [])),
            sessions_per_project=dict(data.get("sessions_per_project", {})),
        )


def split_sessions(
    conn: sqlite3.Connection,
    *,
    project_ids: list[str] | None = None,
    holdout_frac: float = 0.2,
    seed: int = 42,
    min_session_triples: int = 2,
) -> Split:
    """Build a session-level split stratified by project.

    Args:
        conn: open sqlite3 connection.
        project_ids: restrict to these project_ids. None = all projects
            including legacy NULL.
        holdout_frac: fraction of sessions per project to hold out.
        seed: RNG seed; same seed + same input data → same split.
        min_session_triples: drop sessions with fewer triples than this
            (single-triple sessions carry no structure for bench).

    Returns a Split. Does NOT persist — call `save_split` for that.
    """
    # Map session_id -> (project_id, triple_count)
    if project_ids is None:
        cur = conn.execute(
            "SELECT session_id, project_id, COUNT(*) as n "
            "FROM triples WHERE session_id IS NOT NULL "
            "GROUP BY session_id, project_id"
        )
        rows = cur.fetchall()
    else:
        if not project_ids:
            return Split(seed=seed, holdout_frac=holdout_frac)
        placeholders = ",".join("?" for _ in project_ids)
        cur = conn.execute(
            f"SELECT session_id, project_id, COUNT(*) as n "
            f"FROM triples WHERE session_id IS NOT NULL "
            f"AND project_id IN ({placeholders}) "
            f"GROUP BY session_id, project_id",
            project_ids,
        )
        rows = cur.fetchall()

    per_project: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r["n"] < min_session_triples:
            continue
        per_project[r["project_id"] or "_null"].append(r["session_id"])

    rng = random.Random(seed)
    train_ids: list[str] = []
    holdout_ids: list[str] = []
    stats: dict[str, dict[str, int]] = {}

    for proj, sessions in per_project.items():
        rng.shuffle(sessions)
        n_holdout = max(1, int(round(len(sessions) * holdout_frac)))
        # Don't holdout literally everything if frac is huge and sessions few.
        n_holdout = min(n_holdout, len(sessions) - 1) if len(sessions) > 1 else 0
        holdout = sessions[:n_holdout]
        train = sessions[n_holdout:]
        holdout_ids.extend(holdout)
        train_ids.extend(train)
        stats[proj] = {
            "total": len(sessions),
            "train": len(train),
            "holdout": len(holdout),
        }

    return Split(
        seed=seed,
        holdout_frac=holdout_frac,
        train_session_ids=sorted(train_ids),
        holdout_session_ids=sorted(holdout_ids),
        sessions_per_project=stats,
    )


def save_split(split: Split, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(split.to_json(), indent=2))


def load_split(path: Path | str) -> Split:
    return Split.from_json(json.loads(Path(path).read_text()))
