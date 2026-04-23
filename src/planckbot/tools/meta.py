"""Layer-C meta-tool: `edit_tool(name, patch)`.

Applies a unified diff (or a full-source replacement) to a registered tool's
module file, validates the result against an AST whitelist, writes a new
`tool_versions` row, and deactivates any adapter that was trained against the
previous revision.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path

from planckbot.db.models import ToolVersion
from planckbot.models.checkpoints import CheckpointManager
from planckbot.tools.registry import ToolRegistry
from planckbot.tools.versions import ToolVersionStore


# Names that may never appear in a patched tool body.
FORBIDDEN_NAMES: set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
}

# (module, attribute) pairs that may never be accessed via attribute.
FORBIDDEN_ATTRS: set[tuple[str, str]] = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "spawnl"),
    ("os", "spawnle"),
    ("os", "spawnlp"),
    ("os", "spawnlpe"),
    ("os", "execv"),
    ("os", "execve"),
    ("shutil", "rmtree"),
    ("socket", "socket"),
    ("pickle", "loads"),
    ("pickle", "load"),
}

# Module names that, if imported, are outright forbidden regardless of how used.
FORBIDDEN_IMPORTS: set[str] = {
    "subprocess",
    "socket",
    "pickle",
    "ctypes",
}


@dataclass
class EditResult:
    version: ToolVersion
    source_path: Path
    deactivated_checkpoints: list[str]


def edit_tool(
    *,
    name: str,
    patch: str,
    registry: ToolRegistry,
    versions: ToolVersionStore,
    checkpoints: CheckpointManager,
    created_by: str = "agent:claude",
    reload_module: bool = True,
) -> EditResult:
    """Apply `patch` to the source file of the tool named `name`.

    `patch` may be either a unified diff (detected by a leading `@@` or `---`
    header) or the full replacement source of the file. The new source must
    pass the AST whitelist; otherwise `ValueError` is raised and the file is
    left untouched.

    On success: writes the file, inserts a new `tool_versions` row, deactivates
    any active checkpoint for the tool, and (optionally) reloads the module so
    that `registry.execute(name, ...)` picks up the new implementation.
    """
    tool = registry.get(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' is not registered")

    source_file = inspect.getsourcefile(tool.fn)
    if source_file is None:
        raise ValueError(f"Cannot locate source file for tool '{name}'")
    source_path = Path(source_file)
    original = source_path.read_text()

    new_src = _apply_patch(original, patch)
    if new_src == original:
        raise ValueError("patch produced identical source; nothing to record")

    _validate(original, new_src)

    # Persist the new source atomically (write to .tmp then rename).
    tmp_path = source_path.with_suffix(source_path.suffix + ".tmp")
    tmp_path.write_text(new_src)
    tmp_path.replace(source_path)

    code_hash = hashlib.sha256(new_src.encode()).hexdigest()
    parent = versions.latest(name)
    # Short-circuit if we somehow produced the same hash (shouldn't happen
    # because the source differs, but guard anyway).
    if parent is not None and parent.code_hash == code_hash:
        raise ValueError("patch produced identical code_hash to current version")

    diff_text = _make_diff_text(original, new_src, source_path.name) \
        if not _looks_like_unified_diff(patch) else patch

    tv = ToolVersion(
        tool_name=name,
        code_hash=code_hash,
        source=new_src,
        created_by=created_by,
        parent_version_id=parent.id if parent else None,
        diff_from_parent=diff_text,
    )
    versions.insert(tv)

    # Any adapter trained on the previous revision is stale by definition.
    deactivated: list[str] = []
    for ckpt in checkpoints.list_all(tool_name=name):
        if ckpt.is_active:
            checkpoints.deactivate(ckpt.id)
            deactivated.append(ckpt.id)

    if reload_module:
        _reload_tool_module(tool.fn, registry, name)

    return EditResult(
        version=tv, source_path=source_path, deactivated_checkpoints=deactivated
    )


# --- patch application ------------------------------------------------------


def _looks_like_unified_diff(patch: str) -> bool:
    head = patch.lstrip()
    return head.startswith("@@") or head.startswith("--- ") or head.startswith("diff ")


def _apply_patch(original: str, patch: str) -> str:
    if _looks_like_unified_diff(patch):
        return _apply_unified_diff(original, patch)
    # Treat as full replacement source.
    return patch if patch.endswith("\n") else patch + "\n"


def _apply_unified_diff(original: str, patch: str) -> str:
    """Apply a single-file unified diff. Very small, strict implementation.

    Supports standard `@@ -a,b +c,d @@` hunks with ` ` / `+` / `-` lines. Does
    not support rename/mode/binary diffs. Raises ValueError if a hunk does not
    match the current file contents.
    """
    orig_lines = original.splitlines(keepends=True)
    hunks: list[dict] = []
    cur: dict | None = None

    hunk_header = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    for raw in patch.splitlines(keepends=True):
        if raw.startswith("@@"):
            m = hunk_header.match(raw)
            if not m:
                raise ValueError(f"malformed hunk header: {raw!r}")
            if cur is not None:
                hunks.append(cur)
            cur = {
                "orig_start": int(m.group(1)),
                "orig_count": int(m.group(2) or 1),
                "lines": [],
            }
        elif raw.startswith("--- ") or raw.startswith("+++ "):
            continue
        elif raw.startswith("diff ") or raw.startswith("index "):
            continue
        elif cur is not None:
            cur["lines"].append(raw)
    if cur is not None:
        hunks.append(cur)

    if not hunks:
        raise ValueError("patch contains no hunks")

    result = list(orig_lines)
    for h in reversed(hunks):  # reverse so later hunks don't shift earlier ones
        start = max(h["orig_start"] - 1, 0)  # convert to 0-indexed
        new_block: list[str] = []
        consumed = 0
        for hline in h["lines"]:
            if not hline:
                continue
            tag = hline[0]
            body = hline[1:] if len(hline) > 1 else ""
            if tag == " ":
                if start + consumed >= len(result):
                    raise ValueError("hunk context extends past end of file")
                actual = result[start + consumed]
                if actual != body:
                    raise ValueError(
                        f"context mismatch at line {start + consumed + 1}: "
                        f"expected {body!r}, got {actual!r}"
                    )
                new_block.append(body)
                consumed += 1
            elif tag == "-":
                if start + consumed >= len(result):
                    raise ValueError("hunk deletion extends past end of file")
                actual = result[start + consumed]
                if actual != body:
                    raise ValueError(
                        f"deletion mismatch at line {start + consumed + 1}: "
                        f"expected {body!r}, got {actual!r}"
                    )
                consumed += 1
            elif tag == "+":
                new_block.append(body)
            elif tag == "\\":
                # "\ No newline at end of file" — ignore marker.
                continue
            else:
                raise ValueError(f"unknown hunk line marker: {hline!r}")
        result[start : start + consumed] = new_block

    return "".join(result)


def _make_diff_text(original: str, new_src: str, filename: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new_src.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )


# --- AST whitelist ----------------------------------------------------------


def _validate(original: str, new_src: str) -> None:
    try:
        new_tree = ast.parse(new_src)
    except SyntaxError as e:
        raise ValueError(f"new source has syntax error: {e}") from e

    orig_imports = _collect_imports(ast.parse(original))
    new_imports = _collect_imports(new_tree)
    added = new_imports - orig_imports
    forbidden_added = added & FORBIDDEN_IMPORTS
    if forbidden_added:
        raise ValueError(
            f"patch imports forbidden modules: {sorted(forbidden_added)}"
        )
    # Non-forbidden new imports are still rejected — tools must not silently
    # expand their dependency surface.
    if added:
        raise ValueError(f"patch adds new imports: {sorted(added)}")

    for node in ast.walk(new_tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise ValueError(f"patch uses forbidden name: {node.id}")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            pair = (node.value.id, node.attr)
            if pair in FORBIDDEN_ATTRS:
                raise ValueError(
                    f"patch uses forbidden attribute: {pair[0]}.{pair[1]}"
                )


def _collect_imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


# --- module reload ----------------------------------------------------------


def _reload_tool_module(fn, registry: ToolRegistry, name: str) -> None:
    """Reload the module that defines `fn` and re-point the registry at the
    new function object. Best-effort; skipped for lambdas or C-level funcs.
    """
    module_name = getattr(fn, "__module__", None)
    fn_name = getattr(fn, "__name__", None)
    if not module_name or not fn_name:
        return
    module = importlib.import_module(module_name)
    module = importlib.reload(module)
    new_fn = getattr(module, fn_name, None)
    if new_fn is None:
        return
    tool = registry.get(name)
    if tool is not None:
        tool.fn = new_fn
