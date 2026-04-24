"""Tests for config paths — specifically that data_dir is absolute regardless
of the calling process's cwd.

Motivation: before fixing _default_data_dir, MCP subprocesses spawned by a
Claude Code session with cwd=/other-repo would resolve "data/planckbot.db"
against /other-repo and write triples to the wrong DB.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from planckbot.config import PlanckBotConfig, _default_data_dir


def test_default_data_dir_is_absolute():
    path = _default_data_dir()
    assert path.is_absolute()


def test_default_data_dir_points_at_repo_data():
    """When PLANCK_DATA_DIR is unset we should anchor at the repo root."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PLANCK_DATA_DIR", None)
        path = _default_data_dir()
    # The resolved path must end with /data regardless of cwd
    assert path.name == "data"
    # Must be absolute and reachable
    assert path.is_absolute()


def test_env_override_wins(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom_planck_data"
    monkeypatch.setenv("PLANCK_DATA_DIR", str(custom))
    path = _default_data_dir()
    assert path == custom.resolve()


def test_config_db_path_follows_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANCK_DATA_DIR", str(tmp_path))
    cfg = PlanckBotConfig()
    assert cfg.data_dir == tmp_path.resolve()
    assert cfg.db_path == tmp_path.resolve() / "planckbot.db"


def test_config_db_path_unaffected_by_cwd(tmp_path, monkeypatch):
    """Regression: if someone runs from a random cwd, the DB path must still
    resolve to the package-anchored data dir. This is the bug that caused
    MCP subprocesses to write triples into /orquesta/data/.
    """
    monkeypatch.delenv("PLANCK_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # pretend we're running from an unrelated cwd

    cfg = PlanckBotConfig()
    # The data_dir must NOT be under tmp_path
    assert not str(cfg.data_dir).startswith(str(tmp_path))
    assert cfg.data_dir.is_absolute()
