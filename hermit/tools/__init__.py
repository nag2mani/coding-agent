"""Tool registry. Tools are plain dataclasses kept in a list. No plugin system."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    requires_confirm: bool = False
    confirm_summary: Callable[[dict[str, Any]], str] | None = None

    def to_ollama_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def to_ollama_schema(self) -> list[dict[str, Any]]:
        return [t.to_ollama_schema() for t in self._tools.values()]

    def describe_for_prompt(self) -> str:
        if not self._tools:
            return "(no tools available)"
        lines = []
        for t in self._tools.values():
            params = ", ".join((t.parameters.get("properties") or {}).keys()) or "—"
            gate = " [confirm]" if t.requires_confirm else ""
            lines.append(f"- {t.name}({params}){gate}: {t.description}")
        return "\n".join(lines)
