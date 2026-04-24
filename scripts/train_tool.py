"""Train a LoRA adapter for an arbitrary tool from a fixture file.

Generalization of train_smoke.py — accepts --tool + --fixture arguments so
the same code path trains adapters for file_search, list_directory, or
any future tool without duplicating the pipeline.

Usage:
    python scripts/train_tool.py --tool list_directory \
        --fixture data/fixtures/list_directory_triples.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from planckbot.config import config
from planckbot.db.engine import get_connection
from planckbot.experiments.manager import ExperimentManager
from planckbot.ingest import ManualSource
from planckbot.models.checkpoints import CheckpointManager
from planckbot.tools.triples import TriplesStore
from planckbot.training.dataset import build_hf_dataset
from planckbot.training.trainer import (
    TrainingConfig,
    get_training_status,
    train_lora,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True, help="Tool name to train on")
    parser.add_argument("--fixture", required=True, help="Path to fixture JSON")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Mark the resulting checkpoint as the active one for this tool",
    )
    parser.add_argument(
        "--timeout-minutes", type=int, default=30,
        help="Abort if training takes longer than this (default 30 min).",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    fixture = project_root / args.fixture
    if not fixture.exists():
        print(f"fixture not found: {fixture}", file=sys.stderr)
        sys.exit(1)

    print(f"[planckbot] tool:      {args.tool}")
    print(f"[planckbot] fixture:   {fixture}")
    print(f"[planckbot] db:        {config.db_path}")
    print(f"[planckbot] device:    {config.device}")

    conn = get_connection(config.db_path)

    store = TriplesStore(conn)
    before = store.count_total()
    count = ManualSource(fixture).ingest(store)
    print(
        f"[ingest] loaded {count} triples "
        f"(db had {before}, now {store.count_total()})"
    )

    triples = store.get_by_tool(args.tool, limit=1000)
    if not triples:
        print(f"no {args.tool} triples found after ingest", file=sys.stderr)
        sys.exit(2)

    mgr = ExperimentManager(conn)
    exp = mgr.create(
        name=f"train-{args.tool}-{int(time.time())}",
        experiment_type="filter_output",
        hypothesis=(
            f"SmolLM2-135M+LoRA can learn a compression rule for {args.tool} output"
        ),
        config={"tool": args.tool, "strategy": "filter_output"},
    )
    mgr.update_status(exp.id, "running")
    print(f"[exp] created {exp.id} ({exp.name})")

    from transformers import AutoTokenizer
    train_cfg = TrainingConfig(
        num_epochs=args.epochs,
        batch_size=2,
        gradient_accumulation_steps=2,
        max_seq_length=256,
        warmup_steps=2,
        output_dir=str(config.data_dir / "checkpoints"),
    )
    tokenizer = AutoTokenizer.from_pretrained(
        train_cfg.base_model, trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    train_ds, eval_ds = build_hf_dataset(
        triples,
        strategy="filter_output",
        tokenizer=tokenizer,
        max_length=train_cfg.max_seq_length,
        test_size=0.2,
    )
    print(f"[data] train={len(train_ds)} eval={len(eval_ds) if eval_ds else 0}")

    train_ds = train_ds.map(lambda ex: {"labels": ex["input_ids"]})
    if eval_ds:
        eval_ds = eval_ds.map(lambda ex: {"labels": ex["input_ids"]})

    checkpoint_id = train_lora(
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        config=train_cfg,
        experiment_id=exp.id,
        conn=conn,
        tool_name=args.tool,
    )
    print(f"[train] kicked off checkpoint_id={checkpoint_id}")

    start = time.time()
    last_step = -1
    timeout_s = args.timeout_minutes * 60
    while True:
        status = get_training_status()
        if status.current_step != last_step:
            print(
                f"[train] step={status.current_step}/{status.total_steps} "
                f"epoch={status.current_epoch} loss={status.current_loss:.4f} "
                f"best={status.best_loss:.4f} elapsed={int(time.time()-start)}s",
                flush=True,
            )
            last_step = status.current_step
        if not status.is_running and status.started_at is not None:
            if status.error:
                print(f"[train] FAILED: {status.error}", file=sys.stderr)
                sys.exit(3)
            break
        if time.time() - start > timeout_s:
            print(f"[train] timeout {args.timeout_minutes} min — aborting",
                  file=sys.stderr)
            sys.exit(4)
        time.sleep(2)

    elapsed = time.time() - start
    print(f"[train] done in {elapsed:.1f}s")

    ckpt_mgr = CheckpointManager(conn)
    ckpt = ckpt_mgr.get(checkpoint_id)
    if ckpt is None:
        print("checkpoint row missing from DB", file=sys.stderr)
        sys.exit(5)
    print(f"[ckpt] name={ckpt.name}  adapter_path={ckpt.adapter_path}")
    print(f"[ckpt] adapter_size_mb={ckpt.adapter_size_mb}")

    if args.activate:
        # As of schema v5, a freshly-trained checkpoint is NOT automatically
        # trustworthy. The training loop tells you nothing about whether the
        # adapter compresses or regresses — you have to run proxy_demo and
        # check. So we bless + activate ONLY when the operator explicitly
        # asked for --activate, and we mark the event loud so they don't
        # confuse "trained" with "safe to serve".
        ckpt_mgr.bless(checkpoint_id)
        ckpt_mgr.activate(checkpoint_id)
        print(f"[ckpt] ⚠ blessed + activated {checkpoint_id} — "
              "YOU asserted it compresses. If proxy_demo shows regression, "
              "run `planckbot unbless {checkpoint_id[:8]}` immediately.")

    mgr.update_status(exp.id, "completed")
    mgr.record_metrics(exp.id, {
        "final_loss": get_training_status().current_loss,
        "best_loss": get_training_status().best_loss,
        "training_seconds": elapsed,
    })
    print(f"[exp] completed {exp.id}")
    print("[done]")


if __name__ == "__main__":
    main()
