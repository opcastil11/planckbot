"""Shell-callable entrypoint for the cache hook. Exposed via the
`planckbot-hook` console_script in pyproject.toml.

Behavior is documented at `scripts/planckbot-hook.py` (kept as a thin
fallback that imports this). Never raises to Claude Code — internal
errors silently exit 0 with empty stdout so the tool runs as if no hook
were installed.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def _exit_pass_through() -> None:
    sys.exit(0)


def _find_default_db() -> str:
    """Look for `data/planckbot.db` relative to this package's source
    tree. Returns the resolved path as a string, even if the file
    doesn't exist yet — `get_connection` will create it.
    """
    here = Path(__file__).resolve()
    # src/planckbot/hooks/entrypoint.py → repo at parents[3]
    repo = here.parents[3] if len(here.parents) >= 4 else here.parent
    return str(repo / "data" / "planckbot.db")


def main() -> None:
    if os.environ.get("PLANCKBOT_HOOK_DISABLED"):
        _exit_pass_through()

    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        _exit_pass_through()

    if not raw or not raw.strip():
        _exit_pass_through()

    dbg_path = os.environ.get("PLANCKBOT_HOOK_DEBUG")
    if dbg_path:
        try:
            with open(dbg_path, "a") as f:
                f.write(raw.rstrip() + "\n")
        except OSError:
            pass

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        _exit_pass_through()

    try:
        from planckbot.db.engine import get_connection
        from planckbot.hooks.cache_hook import handle
        from planckbot.tools.cache import ToolCacheStore

        db_path = os.environ.get("PLANCKBOT_DB") or _find_default_db()
        conn = get_connection(db_path)
        try:
            store = ToolCacheStore(conn)
            result = handle(payload, store)
        finally:
            conn.close()
    except Exception:
        if dbg_path:
            try:
                with open(dbg_path, "a") as f:
                    f.write("ERR " + traceback.format_exc() + "\n")
            except OSError:
                pass
        _exit_pass_through()

    if result:
        try:
            sys.stdout.write(json.dumps(result))
        except (TypeError, ValueError):
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
