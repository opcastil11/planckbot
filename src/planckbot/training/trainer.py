"""LoRA training loop with real-time metrics callback."""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from planckbot.db.models import MetricRecord, ModelCheckpoint, _new_id, _now


@dataclass
class TrainingConfig:
    base_model: str = "HuggingFaceTB/SmolLM2-135M-Instruct"
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    lora_target_modules: list[str] = field(default_factory=lambda: ["q_proj", "v_proj"])
    learning_rate: float = 1e-4
    num_epochs: int = 3
    batch_size: int = 4
    max_seq_length: int = 512
    gradient_accumulation_steps: int = 4
    warmup_steps: int = 10
    weight_decay: float = 0.01
    device: str = "cpu"
    output_dir: str = "data/checkpoints"
    fp16: bool = False


@dataclass
class TrainingStatus:
    is_running: bool = False
    experiment_id: str | None = None
    current_epoch: int = 0
    current_step: int = 0
    total_steps: int = 0
    current_loss: float = 0.0
    best_loss: float = float("inf")
    started_at: str | None = None
    error: str | None = None


# Global training status
_training_status = TrainingStatus()


def get_training_status() -> TrainingStatus:
    return _training_status


class MetricsCallback:
    """Callback that records metrics to DB during training."""

    def __init__(self, conn: sqlite3.Connection, experiment_id: str):
        self.conn = conn
        self.experiment_id = experiment_id

    def on_log(self, step: int, logs: dict):
        loss = logs.get("loss", logs.get("train_loss"))
        if loss is not None:
            rec = MetricRecord(
                experiment_id=self.experiment_id,
                metric_name="train_loss",
                metric_value=float(loss),
                step=step,
            )
            row = rec.to_row()
            cols = ", ".join(row.keys())
            ph = ", ".join("?" for _ in row)
            try:
                self.conn.execute(
                    f"INSERT INTO metrics_history ({cols}) VALUES ({ph})",
                    list(row.values()),
                )
                self.conn.commit()
            except Exception:
                pass

        _training_status.current_step = step
        _training_status.current_loss = float(loss) if loss else 0.0
        if loss and loss < _training_status.best_loss:
            _training_status.best_loss = float(loss)


def train_lora(
    train_dataset,
    eval_dataset,
    config: TrainingConfig,
    experiment_id: str,
    conn: sqlite3.Connection,
    tool_name: str = "",
    on_complete: Callable | None = None,
) -> str:
    """Run LoRA fine-tuning in a background thread. Returns checkpoint ID."""
    checkpoint_id = _new_id()

    def _train():
        global _training_status
        _training_status = TrainingStatus(
            is_running=True,
            experiment_id=experiment_id,
            started_at=_now(),
        )

        try:
            from transformers import (
                AutoTokenizer, AutoModelForCausalLM,
                TrainingArguments, Trainer, TrainerCallback,
            )
            from peft import LoraConfig, get_peft_model, TaskType
            import torch

            # Load model
            tokenizer = AutoTokenizer.from_pretrained(
                config.base_model, trust_remote_code=True
            )
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            model = AutoModelForCausalLM.from_pretrained(
                config.base_model, trust_remote_code=True
            )

            # Apply LoRA
            lora_config = LoraConfig(
                r=config.lora_rank,
                lora_alpha=config.lora_alpha,
                lora_dropout=config.lora_dropout,
                target_modules=config.lora_target_modules,
                task_type=TaskType.CAUSAL_LM,
            )
            model = get_peft_model(model, lora_config)
            model.print_trainable_parameters()

            # Output dir
            output_path = Path(config.output_dir) / checkpoint_id
            output_path.mkdir(parents=True, exist_ok=True)

            # Metrics callback
            metrics_cb = MetricsCallback(conn, experiment_id)

            class HFMetricsCallback(TrainerCallback):
                def on_log(self, args, state, control, logs=None, **kwargs):
                    if logs and state:
                        metrics_cb.on_log(state.global_step, logs)
                        _training_status.current_epoch = int(state.epoch) if state.epoch else 0

            # Training args
            training_args = TrainingArguments(
                output_dir=str(output_path),
                num_train_epochs=config.num_epochs,
                per_device_train_batch_size=config.batch_size,
                gradient_accumulation_steps=config.gradient_accumulation_steps,
                learning_rate=config.learning_rate,
                warmup_steps=config.warmup_steps,
                weight_decay=config.weight_decay,
                logging_steps=1,
                save_strategy="epoch",
                eval_strategy="epoch" if eval_dataset else "no",
                fp16=config.fp16 and torch.cuda.is_available(),
                report_to="none",
                dataloader_pin_memory=False,
            )

            trainer = Trainer(
                model=model,
                args=training_args,
                train_dataset=train_dataset,
                eval_dataset=eval_dataset,
                callbacks=[HFMetricsCallback()],
            )

            _training_status.total_steps = len(train_dataset) * config.num_epochs // (
                config.batch_size * config.gradient_accumulation_steps
            )

            trainer.train()

            # Save adapter
            model.save_pretrained(str(output_path))
            tokenizer.save_pretrained(str(output_path))

            # Calculate adapter size
            adapter_size = sum(
                f.stat().st_size for f in output_path.rglob("*") if f.is_file()
            ) / (1024 * 1024)

            # Save checkpoint record
            ckpt = ModelCheckpoint(
                id=checkpoint_id,
                name=f"{tool_name}_{config.base_model.split('/')[-1]}",
                base_model=config.base_model,
                adapter_path=str(output_path),
                adapter_size_mb=round(adapter_size, 2),
                experiment_id=experiment_id,
                strategy="filter_output",
                tool_name=tool_name,
                lora_config={
                    "r": config.lora_rank,
                    "alpha": config.lora_alpha,
                    "dropout": config.lora_dropout,
                    "target_modules": config.lora_target_modules,
                },
                training_args={
                    "lr": config.learning_rate,
                    "epochs": config.num_epochs,
                    "batch_size": config.batch_size,
                },
                num_triples=len(train_dataset),
            )
            row = ckpt.to_row()
            cols = ", ".join(row.keys())
            ph = ", ".join("?" for _ in row)
            conn.execute(
                f"INSERT INTO model_checkpoints ({cols}) VALUES ({ph})",
                list(row.values()),
            )
            conn.commit()

            _training_status.is_running = False

            if on_complete:
                on_complete(checkpoint_id)

        except Exception as e:
            _training_status.is_running = False
            _training_status.error = str(e)
            if on_complete:
                on_complete(None)

    thread = threading.Thread(target=_train, daemon=True)
    thread.start()
    return checkpoint_id
