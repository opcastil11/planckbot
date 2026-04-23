"""Tests for the Layer-C `edit_tool` meta-tool."""

import hashlib
import textwrap
from pathlib import Path

import pytest

from planckbot.db.models import ModelCheckpoint
from planckbot.models.checkpoints import CheckpointManager
from planckbot.tools.meta import (
    _apply_unified_diff,
    _validate,
    edit_tool,
)
from planckbot.tools.registry import ToolRegistry
from planckbot.tools.versions import ToolVersionStore


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def versions(conn):
    return ToolVersionStore(conn)


@pytest.fixture
def checkpoints(conn):
    return CheckpointManager(conn)


@pytest.fixture
def tmp_tool(tmp_path: Path, monkeypatch, request):
    """Create a throwaway tool module on disk and register it.

    Uses a test-unique package name so sys.modules caching across tests can't
    point us at a stale file on a previous tmp_path.
    """
    pkg_name = f"throwaway_pkg_{abs(hash(request.node.nodeid))}"
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    src = textwrap.dedent(
        '''
        """Throwaway tool module for tests."""

        import json


        def verbose_echo(msg: str = "") -> str:
            """Return msg wrapped in a noisy JSON envelope."""
            return json.dumps({"message": msg, "noise": "x" * 50})
        '''
    ).lstrip()
    (pkg_dir / "tool.py").write_text(src)

    monkeypatch.syspath_prepend(str(tmp_path))
    import importlib
    import sys

    # Make sure no cached version from a previous test body sticks around.
    for key in list(sys.modules):
        if key.startswith(pkg_name):
            del sys.modules[key]

    mod = importlib.import_module(f"{pkg_name}.tool")

    reg = ToolRegistry()
    reg.register(name="verbose_echo", fn=mod.verbose_echo, description="test")
    return reg, pkg_dir / "tool.py"


# --- unified-diff applier ---------------------------------------------------


def test_apply_unified_diff_basic():
    original = "a\nb\nc\n"
    patch = (
        "--- a/f\n"
        "+++ b/f\n"
        "@@ -1,3 +1,3 @@\n"
        " a\n"
        "-b\n"
        "+B\n"
        " c\n"
    )
    assert _apply_unified_diff(original, patch) == "a\nB\nc\n"


def test_apply_unified_diff_rejects_context_mismatch():
    original = "a\nb\nc\n"
    patch = (
        "@@ -1,3 +1,3 @@\n"
        " WRONG\n"
        "-b\n"
        "+B\n"
        " c\n"
    )
    with pytest.raises(ValueError, match="context"):
        _apply_unified_diff(original, patch)


# --- AST whitelist ----------------------------------------------------------


def test_validate_rejects_new_imports():
    original = "def f():\n    return 1\n"
    new = "import subprocess\ndef f():\n    return 1\n"
    with pytest.raises(ValueError, match="forbidden"):
        _validate(original, new)


def test_validate_rejects_os_system():
    original = "import os\ndef f():\n    return 1\n"
    new = "import os\ndef f():\n    os.system('rm -rf /')\n    return 1\n"
    with pytest.raises(ValueError, match="os.system"):
        _validate(original, new)


def test_validate_rejects_eval():
    original = "def f():\n    return 1\n"
    new = "def f():\n    return eval('1+1')\n"
    with pytest.raises(ValueError, match="eval"):
        _validate(original, new)


def test_validate_accepts_benign_change():
    original = "import json\n\ndef f(x):\n    return json.dumps({'x': x})\n"
    new = "import json\n\ndef f(x):\n    return json.dumps({'y': x})\n"
    _validate(original, new)  # must not raise


def test_validate_rejects_syntax_error():
    with pytest.raises(ValueError, match="syntax"):
        _validate("def f():\n    return 1\n", "def f( :\n")


# --- edit_tool end-to-end ---------------------------------------------------


def test_edit_tool_writes_file_and_records_version(tmp_tool, versions, checkpoints):
    reg, path = tmp_tool
    before = path.read_text()
    # Full-source replacement: minimize the output envelope.
    new_source = textwrap.dedent(
        '''
        """Throwaway tool module for tests."""

        import json


        def verbose_echo(msg: str = "") -> str:
            """Return just the message."""
            return json.dumps({"m": msg})
        '''
    ).lstrip()

    result = edit_tool(
        name="verbose_echo",
        patch=new_source,
        registry=reg,
        versions=versions,
        checkpoints=checkpoints,
        created_by="agent:test",
    )

    assert path.read_text() != before
    assert "x" * 50 not in path.read_text()  # the noise is gone
    assert result.version.tool_name == "verbose_echo"
    assert result.version.code_hash == hashlib.sha256(
        path.read_text().encode()
    ).hexdigest()
    assert versions.latest("verbose_echo").id == result.version.id


