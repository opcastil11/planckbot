#!/usr/bin/env python3
"""Portable shim around `planckbot.hooks.entrypoint:main`.

Prefer registering the `planckbot-hook` console_script from pyproject in
~/.claude/settings.json (lives at `.venv/bin/planckbot-hook`). This file
exists so a clone of the repo can wire the hook before running
`uv pip install -e .` — it puts `src/` on sys.path manually.

Settings.json snippet:
  "hooks": {
    "PreToolUse": [
      {"matcher": "Read|Edit|Write|MultiEdit|NotebookEdit",
       "hooks": [{"type": "command",
                  "command": "/abs/path/to/.venv/bin/planckbot-hook",
                  "timeout": 3, "async": true}]}
    ],
    "PostToolUse": [
      {"matcher": "Read",
       "hooks": [{"type": "command",
                  "command": "/abs/path/to/.venv/bin/planckbot-hook",
                  "timeout": 3, "async": true}]}
    ]
  }

Env vars:
  PLANCKBOT_DB              Override DB path (default: <repo>/data/planckbot.db).
  PLANCKBOT_HOOK_DEBUG      File path; appends every stdin payload + errors.
  PLANCKBOT_HOOK_DISABLED   When set, pass through (skip cache lookup).
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from planckbot.hooks.entrypoint import main


if __name__ == "__main__":
    main()
