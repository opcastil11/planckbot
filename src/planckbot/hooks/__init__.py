"""Claude Code hook handlers. Each module exposes a `handle(input, ...)`
that takes the JSON Claude Code sends to the hook's stdin and returns the
JSON to write to stdout. The shell-level entrypoints live in `scripts/`."""
