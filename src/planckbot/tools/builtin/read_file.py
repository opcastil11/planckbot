"""Built-in tool: read file contents."""

import json
from pathlib import Path


def read_file(path: str, start_line: int = 1, end_line: int = -1) -> str:
    """Read contents of a file with optional line range."""
    fpath = Path(path).expanduser().resolve()
    if not fpath.exists():
        return json.dumps({"error": f"File '{path}' not found"})
    if not fpath.is_file():
        return json.dumps({"error": f"'{path}' is not a file"})

    try:
        text = fpath.read_text(errors="replace")
    except Exception as e:
        return json.dumps({"error": str(e)})

    lines = text.splitlines()
    total = len(lines)

    start = max(0, start_line - 1)
    end = total if end_line < 0 else min(end_line, total)
    selected = lines[start:end]

    return json.dumps({
        "path": str(fpath),
        "total_lines": total,
        "showing": f"{start + 1}-{end}",
        "content": "\n".join(selected),
    }, indent=2)
