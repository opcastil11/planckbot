"""Entry point for `python -m planckbot` and the `planckbot` console script.

The real dispatch lives in `planckbot.cli`; this file is a thin shim so the
console_script in pyproject.toml keeps pointing at a stable path.
"""

from planckbot.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
