"""Benchmark harness for evaluating PlanckBot optimization techniques on real
ingested triples. Public surface:

- `load_triples` — filtered loader from `data/planckbot.db`
- `group_into_sessions` — bucket triples by session, ordered temporally
- `Technique` — ABC every candidate optimization implements
- `replay` — drives a Technique across a dataset, returns BenchResult rows
- `aggregate` — roll BenchResult rows into per-tool / per-project / overall
- `split_sessions` — train/holdout split by session, deterministic by seed
"""

from planckbot.bench.harness import (
    Technique,
    TechniqueResult,
    SessionView,
    TripleEvent,
    load_triples,
    group_into_sessions,
    replay,
)
from planckbot.bench.metrics import (
    BenchResult,
    aggregate,
    write_results_csv,
    token_count,
)
from planckbot.bench.datasets import split_sessions, save_split, load_split

__all__ = [
    "Technique", "TechniqueResult", "SessionView", "TripleEvent",
    "load_triples", "group_into_sessions", "replay",
    "BenchResult", "aggregate", "write_results_csv", "token_count",
    "split_sessions", "save_split", "load_split",
]
