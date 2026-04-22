"""Dataclasses mirroring DB tables."""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Experiment:
    id: str = field(default_factory=_new_id)
    name: str = ""
    description: str = ""
    hypothesis: str = ""
    experiment_type: str = "filter_output"
    status: str = "planned"
    config: dict = field(default_factory=dict)
    random_seed: int | None = None
    data_snapshot: str | None = None
    checkpoint_id: str | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    completed_at: str | None = None
    metrics: dict | None = None
    observations: str | None = None

    def to_row(self) -> dict:
        d = asdict(self)
        d["config"] = json.dumps(d["config"])
        d["metrics"] = json.dumps(d["metrics"]) if d["metrics"] else None
        return d

    @classmethod
    def from_row(cls, row) -> "Experiment":
        d = dict(row)
        d["config"] = json.loads(d["config"]) if d["config"] else {}
        d["metrics"] = json.loads(d["metrics"]) if d["metrics"] else None
        return cls(**d)


@dataclass
class Triple:
    id: str = field(default_factory=_new_id)
    tool_name: str = ""
    session_id: str | None = None
    input_data: str = "{}"
    context_data: str | None = None
    output_data: str = "{}"
    input_tokens: int | None = None
    output_tokens: int | None = None
    filtered_output: str | None = None
    filtered_tokens: int | None = None
    source: str = "manual"
    created_at: str = field(default_factory=_now)
    experiment_id: str | None = None
    tool_version_id: str | None = None  # v2: co-evolution tracking

    def to_row(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row) -> "Triple":
        return cls(**dict(row))


@dataclass
class ModelCheckpoint:
    id: str = field(default_factory=_new_id)
    name: str = ""
    base_model: str = ""
    model_size_mb: float | None = None
    adapter_path: str | None = None
    adapter_size_mb: float | None = None
    experiment_id: str | None = None
    strategy: str | None = None
    tool_name: str | None = None
    tool_version_id: str | None = None  # v2: which tool revision this was trained on
    lora_config: dict | None = None
    training_args: dict | None = None
    num_triples: int | None = None
    eval_metrics: dict | None = None
    is_active: int = 0
    created_at: str = field(default_factory=_now)

    def to_row(self) -> dict:
        d = asdict(self)
        d["lora_config"] = json.dumps(d["lora_config"]) if d["lora_config"] else None
        d["training_args"] = json.dumps(d["training_args"]) if d["training_args"] else None
        d["eval_metrics"] = json.dumps(d["eval_metrics"]) if d["eval_metrics"] else None
        return d

    @classmethod
    def from_row(cls, row) -> "ModelCheckpoint":
        d = dict(row)
        d["lora_config"] = json.loads(d["lora_config"]) if d["lora_config"] else None
        d["training_args"] = json.loads(d["training_args"]) if d["training_args"] else None
        d["eval_metrics"] = json.loads(d["eval_metrics"]) if d["eval_metrics"] else None
        return cls(**d)


@dataclass
class PaperEntry:
    id: str = field(default_factory=_new_id)
    experiment_id: str | None = None
    entry_type: str = "observation"
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str | None = None

    def to_row(self) -> dict:
        d = asdict(self)
        d["tags"] = json.dumps(d["tags"])
        return d

    @classmethod
    def from_row(cls, row) -> "PaperEntry":
        d = dict(row)
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        return cls(**d)


@dataclass
class MetricRecord:
    id: str = field(default_factory=_new_id)
    experiment_id: str = ""
    recorded_at: str = field(default_factory=_now)
    metric_name: str = ""
    metric_value: float = 0.0
    step: int | None = None
    metadata: str | None = None

    def to_row(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row) -> "MetricRecord":
        return cls(**dict(row))


@dataclass
class ToolVersion:
    """Immutable snapshot of a tool's source, identified by its hash.

    Every observation and every trained adapter carries the id of the
    ToolVersion it was recorded/trained against, so the proxy never applies
    an adapter to a tool revision it wasn't trained on.
    See docs/PLANCKBOT_CONCEPT.md §7.
    """
    id: str = field(default_factory=_new_id)
    tool_name: str = ""
    code_hash: str = ""       # sha256 of the tool source
    source: str | None = None  # optional verbatim source
    created_at: str = field(default_factory=_now)
    created_by: str | None = None  # 'human' | 'prompt:<id>' | 'agent:<name>'
    parent_version_id: str | None = None
    diff_from_parent: str | None = None

    def to_row(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row) -> "ToolVersion":
        return cls(**dict(row))


@dataclass
class AgentEvent:
    """Tracks Planck agent lifecycle events for the real-time tree."""
    id: str = field(default_factory=_new_id)
    tool_name: str = ""
    event_type: str = ""  # 'registered' | 'observing' | 'collecting' | 'training' | 'evaluating' | 'deployed' | 'error'
    timestamp: str = field(default_factory=_now)
    details: str | None = None
    session_id: str | None = None

    def to_row(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row) -> "AgentEvent":
        return cls(**dict(row))