def test_edit_tool_chains_parent_version(tmp_tool, versions, checkpoints):
    reg, path = tmp_tool
    src_v1 = textwrap.dedent(
        '''
        """v1"""

        import json


        def verbose_echo(msg: str = "") -> str:
            return json.dumps({"m": msg, "v": 1})
        '''
    ).lstrip()
    src_v2 = textwrap.dedent(
        '''
        """v2"""

        import json


        def verbose_echo(msg: str = "") -> str:
            return json.dumps({"m": msg, "v": 2})
        '''
    ).lstrip()

    r1 = edit_tool(
        name="verbose_echo", patch=src_v1,
        registry=reg, versions=versions, checkpoints=checkpoints,
    )
    r2 = edit_tool(
        name="verbose_echo", patch=src_v2,
        registry=reg, versions=versions, checkpoints=checkpoints,
    )

    assert r1.version.parent_version_id is None
    assert r2.version.parent_version_id == r1.version.id
    assert len(versions.list_for_tool("verbose_echo")) == 2


def test_edit_tool_deactivates_active_adapter(
    tmp_tool, versions, checkpoints, conn,
):
    reg, path = tmp_tool
    ckpt = ModelCheckpoint(
        name="verbose_echo_adapter_v1",
        base_model="SmolLM2-135M-Instruct",
        tool_name="verbose_echo",
        adapter_path="/tmp/fake",
        is_active=1,
    )
    checkpoints.save(ckpt)
    assert checkpoints.get_active("verbose_echo") is not None

    src_v1 = textwrap.dedent(
        '''
        """v1"""

        import json


        def verbose_echo(msg: str = "") -> str:
            return json.dumps({"m": msg})
        '''
    ).lstrip()
    result = edit_tool(
        name="verbose_echo", patch=src_v1,
        registry=reg, versions=versions, checkpoints=checkpoints,
    )

    assert ckpt.id in result.deactivated_checkpoints
    assert checkpoints.get_active("verbose_echo") is None


def test_edit_tool_rejects_unknown_tool(tmp_tool, versions, checkpoints):
    reg, _ = tmp_tool
    with pytest.raises(ValueError, match="not registered"):
        edit_tool(
            name="does_not_exist",
            patch="def f(): pass\n",
            registry=reg,
            versions=versions,
            checkpoints=checkpoints,
        )


def test_edit_tool_rejects_forbidden_patch(tmp_tool, versions, checkpoints):
    reg, path = tmp_tool
    evil = textwrap.dedent(
        '''
        """evil"""

        import json
        import subprocess


        def verbose_echo(msg: str = "") -> str:
            return json.dumps({"m": msg})
        '''
    ).lstrip()
    before = path.read_text()
    with pytest.raises(ValueError):
        edit_tool(
            name="verbose_echo", patch=evil,
            registry=reg, versions=versions, checkpoints=checkpoints,
        )
    # File must be untouched on rejection.
    assert path.read_text() == before
    assert versions.count() == 0


def test_edit_tool_rejects_noop(tmp_tool, versions, checkpoints):
    reg, path = tmp_tool
    same = path.read_text()
    with pytest.raises(ValueError, match="identical"):
        edit_tool(
            name="verbose_echo", patch=same,
            registry=reg, versions=versions, checkpoints=checkpoints,
        )


def test_edit_tool_reloads_module_so_registry_uses_new_fn(
    tmp_tool, versions, checkpoints,
):
    reg, path = tmp_tool
    # Sanity: before edit, the tool returns the noisy envelope.
    out_before = reg.execute("verbose_echo", msg="hi")
    assert "x" * 50 in out_before

    src = textwrap.dedent(
        '''
        """minimal"""

        import json


        def verbose_echo(msg: str = "") -> str:
            return json.dumps({"m": msg})
        '''
    ).lstrip()
    edit_tool(
        name="verbose_echo", patch=src,
        registry=reg, versions=versions, checkpoints=checkpoints,
    )

    out_after = reg.execute("verbose_echo", msg="hi")
    assert "x" * 50 not in out_after
    assert '"m": "hi"' in out_after


# --- ToolVersionStore -------------------------------------------------------


def test_versions_store_roundtrip(versions):
    from planckbot.db.models import ToolVersion

    v = ToolVersion(tool_name="t", code_hash="abc", source="pass\n")
    versions.insert(v)
    assert versions.get(v.id).code_hash == "abc"
    assert versions.latest("t").id == v.id
    assert versions.count() == 1
