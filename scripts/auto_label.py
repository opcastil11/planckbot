"""Back-fill `filtered_output` on unlabeled triples from a reference text.

This is the manual side of Layer B's "reference-tracking signal" — you paste
(or pipe in) the host-LLM message that quoted the tool output, and this
script marks the matching lines as `filtered_output` on the most recent
unlabeled triples for a given tool.

Usage:
    # from stdin
    xclip -selection clipboard -o | python scripts/auto_label.py --tool list_directory --recent 5

    # from a file
    python scripts/auto_label.py --tool file_search --reference msg.txt --recent 3

    # preview without writing
    python scripts/auto_label.py --tool list_directory --reference msg.txt --recent 5 --dry-run
"""

from __future__ import annotations

import argparse
import sys

from planckbot.config import config
from planckbot.db.engine import get_connection
from planckbot.experiments.metrics import count_tokens_approx
from planckbot.ingest.reference_tracker import (
    extract_referenced_lines,
    label_triple_from_reference,
)
from planckbot.tools.triples import TriplesStore


def _load_reference(args) -> str:
    if args.reference and args.reference != "-":
        with open(args.reference) as f:
            return f.read()
    return sys.stdin.read()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tool", required=True,
                   help="Tool name to auto-label triples for.")
    p.add_argument("--recent", type=int, default=5,
                   help="How many most-recent unlabeled triples to process.")
    p.add_argument("--reference",
                   help="Path to a file with the reference text. "
                        "Use '-' or omit to read stdin.")
    p.add_argument("--min-line-len", type=int, default=3,
                   help="Ignore lines shorter than this (noise filter).")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be labeled; do not write.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing filtered_output values.")
    args = p.parse_args()

    reference = _load_reference(args)
    if not reference.strip():
        print("reference text is empty — nothing to match", file=sys.stderr)
        sys.exit(1)

    conn = get_connection(config.db_path)
    store = TriplesStore(conn)

    if args.force:
        triples = store.get_by_tool(args.tool, limit=args.recent)
    else:
        triples = store.list_unlabeled(tool_name=args.tool, limit=args.recent)

    if not triples:
        print(f"no {'unlabeled ' if not args.force else ''}"
              f"triples for tool={args.tool!r}")
        return

    print(f"[auto-label] tool={args.tool} candidates={len(triples)} "
          f"ref_chars={len(reference)} dry={args.dry_run}")

    labeled = skipped = 0
    for t in triples:
        kept = extract_referenced_lines(
            t.output_data, reference, min_line_len=args.min_line_len
        )
        raw_tok = count_tokens_approx(t.output_data)
        if kept is None:
            print(f"  · {t.id[:8]}  raw={raw_tok:>4} tok  → no match, skipping")
            skipped += 1
            continue

        kept_tok = count_tokens_approx(kept)
        saved_pct = (1 - kept_tok / raw_tok) * 100 if raw_tok else 0
        marker = "[dry]" if args.dry_run else "[save]"
        print(f"  {marker} {t.id[:8]}  raw={raw_tok:>4}  "
              f"kept={kept_tok:>4}  saved={saved_pct:+5.1f}%")

        if not args.dry_run:
            label_triple_from_reference(
                store, t.id, reference,
                min_line_len=args.min_line_len,
                force=args.force,
            )
            labeled += 1

    print(f"[auto-label] done — labeled={labeled} "
          f"skipped(no-match)={skipped} candidates={len(triples)}")


if __name__ == "__main__":
    main()
