"""Smoke-test the real LoRA trainer against the fixture triples.

Usage:
    python scripts/train_smoke.py [fixture_path]

Defaults to data/fixtures/file_search_triples.json. Writes the checkpoint
under data/checkpoints/<uuid>/ and records metrics/experiment to
data/planckbot.db.

This exercises the full data path:
    fixture JSON -> ManualSource -> TriplesStore ->
    build_hf_dataset (tokenized) -> train_lora (PEFT+HF) ->
    adapter saved to disk + checkpoint row in DB
"""

from __future__ import annotations

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


def main(fixture_path: str = "data/fixtures/file_search_triples.json"):
    project_root = Path(__file__).resolve().parent.parent
    fixture = project_root / fixture_path
    if not fixture.exists():
        print(f"fixture not found: {fixture}", file=sys.stderr)
        sys.exit(1)

    print(f"[planckbot] fixture:   {fixture}")
    print(f"[planckbot] db:        {config.db_path}")
    print(f"[planckbot] device:    {config.device}")
    print(f"[planckbot] ram_gb:    {config.ram_gb}")

    conn = get_connection(config.db_path)

    # 1) Ingest fixture into SQLite
    store = TriplesStore(conn)
    before = store.count_total()
    count = ManualSource(fixture).ingest(store)
    print(f"[ingest] loaded {count} triples (db had {before}, now {store.count_total()})")

    triples = store.get_by_tool("file_search", limit=1000)
    if not triples:
        print("no file_search triples found after ingest", file=sys.stderr)
        sys.exit(2)

    # 2) Seed experiment
    mgr = ExperimentManager(conn)
    exp = mgr.create(
        name=f"smoke-{int(time.time())}",
        experiment_type="filter_output",
        hypothesis="SmolLM2-135M can learn to drop node_modules/dist/tests from file_search output",
        config={"tool": "file_search", "strategy": "filter_output"},
    )
    mgr.update_status(exp.id, "running")
    print(f"[exp] created {exp.id} ({exp.name})")

    # 3) Build tokenized dataset
    from transformers import AutoTokenizer
    train_cfg = TrainingConfig(
        num_epochs=1,
        batch_size=2,
        gradient_accumulation_steps=2,
        max_seq_length=256,
        warmup_steps=2,
        output_dir=str(config.data_dir / "checkpoints"),
    )
    tokenizer = AutoTokenizer.from_pretrained(train_cfg.base_model, trust_remote_code=True)
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

    # Give the HF collator the labels=input_ids shape it expects for causal LM.
    train_ds = train_ds.map(lambda ex: {"labels": ex["input_ids"]})
    if eval_ds:
        eval_ds = eval_ds.map(lambda ex: {"labels": ex["input_ids"]})

    # 4) Kick off training (runs in background thread)
    checkpoint_id = train_lora(
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        config=train_cfg,
        experiment_id=exp.id,
        conn=conn,
        tool_name="file_search",
    )
    print(f"[train] kicked off checkpoint_id={checkpoint_id}")

    # 5) Wait for completion (poll training status)
    start = time.time()
    last_step = -1
    while True:
        status = get_training_status()
        if status.current_step != last_step:
            print(
                f"[train] step={status.current_step}/{status.total_steps} "
                f"epoch={status.current_epoch} loss={status.current_loss:.4f} "
                f"best={status.best_loss:.4f} elapsed={int(time.time()-start)}s"
            )
            last_step = status.current_step
        if not status.is_running and status.started_at is not None:
            if status.error:
                print(f"[train] FAILED: {status.error}", file=sys.stderr)
                sys.exit(3)
            break
        if time.time() - start > 600:
            print("[train] timeout 10 min — aborting", file=sys.stderr)
            sys.exit(4)
        time.sleep(2)

    elapsed = time.time() - start
    print(f"[train] done in {elapsed:.1f}s")

    # 6) Verify checkpoint row + on-disk adapter
    ckpt_mgr = CheckpointManager(conn)
    ckpt = ckpt_mgr.get(checkpoint_id)
    if ckpt is None:
        print("checkpoint row missing from DB", file=sys.stderr)
        sys.exit(5)
    print(f"[ckpt] name={ckpt.name}  adapter_path={ckpt.adapter_path}")
    print(f"[ckpt] adapter_size_mb={ckpt.adapter_size_mb}  lora={ckpt.lora_config}")

    adapter_dir = Path(ckpt.adapter_path)
    key_files = ["adapter_config.json", "adapter_model.safetensors"]
    present = [f for f in key_files if (adapter_dir / f).exists()]
    missing = [f for f in key_files if f not in present]
    print(f"[ckpt] files present: {present}")
    if missing:
        print(f"[ckpt] WARNING — missing expected files: {missing}")

    mgr.update_status(exp.id, "completed")
    mgr.record_metrics(exp.id, {
        "final_loss": get_training_status().current_loss,
        "best_loss": get_training_status().best_loss,
        "training_seconds": elapsed,
    })
    print(f"[exp] completed {exp.id}")
    print("[done]")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/fixtures/file_search_triples.json"
    main(path)
