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
    blessed: int = 0                  # v5: safety gate — set with `planckbot bless`
    tuned_threshold: float | None = None  # v5: per-ckpt confidence override
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
class CronJob:
    """A scheduled background job (auto-label, retrain, …).

    Rows live in `cron_jobs`. The daemon polls for rows where `enabled = 1`
    and `next_run_at <= now`, dispatches by `job_type`, and updates
    `last_run_at` / `next_run_at` / `last_status` / `last_output` based on
    the outcome. See docs/PLANCKBOT_CONCEPT.md §"Automated loop".
    """
    id: str = field(default_factory=_new_id)
    name: str = ""
    job_type: str = ""            # 'autolabel' | 'retrain' | 'noop'
    params: dict = field(default_factory=dict)
    interval_seconds: int = 300
    enabled: int = 1
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_status: str | None = None   # 'ok' | 'error'
    last_output: str | None = None
    created_at: str = field(default_factory=_now)

    def to_row(self) -> dict:
        d = asdict(self)
        d["params"] = json.dumps(d["params"] or {})
        return d

    @classmethod
    def from_row(cls, row) -> "CronJob":
        d = dict(row)
        d["params"] = json.loads(d["params"]) if d["params"] else {}
        return cls(**d)


@dataclass
class GapReport:
    """Output of the Layer D pattern detector.

    A gap report says "the sequence of tools X → Y → Z repeats often enough
    that a single tool merging them would save tokens". It holds pointers
    into the `triples` table so the synthesizer can inspect concrete
    examples when deciding what code to produce.
    """
    id: str = field(default_factory=_new_id)
    tool_sequence: list[str] = field(default_factory=list)
    occurrences: int = 0
    example_triple_ids: list[str] = field(default_factory=list)
    proposed_name: str | None = None
    proposed_description: str | None = None
    status: str = "open"          # open | accepted | rejected
    created_at: str = field(default_factory=_now)

    def to_row(self) -> dict:
        d = asdict(self)
        d["tool_sequence"] = json.dumps(d["tool_sequence"])
        d["example_triple_ids"] = json.dumps(d["example_triple_ids"])
        return d

    @classmethod
    def from_row(cls, row) -> "GapReport":
        d = dict(row)
        d["tool_sequence"] = json.loads(d["tool_sequence"]) if d["tool_sequence"] else []
        d["example_triple_ids"] = (
            json.loads(d["example_triple_ids"]) if d["example_triple_ids"] else []
        )
        return cls(**d)


@dataclass
class SynthesizedTool:
    """A tool whose code was generated from usage patterns, not hand-written.

    Exposed to Claude Code through the `planckbot-synth` MCP server. Starts
    as `status='draft'` (in the DB but not served); `activate(name)` flips
    it to `active` and signals the MCP server to reload.
    """
    id: str = field(default_factory=_new_id)
    name: str = ""
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    code: str = ""
    source_file_path: str | None = None
    status: str = "draft"          # draft | active | retired
    created_at: str = field(default_factory=_now)
    created_by: str | None = None
    gap_report_id: str | None = None

    def to_row(self) -> dict:
        d = asdict(self)
        d["input_schema"] = json.dumps(d["input_schema"] or {})
        return d

    @classmethod
    def from_row(cls, row) -> "SynthesizedTool":
        d = dict(row)
        d["input_schema"] = json.loads(d["input_schema"]) if d["input_schema"] else {}
        return cls(**d)


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
