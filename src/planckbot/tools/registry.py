"""Tool registration and management."""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolDefinition:
    name: str
    description: str
    fn: Callable
    input_schema: dict = field(default_factory=dict)
    output_schema: dict | None = None
    category: str = "general"


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        fn: Callable,
        description: str = "",
        input_schema: dict | None = None,
        category: str = "general",
    ) -> ToolDefinition:
        tool = ToolDefinition(
            name=name,
            description=description or fn.__doc__ or "",
            fn=fn,
            input_schema=input_schema or {},
            category=category,
        )
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, name: str, **kwargs) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Tool '{name}' not registered")
        return tool.fn(**kwargs)
