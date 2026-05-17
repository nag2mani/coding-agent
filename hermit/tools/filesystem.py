"""Filesystem tools with workspace containment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hermit.tools import Tool


class WorkspaceEscape(Exception):
    pass


def resolve_in_workspace(workspace: Path, rel_path: str) -> Path:
    workspace = workspace.resolve()
    candidate = (workspace / rel_path).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise WorkspaceEscape(
            f"path {rel_path!r} escapes workspace {str(workspace)!r}"
        ) from exc
    return candidate


def make_read_file(workspace: Path) -> Tool:
    async def handler(args: dict[str, Any]) -> str:
        path = args.get("path")
        max_bytes = int(args.get("max_bytes", 65536))
        if not isinstance(path, str) or not path:
            return "error: missing 'path'"
        try:
            target = resolve_in_workspace(workspace, path)
        except WorkspaceEscape as e:
            return f"error: {e}"
        if not target.exists():
            return f"error: not found: {path}"
        if not target.is_file():
            return f"error: not a file: {path}"
        data = target.read_bytes()[: max_bytes + 1]
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"error: not valid utf-8: {path}"
        suffix = "\n[truncated]" if truncated else ""
        return text + suffix

    return Tool(
        name="read_file",
        description="Read a UTF-8 text file from the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the workspace directory.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to read (default 65536).",
                },
            },
            "required": ["path"],
        },
        handler=handler,
    )


def make_write_file(workspace: Path) -> Tool:
    async def handler(args: dict[str, Any]) -> str:
        path = args.get("path")
        content = args.get("content")
        if not isinstance(path, str) or not path:
            return "error: missing 'path'"
        if not isinstance(content, str):
            return "error: missing 'content'"
        try:
            target = resolve_in_workspace(workspace, path)
        except WorkspaceEscape as e:
            return f"error: {e}"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
        return f"wrote {len(content)} chars to {path}"

    def summary(args: dict[str, Any]) -> str:
        path = args.get("path", "?")
        size = len(args.get("content", ""))
        return f"write_file path={path} ({size} chars)"

    return Tool(
        name="write_file",
        description="Write or overwrite a UTF-8 text file in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the workspace directory.",
                },
                "content": {
                    "type": "string",
                    "description": "Full file contents to write.",
                },
            },
            "required": ["path", "content"],
        },
        handler=handler,
        requires_confirm=True,
        confirm_summary=summary,
    )
