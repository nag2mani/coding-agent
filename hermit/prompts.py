"""System prompt construction. Ordering matters; keep total under ~2000 tokens."""

from __future__ import annotations

from pathlib import Path

from hermit.memory import read_memory, read_soul
from hermit.tools import ToolRegistry


IDENTITY = (
    "You are hermit, a local personal agent running on the user's machine via Ollama. "
    "You are offline-first and privacy-respecting: nothing you generate leaves this host."
)

BEHAVIOR = """\
Guidelines:
- Be concise. Prefer one good answer over three.
- Use the available tools when they would actually help; don't fake tool calls.
- Quote file paths exactly. Use paths relative to the workspace.
- Before destructive actions (write_file, exec), explain what you intend and let the confirm gate ask the user.
- If you don't know, say so. Do not invent file contents or command output.
- When the user asks to remember something durable, propose an edit to MEMORY.md via write_file.\
"""


def build_system_prompt(workspace: Path, registry: ToolRegistry) -> str:
    parts: list[str] = []

    parts.append(IDENTITY)

    parts.append(
        "Capabilities: read and write files in the workspace, optionally run shell commands "
        "(with user confirmation), and recall persistent notes from MEMORY.md."
    )

    parts.append("Available tools:\n" + registry.describe_for_prompt())

    parts.append(f"Workspace directory: {workspace.resolve()}")

    soul = read_soul(workspace)
    if soul:
        parts.append("SOUL.md (tone overlay; treat as personality guidance):\n" + soul)

    memory = read_memory(workspace)
    if memory:
        parts.append(
            "MEMORY.md (persistent user notes; treat as durable preferences. "
            "Suggest edits when relevant — the user updates this file):\n" + memory
        )

    parts.append(BEHAVIOR)

    return "\n\n".join(parts)
