#!/usr/bin/env python3
"""Tier-1 bench runner: drives all 12 techniques over real triples.

Reads `data/planckbot.db`, builds a stratified holdout split, runs every
technique, and writes:
- `data/bench/tier1_events.csv`     — one row per (technique, triple)
- `data/bench/tier1_aggregate.csv`  — per-technique × slice (ALL/project/tool)
- `data/bench/tier1_ranking.csv`    — top-level technique ranking
Plus prints a clean ranking table to stdout for quick read.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from planckbot.bench import (
    load_triples, group_into_sessions, replay,
    aggregate, write_results_csv,
    split_sessions, save_split,
)
from planckbot.bench.metrics import tool_volume_summary
from planckbot.bench.references import load_references_for_project
from planckbot.bench.techniques import (
    all_triples_only_techniques, jsonl_techniques,
)
from planckbot.db.engine import get_connection
from planckbot.tools.projects import ProjectStore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/planckbot.db")
    ap.add_argument("--out-dir", default="data/bench")
    ap.add_argument("--holdout-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--full", action="store_true",
        help="Run on full dataset, not just holdout (more honest at higher cost)",
    )
    ap.add_argument(
        "--no-jsonl", action="store_true",
        help="Skip JSONL-dependent techniques (L-jsonl, T)",
    )
    args = ap.parse_args()

    db_path = Path(args.db).expanduser()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    proj_store = ProjectStore(conn)
    projects = proj_store.list_all()
    print(f"DB: {db_path}  projects={len(projects)}")

    # Build / persist split
    split = split_sessions(
        conn, holdout_frac=args.holdout_frac, seed=args.seed,
    )
    save_split(split, out_dir / "split.json")
    target_sessions = (
        None if args.full else split.holdout_session_ids
    )
    print(
        f"Split: train={len(split.train_session_ids)} "
        f"holdout={len(split.holdout_session_ids)}  "
        f"running on={'FULL dataset' if args.full else 'HOLDOUT'}"
    )

    # Load triples (optionally filtered by holdout sessions)
    triples = load_triples(conn, session_ids=target_sessions)
    sessions = group_into_sessions(triples)
    print(f"Sessions in scope: {len(sessions)}  triples: {len(triples)}")

    # Baseline: tool volume in scope
    tools_in_scope = defaultdict(lambda: {"n": 0, "tokens": 0})
    for s in sessions:
        for ev in s.events:
            tools_in_scope[ev.triple.tool_name]["n"] += 1
            tools_in_scope[ev.triple.tool_name]["tokens"] += (
                ev.triple.output_tokens or 0
            )
    total_output_tokens = sum(v["tokens"] for v in tools_in_scope.values())
    print(f"Total output tokens in scope: {total_output_tokens:,}")
    print()

    # --- Run triples-only techniques ---
    all_event_rows: list[dict] = []
    all_agg: list = []

    print("=== Triples-only techniques ===")
    triples_techs = all_triples_only_techniques()
    for tech in triples_techs:
        if hasattr(tech, "calibrate"):
            tech.calibrate(sessions)
        rows = replay(tech, sessions)
        agg = aggregate(rows)
        all_event_rows.extend(rows)
        all_agg.extend(agg)
        overall = next(r for r in agg if r.slice_kind == "overall")
        print(
            f"  {tech.name:<32} affected={overall.affected_pct:>5.1f}%  "
            f"saved={overall.tokens_saved:>9,}  "
            f"risk={overall.tokens_at_risk:>9,}  "
            f"% of scope={100*overall.tokens_saved/max(total_output_tokens,1):>5.2f}%"
        )

    # --- JSONL-based techniques ---
    if not args.no_jsonl:
        print()
        print("=== JSONL-based techniques (loading references...) ===")
        # Build references dict by walking each project's JSONL folder.
        refs: dict[str, str] = {}
        for proj in projects:
            slug = "-" + proj.path.lstrip("/").replace("/", "-")
            proj_refs = load_references_for_project(
                Path.home() / ".claude" / "projects", slug,
            )
            refs.update(proj_refs)
        print(f"  Loaded {len(refs):,} tool_use_id → reference mappings")
        for tech in jsonl_techniques(refs):
            rows = replay(tech, sessions)
            agg = aggregate(rows)
            all_event_rows.extend(rows)
            all_agg.extend(agg)
            overall = next(r for r in agg if r.slice_kind == "overall")
            print(
                f"  {tech.name:<32} affected={overall.affected_pct:>5.1f}%  "
                f"saved={overall.tokens_saved:>9,}  "
                f"risk={overall.tokens_at_risk:>9,}  "
                f"% of scope={100*overall.tokens_saved/max(total_output_tokens,1):>5.2f}%"
            )

    # --- Persist ---
    write_results_csv(all_event_rows, out_dir / "tier1_events.csv")
    write_results_csv(all_agg, out_dir / "tier1_aggregate.csv")
    # Compact ranking
    ranking_rows = []
    for r in all_agg:
        if r.slice_kind == "overall":
            ranking_rows.append({
                "technique": r.technique,
                "triples": r.triples,
                "affected_pct": round(r.affected_pct, 2),
                "tokens_saved": r.tokens_saved,
                "tokens_at_risk": r.tokens_at_risk,
                "pct_of_scope": round(
                    100 * r.tokens_saved / max(total_output_tokens, 1), 2
                ),
                "risk_pct": round(r.risk_pct, 2),
            })
    ranking_rows.sort(key=lambda x: -x["tokens_saved"])
    write_results_csv(ranking_rows, out_dir / "tier1_ranking.csv")

    # --- Final stdout summary ---
    print()
    print("=" * 80)
    print(f"RANKING (sorted by tokens_saved, scope total = {total_output_tokens:,})")
    print("=" * 80)
    print(f"{'#':>2}  {'technique':<32} {'saved':>11} {'%scope':>7} "
          f"{'affect%':>8} {'risk%':>7}")
    for i, r in enumerate(ranking_rows, 1):
        print(
            f"{i:>2}  {r['technique']:<32} {r['tokens_saved']:>11,} "
            f"{r['pct_of_scope']:>6.2f}% {r['affected_pct']:>7.1f}% "
            f"{r['risk_pct']:>6.1f}%"
        )

    print()
    print(f"Output written to: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
