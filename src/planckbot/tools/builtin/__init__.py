"""Built-in demo tools for testing."""

from planckbot.tools.builtin.file_search import file_search
from planckbot.tools.builtin.read_file import read_file
from planckbot.tools.builtin.dummy_api import dummy_api


def register_builtins(registry):
    """Register all built-in tools."""
    registry.register(
        name="file_search",
        fn=file_search,
        description="Search for files matching a pattern in a directory",
        input_schema={
            "query": {"type": "string", "description": "Search pattern or keyword"},
            "path": {"type": "string", "description": "Directory to search in"},
            "glob": {"type": "string", "description": "Glob pattern (e.g., '*.py')"},
        },
        category="filesystem",
    )
    registry.register(
        name="read_file",
        fn=read_file,
        description="Read contents of a file with optional line range",
        input_schema={
            "path": {"type": "string", "description": "Path to the file"},
            "start_line": {"type": "integer", "description": "Start line (1-indexed)"},
            "end_line": {"type": "integer", "description": "End line (inclusive)"},
        },
        category="filesystem",
    )
    registry.register(
        name="dummy_api",
        fn=dummy_api,
        description="Simulated API call that returns verbose JSON (for testing compression)",
        input_schema={
            "endpoint": {"type": "string", "description": "API endpoint name"},
            "params": {"type": "object", "description": "Query parameters"},
        },
        category="api",
    )
