#!/usr/bin/env python3
"""Mass-ingest Claude Code JSONL transcripts for every project under
~/.claude/projects/ that has ≥ MIN_TOOL_USES tool_use blocks.

For each slug folder:
  1. Read the first record bearing a `cwd` field — that's the real path.
  2. Create or look up a Project row by that path.
  3. Run ClaudeCodeJsonlSource with explicit `slug` override so dir names
     containing `.` or mixed case still match disk.
  4. Report rows ingested + secrets flagged.

Idempotent: re-running skips already-ingested tool_use_ids.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from planckbot.db.engine import get_connection
from planckbot.ingest.claude_code import ClaudeCodeJsonlSource
from planckbot.tools.projects import ProjectStore
from planckbot.tools.triples import TriplesStore


MIN_TOOL_USES = 100
CLAUDE_ROOT = Path.home() / ".claude" / "projects"
PROJECT_PREFIX = "-home-kai-Escritorio-PROGRAMACION-"


def count_tool_uses(folder: Path) -> int:
    n = 0
    for jf in folder.glob("*.jsonl"):
        try:
            with jf.open() as f:
                for line in f:
                    if '"type":"tool_use"' in line:
                        n += 1
        except OSError:
            continue
    return n


def find_real_cwd(folder: Path) -> str | None:
    """Walk JSONL files until we find a record with a `cwd` field."""
    for jf in sorted(folder.glob("*.jsonl")):
        try:
            with jf.open() as f:
                for i, line in enumerate(f):
                    if i > 200:  # cap per file before moving on
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    cwd = rec.get("cwd")
                    if cwd:
                        return cwd
        except OSError:
            continue
    return None


def ingest_one(
    folder: Path,
    *,
    db_path: Path,
    limit: int,
    dry_run: bool,
) -> dict:
    slug = folder.name
    pretty_name = slug.removeprefix(PROJECT_PREFIX)
    real_cwd = find_real_cwd(folder)
    if not real_cwd:
        return {"slug": slug, "skipped": "no cwd in jsonl"}

    conn = get_connection(db_path)
    proj_store = ProjectStore(conn)
    triples_store = TriplesStore(conn)

    proj = proj_store.by_path(real_cwd)
    if proj is None:
        if dry_run:
            project_id = "DRY-RUN"
        else:
            proj = proj_store.create(name=pretty_name, path=real_cwd)
            project_id = proj.id
    else:
        project_id = proj.id

    if dry_run:
        return {
            "slug": slug, "name": pretty_name, "cwd": real_cwd,
            "project_id": project_id, "dry_run": True,
        }

    before = triples_store.count_total(project_id=project_id)
    source = ClaudeCodeJsonlSource(
        project_path=real_cwd,
        slug=slug,
    )
    inserted = source.ingest(
        triples_store, limit=limit, project_id=project_id,
    )
    after = triples_store.count_total(project_id=project_id)

    # Count triples flagged with secrets — peek context_data.had_secrets.
    secret_rows = conn.execute(
        "SELECT COUNT(*) FROM triples "
        "WHERE project_id = ? AND context_data LIKE '%\"had_secrets\": true%'",
        (project_id,),
    ).fetchone()[0]

    conn.close()
    return {
        "slug": slug, "name": pretty_name, "cwd": real_cwd,
        "project_id": project_id, "before": before, "inserted": inserted,
        "after": after, "secret_flagged": secret_rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/planckbot.db",
                    help="path to planckbot.db")
    ap.add_argument("--limit", type=int, default=50_000,
                    help="max triples per project per run")
    ap.add_argument("--dry-run", action="store_true",
                    help="only print what would be done")
    ap.add_argument("--min-tool-uses", type=int, default=MIN_TOOL_USES,
                    help="skip slug folders below this tool_use count")
    ap.add_argument("--only", help="ingest only this slug substring (debug)")
    args = ap.parse_args()

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"ERROR: db not found at {db_path}", file=sys.stderr)
        sys.exit(2)

    candidates = sorted([
        f for f in CLAUDE_ROOT.iterdir()
        if f.is_dir() and f.name.startswith(PROJECT_PREFIX)
    ])
    if args.only:
        candidates = [c for c in candidates if args.only in c.name]

    print(f"Scanning {len(candidates)} candidate slug folders "
          f"(min_tool_uses={args.min_tool_uses}, dry_run={args.dry_run})")
    print()

    results = []
    for folder in candidates:
        tu = count_tool_uses(folder)
        if tu < args.min_tool_uses:
            continue
        print(f"-> {folder.name}  (tool_uses≈{tu})")
        try:
            r = ingest_one(
                folder, db_path=db_path,
                limit=args.limit, dry_run=args.dry_run,
            )
            r["tool_uses_estimate"] = tu
            results.append(r)
            if "skipped" in r:
                print(f"   SKIP: {r['skipped']}")
            elif args.dry_run:
                print(f"   would create/use project for cwd={r['cwd']}")
            else:
                print(
                    f"   project={r['name']}  inserted={r['inserted']}  "
                    f"after={r['after']}  secret_flagged={r['secret_flagged']}"
                )
        except Exception as e:
            print(f"   ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            results.append({"slug": folder.name, "error": str(e)})

    # Aggregate
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total_inserted = sum(r.get("inserted", 0) for r in results)
    total_after = sum(r.get("after", 0) for r in results)
    total_flagged = sum(r.get("secret_flagged", 0) for r in results)
    errors = [r for r in results if "error" in r]
    print(f"  projects processed: {len([r for r in results if 'inserted' in r])}")
    print(f"  triples inserted this run: {total_inserted}")
    print(f"  triples now in DB (across processed projects): {total_after}")
    print(f"  triples flagged with secrets: {total_flagged}")
    if errors:
        print(f"  ERRORS: {len(errors)}")
        for e in errors:
            print(f"    {e['slug']}: {e['error']}")


if __name__ == "__main__":
    main()
