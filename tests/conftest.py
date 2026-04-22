"""Shared test fixtures."""

import sqlite3
import pytest

from planckbot.db.engine import get_connection
from planckbot.db.models import Experiment, Triple, ModelCheckpoint, PaperEntry, MetricRecord
from planckbot.experiments.manager import ExperimentManager
from planckbot.tools.triples import TriplesStore
from planckbot.tools.registry import ToolRegistry
from planckbot.tools.builtin import register_builtins
from planckbot.paper.log import PaperLog


@pytest.fixture
def conn(tmp_path):
    """File-backed SQLite connection with schema applied (WAL requires a real file)."""
    c = get_connection(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def exp_manager(conn):
    return ExperimentManager(conn)


@pytest.fixture
def triples_store(conn):
    return TriplesStore(conn)


@pytest.fixture
def paper_log(conn):
    return PaperLog(conn)


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_builtins(reg)
    return reg
