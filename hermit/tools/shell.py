"""Shell exec tool. Workspace cwd, bounded output, requires confirm."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hermit.tools import Tool


MAX_OUTPUT_BYTES = 4096


def make_exec(workspace: Path) -> Tool:
    async def handler(args: dict[str, Any]) -> str:
        command = args.get("command")
        timeout = float(args.get("timeout_sec", 30))
        if not isinstance(command, str) or not command.strip():
            return "error: missing 'command'"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as e:
            return f"error: failed to spawn: {e}"

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"error: command timed out after {timeout}s"

        text = (stdout or b"").decode("utf-8", errors="replace")
        truncated = len(text.encode("utf-8")) > MAX_OUTPUT_BYTES
        if truncated:
            text = text.encode("utf-8")[:MAX_OUTPUT_BYTES].decode(
                "utf-8", errors="ignore"
            )
        suffix = "\n[truncated]" if truncated else ""
        return f"exit={proc.returncode}\n{text}{suffix}"

    def summary(args: dict[str, Any]) -> str:
        cmd = (args.get("command") or "").strip()
        if len(cmd) > 200:
            cmd = cmd[:200] + "…"
        return f"exec command={cmd!r}"

    return Tool(
        name="exec",
        description=(
            "Run a shell command in the workspace and return its combined "
            "stdout+stderr. Use sparingly; prefer file tools when possible."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout_sec": {
                    "type": "integer",
                    "description": "Seconds before the command is killed (default 30).",
                },
            },
            "required": ["command"],
        },
        handler=handler,
        requires_confirm=True,
        confirm_summary=summary,
    )
