"""Tests for training infrastructure (mock model tests)."""

from unittest.mock import MagicMock, patch

from planckbot.training.dataset import build_training_texts, build_hf_dataset
from planckbot.training.trainer import TrainingConfig, TrainingStatus, get_training_status
from planckbot.training.evaluator import EvalResult
from planckbot.models.inference import format_prompt
from planckbot.models.checkpoints import CheckpointManager
from planckbot.db.models import Triple, ModelCheckpoint


# --- Dataset tests ---

def _make_triple(**kwargs) -> Triple:
    defaults = dict(
        tool_name="file_search",
        input_data='{"query":"test"}',
        output_data='{"results":["a.py","b.py","c.py"]}',
        filtered_output='["a.py"]',
        input_tokens=10,
        output_tokens=20,
        filtered_tokens=5,
    )
    defaults.update(kwargs)
    return Triple(**defaults)


def test_build_training_texts_filter():
    triples = [_make_triple(), _make_triple()]
    pairs = build_training_texts(triples, strategy="filter_output")
    assert len(pairs) == 2
    assert "prompt" in pairs[0]
    assert "completion" in pairs[0]
    # Completion should be the filtered output
    assert pairs[0]["completion"] == '["a.py"]'


def test_build_training_texts_compress():
    triples = [_make_triple()]
    pairs = build_training_texts(triples, strategy="compress_input")
    assert len(pairs) == 1
    assert "Compress" in pairs[0]["prompt"]


def test_build_training_texts_short_circuit():
    triples = [_make_triple()]
    pairs = build_training_texts(triples, strategy="short_circuit")
    assert pairs[0]["completion"] == '{"results":["a.py","b.py","c.py"]}'


def test_format_prompt_filter():
    p = format_prompt("tool_x", "some input", "filter_output")
    assert "tool_x" in p
    assert "Filter" in p


def test_format_prompt_compress():
    p = format_prompt("tool_x", "some input", "compress_input")
    assert "Compress" in p


def test_format_prompt_short_circuit():
    p = format_prompt("tool_x", "some input", "short_circuit")
    assert "Predict" in p


# --- TrainingConfig tests ---

def test_training_config_defaults():
    cfg = TrainingConfig()
    assert cfg.lora_rank == 8
    assert cfg.learning_rate == 1e-4
    assert cfg.num_epochs == 3


def test_training_status_defaults():
    status = TrainingStatus()
    assert status.is_running is False
    assert status.current_step == 0


# --- CheckpointManager tests ---

def test_checkpoint_save_and_get(conn):
    mgr = CheckpointManager(conn)
    ckpt = ModelCheckpoint(
        name="test-ckpt",
        base_model="SmolLM2-135M",
        tool_name="file_search",
        lora_config={"r": 8},
    )
    mgr.save(ckpt)
    loaded = mgr.get(ckpt.id)
    assert loaded is not None
    assert loaded.name == "test-ckpt"


def test_checkpoint_list(conn):
    mgr = CheckpointManager(conn)
    mgr.save(ModelCheckpoint(name="a", base_model="m1", tool_name="t1"))
    mgr.save(ModelCheckpoint(name="b", base_model="m1", tool_name="t2"))
    assert len(mgr.list_all()) == 2
    assert len(mgr.list_all(tool_name="t1")) == 1


def test_checkpoint_activate(conn):
    mgr = CheckpointManager(conn)
    c1 = ModelCheckpoint(name="c1", base_model="m", tool_name="tool_a")
    c2 = ModelCheckpoint(name="c2", base_model="m", tool_name="tool_a")
    mgr.save(c1)
    mgr.save(c2)
    mgr.activate(c1.id)
    assert mgr.get_active("tool_a").id == c1.id
    mgr.activate(c2.id)
    assert mgr.get_active("tool_a").id == c2.id
    # c1 should be deactivated
    assert mgr.get(c1.id).is_active == 0


def test_checkpoint_delete(conn):
    mgr = CheckpointManager(conn)
    ckpt = ModelCheckpoint(name="del", base_model="m")
    mgr.save(ckpt)
    mgr.delete(ckpt.id)
    assert mgr.get(ckpt.id) is None


def test_checkpoint_count(conn):
    mgr = CheckpointManager(conn)
    assert mgr.count() == 0
    mgr.save(ModelCheckpoint(name="x", base_model="m"))
    assert mgr.count() == 1


def test_checkpoint_compare(conn):
    mgr = CheckpointManager(conn)
    c1 = ModelCheckpoint(name="x", base_model="m")
    c2 = ModelCheckpoint(name="y", base_model="m")
    mgr.save(c1)
    mgr.save(c2)
    result = mgr.compare([c1.id, c2.id])
    assert len(result) == 2


# --- EvalResult tests ---

def test_eval_result_defaults():
    r = EvalResult()
    assert r.num_samples == 0
    assert r.avg_accuracy_bleu == 0.0
