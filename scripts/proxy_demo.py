"""End-to-end proof that the proxy + adapter + tool loop actually runs.

Loads the most-recent active adapter for `file_search` out of the DB, wires it
through `PlanckProxy`, calls a dummy `file_search` tool a few times, and prints
before/after token counts + confidence.

Note on results: with today's 16-example / 1-epoch smoke training, the adapter
has barely learned anything — this script is a *plumbing* test, not a
performance benchmark. The point is to demonstrate the full path:
    tool call → proxy → load adapter → generate prediction with confidence
              → decide (pass-through vs swap) → record triple → return.

Usage:
    python scripts/proxy_demo.py [checkpoint_id]

Without an argument, uses the most recent checkpoint for `file_search`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import tiktoken

from planckbot.config import config
from planckbot.db.engine import get_connection
from planckbot.models.checkpoints import CheckpointManager
from planckbot.models.inference import format_prompt, predict
from planckbot.models.loader import load_model
from planckbot.proxy import InterveneMode, PlanckProxy, SuggestMode
from planckbot.tools.triples import TriplesStore


STRATEGY = "filter_output"


def _fake_search(query: str) -> str:
    """Stand-in for a real file_search tool, mirroring the fixture shape."""
    return (
        f"src/{query}/core.py\n"
        f"src/{query}/handlers.py\n"
        f"src/tests/test_{query}.py\n"
        f"docs/{query}.md\n"
        f"dist/{query}.min.js\n"
        f"node_modules/@corp/{query}/index.js\n"
        f"node_modules/@corp/{query}/package.json\n"
        f".git/objects/{query[:2]}/deadbeef"
    )


def _count_tokens(text: str) -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def _pick_checkpoint(ckpt_mgr: CheckpointManager, explicit_id: str | None):
    if explicit_id:
        c = ckpt_mgr.get(explicit_id)
        if c is None:
            print(f"no checkpoint with id={explicit_id}", file=sys.stderr)
            sys.exit(1)
        return c
    # Pick the most recent file_search checkpoint
    for c in sorted(ckpt_mgr.list_all(tool_name="file_search"),
                    key=lambda x: x.created_at, reverse=True):
        return c
    # Fallback: any checkpoint
    all_ = ckpt_mgr.list_all()
    if not all_:
        print("no checkpoints in the DB — run scripts/train_smoke.py first",
              file=sys.stderr)
        sys.exit(2)
    return sorted(all_, key=lambda x: x.created_at, reverse=True)[0]


def main(explicit_checkpoint_id: str | None = None):
    conn = get_connection(config.db_path)
    store = TriplesStore(conn)
    ckpt_mgr = CheckpointManager(conn)

    ckpt = _pick_checkpoint(ckpt_mgr, explicit_checkpoint_id)
    print(f"[demo] using checkpoint {ckpt.id} — {ckpt.name}")
    print(f"[demo] adapter: {ckpt.adapter_path} ({ckpt.adapter_size_mb} MB)")
    print(f"[demo] base model: {ckpt.base_model}")
    print(f"[demo] trained on: {ckpt.num_triples} triples")

    print("[demo] loading model + adapter (this can take ~20s on CPU)…")
    loaded = load_model(
        model_name=ckpt.base_model,
        device=config.device if config.device in ("cpu", "cuda") else "cpu",
        adapter_path=ckpt.adapter_path,
    )
    print(f"[demo] loaded in {loaded.load_time_s}s ({loaded.memory_mb} MB)")

    def predictor(tool_name: str, input_str: str, output_str: str, strategy: str):
        """Run the adapter on (tool_name, input, output) and return
        (predicted_text, confidence)."""
        prompt = format_prompt(tool_name, output_str, strategy)
        result = predict(
            loaded.model, loaded.tokenizer, prompt,
            max_new_tokens=128,
            device=loaded.device,
        )
        return result.output_text.strip(), result.confidence

    queries = ["auth", "billing", "notifications"]

    # First pass: SuggestMode (log what the adapter would do, don't swap)
    print("\n=== Pass 1: SuggestMode (observe-only, see what the adapter proposes) ===")
    suggest_proxy = PlanckProxy(
        store,
        mode=SuggestMode(confidence_threshold=0.90),
        predictor=predictor,
        strategy=STRATEGY,
    )
    search = suggest_proxy.wrap(_fake_search, tool_name="file_search")
    for q in queries:
        r = search(query=q)
        raw_tok = _count_tokens(r.raw_output)
        pred_tok = _count_tokens(r.predicted_output or "")
        print(f"  query={q!r:20}  raw={raw_tok:>4} tok  "
              f"predicted={pred_tok:>4} tok  conf={r.confidence:.3f}  "
              f"intervened={r.intervened}")

    # Second pass: InterveneMode with a low-threshold just to prove the swap
    # happens in the happy path. Do NOT run this way in real life without
    # trustworthy confidence.
    print("\n=== Pass 2: InterveneMode (threshold=0.0, forces swap to show the path) ===")
    intervene_proxy = PlanckProxy(
        store,
        mode=InterveneMode(confidence_threshold=0.0),
        predictor=predictor,
        strategy=STRATEGY,
    )
    search = intervene_proxy.wrap(_fake_search, tool_name="file_search")
    total_raw = total_final = 0
    for q in queries:
        r = search(query=q)
        raw_tok = _count_tokens(r.raw_output)
        final_tok = _count_tokens(r.final_output)
        total_raw += raw_tok
        total_final += final_tok
        savings_pct = (1 - final_tok / raw_tok) * 100 if raw_tok else 0
        print(f"  query={q!r:20}  raw={raw_tok:>4}  final={final_tok:>4}  "
              f"saved={savings_pct:+6.1f}%  conf={r.confidence:.3f}  "
              f"intervened={r.intervened}")

    if total_raw:
        overall_saving = (1 - total_final / total_raw) * 100
        print(f"\n[total] raw={total_raw}  final={total_final}  "
              f"saved={overall_saving:+.1f}%")
    print(f"[db] triples stored: {store.count_total()} "
          f"(sources: {store.count_by_tool()})")
    print("[done]")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(arg)
