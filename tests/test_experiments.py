"""Tests for experiment manager and metrics."""

import json

from planckbot.experiments.manager import ExperimentManager
from planckbot.experiments.metrics import (
    count_tokens_approx, compute_token_savings,
    compute_accuracy_exact, compute_accuracy_bleu, compute_accuracy,
)


# --- ExperimentManager tests ---

def test_create_experiment(exp_manager):
    exp = exp_manager.create(name="exp-1", experiment_type="filter_output", hypothesis="h1")
    assert exp.name == "exp-1"
    assert exp.status == "planned"


def test_get_experiment(exp_manager):
    exp = exp_manager.create(name="exp-get", experiment_type="filter_output")
    loaded = exp_manager.get(exp.id)
    assert loaded is not None
    assert loaded.name == "exp-get"


def test_get_nonexistent(exp_manager):
    assert exp_manager.get("nonexistent-id") is None


def test_list_all(exp_manager):
    exp_manager.create(name="a", experiment_type="filter_output")
    exp_manager.create(name="b", experiment_type="compress_input")
    all_exps = exp_manager.list_all()
    assert len(all_exps) == 2


def test_list_by_status(exp_manager):
    exp = exp_manager.create(name="s1", experiment_type="filter_output")
    exp_manager.update_status(exp.id, "running")
    running = exp_manager.list_all(status="running")
    planned = exp_manager.list_all(status="planned")
    assert len(running) == 1
    assert len(planned) == 0


def test_update_status_running(exp_manager):
    exp = exp_manager.create(name="run-test", experiment_type="filter_output")
    exp_manager.update_status(exp.id, "running")
    loaded = exp_manager.get(exp.id)
    assert loaded.status == "running"
    assert loaded.started_at is not None


def test_update_status_completed(exp_manager):
    exp = exp_manager.create(name="done-test", experiment_type="filter_output")
    exp_manager.update_status(exp.id, "completed")
    loaded = exp_manager.get(exp.id)
    assert loaded.status == "completed"
    assert loaded.completed_at is not None


def test_record_metrics(exp_manager):
    exp = exp_manager.create(name="met-test", experiment_type="filter_output")
    exp_manager.record_metrics(exp.id, {"accuracy": 0.95, "token_savings_pct": 42.0})
    loaded = exp_manager.get(exp.id)
    assert loaded.metrics["accuracy"] == 0.95


def test_update_observations(exp_manager):
    exp = exp_manager.create(name="obs-test", experiment_type="filter_output")
    exp_manager.update_observations(exp.id, "Model converged fast")
    loaded = exp_manager.get(exp.id)
    assert loaded.observations == "Model converged fast"


def test_delete_experiment(exp_manager):
    exp = exp_manager.create(name="del-test", experiment_type="filter_output")
    exp_manager.delete(exp.id)
    assert exp_manager.get(exp.id) is None


def test_count(exp_manager):
    assert exp_manager.count() == 0
    exp_manager.create(name="c1", experiment_type="filter_output")
    exp_manager.create(name="c2", experiment_type="filter_output")
    assert exp_manager.count() == 2


def test_compare(exp_manager):
    e1 = exp_manager.create(name="cmp-1", experiment_type="filter_output")
    e2 = exp_manager.create(name="cmp-2", experiment_type="compress_input")
    results = exp_manager.compare([e1.id, e2.id])
    assert len(results) == 2


# --- Metrics tests ---

def test_count_tokens_approx():
    assert count_tokens_approx("") == 0
    assert count_tokens_approx("hello world") >= 1


def test_token_savings():
    assert compute_token_savings(100, 60) == 40.0
    assert compute_token_savings(0, 0) == 0.0


def test_accuracy_exact():
    assert compute_accuracy_exact("hello", "hello") == 1.0
    assert compute_accuracy_exact("hello", "world") == 0.0


def test_accuracy_bleu():
    score = compute_accuracy_bleu("the cat sat on the mat", "the cat sat on the mat")
    assert score > 0.9
    score2 = compute_accuracy_bleu("the cat sat on the mat", "completely different words")
    assert score2 < score


def test_compute_accuracy_dispatch():
    assert compute_accuracy("a", "a", method="exact") == 1.0
    assert compute_accuracy("a b c", "a b c", method="bleu") > 0.9
