"""End-to-end experiment orchestrator: train -> evaluate -> log."""

import json
import sqlite3
from datetime import datetime, timezone

from planckbot.db.models import ModelCheckpoint, _now
from planckbot.experiments.manager import ExperimentManager
from planckbot.experiments.metrics import compute_token_savings
from planckbot.models.checkpoints import CheckpointManager
from planckbot.paper.log import PaperLog
from planckbot.tools.triples import TriplesStore
from planckbot.training.dataset import build_hf_dataset
from planckbot.training.trainer import TrainingConfig, train_lora


def run_experiment(
    conn: sqlite3.Connection,
    experiment_id: str,
    training_config: TrainingConfig | None = None,
    on_complete=None,
) -> str | None:
    """
    Run a full experiment: prepare data -> train -> evaluate -> log.
    Returns checkpoint_id or None on failure.
    """
    exp_mgr = ExperimentManager(conn)
    triples_store = TriplesStore(conn)
    paper_log = PaperLog(conn)

    exp = exp_mgr.get(experiment_id)
    if not exp:
        return None

    cfg = exp.config or {}
    tool_name = cfg.get("tool_name", "")
    strategy = cfg.get("strategy", "filter_output")

    # Get triples for training
    if tool_name:
        triples = triples_store.get_by_tool(tool_name)
    else:
        triples = triples_store.list_all(limit=1000)

    if not triples:
        exp_mgr.update_status(experiment_id, "failed")
        exp_mgr.update_observations(experiment_id, "No triples available for training.")
        return None

    # Update status
    exp_mgr.update_status(experiment_id, "running")

    # Build config
    if training_config is None:
        training_config = TrainingConfig(
            base_model=cfg.get("model_name", "HuggingFaceTB/SmolLM2-135M-Instruct"),
            lora_rank=cfg.get("lora_rank", 8),
            lora_alpha=cfg.get("lora_alpha", 16),
            learning_rate=cfg.get("learning_rate", 1e-4),
            num_epochs=cfg.get("epochs", 3),
            batch_size=cfg.get("batch_size", 4),
            max_seq_length=cfg.get("max_seq_length", 512),
            device=cfg.get("device", "cpu"),
        )

    # Tokenize
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(
        training_config.base_model, trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    train_ds, eval_ds = build_hf_dataset(
        triples, strategy, tokenizer, training_config.max_seq_length
    )

    def _on_training_complete(checkpoint_id):
        if checkpoint_id:
            # Record metrics
            ckpt_mgr = CheckpointManager(conn)
            ckpt = ckpt_mgr.get(checkpoint_id)

            metrics = {
                "num_triples": len(triples),
                "checkpoint_id": checkpoint_id,
            }

            if ckpt and ckpt.eval_metrics:
                metrics.update(ckpt.eval_metrics)

            exp_mgr.record_metrics(experiment_id, metrics)
            exp_mgr.update_checkpoint(experiment_id, checkpoint_id)
            exp_mgr.update_status(experiment_id, "completed")

            # Auto paper log
            refreshed = exp_mgr.get(experiment_id)
            if refreshed:
                paper_log.auto_log_experiment_result(refreshed)
        else:
            exp_mgr.update_status(experiment_id, "failed")

        if on_complete:
            on_complete(checkpoint_id)

    checkpoint_id = train_lora(
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        config=training_config,
        experiment_id=experiment_id,
        conn=conn,
        tool_name=tool_name,
        on_complete=_on_training_complete,
    )

    return checkpoint_id
