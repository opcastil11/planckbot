"""Built-in tool: search files by pattern."""

import json
from pathlib import Path


def file_search(query: str = "", path: str = ".", glob: str = "*") -> str:
    """Search for files matching a query in the given path."""
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return json.dumps({"error": f"Path '{path}' does not exist", "results": []})

    results = []
    try:
        for p in root.rglob(glob):
            if p.is_file():
                if not query or query.lower() in p.name.lower() or query.lower() in str(p).lower():
                    results.append({
                        "path": str(p),
                        "name": p.name,
                        "size_bytes": p.stat().st_size,
                        "suffix": p.suffix,
                    })
            if len(results) >= 200:
                break
    except PermissionError:
        pass

    return json.dumps({
        "query": query,
        "search_path": str(root),
        "glob_pattern": glob,
        "total_results": len(results),
        "results": results,
    }, indent=2)
