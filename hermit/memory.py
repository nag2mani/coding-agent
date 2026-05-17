"""Helpers for the user-editable Markdown context files (MEMORY.md, SOUL.md)."""

from __future__ import annotations

from pathlib import Path


MEMORY_MAX_BYTES = 4096
SOUL_MAX_BYTES = 1024


def _read_capped(path: Path, cap: int) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        data = path.read_bytes()[: cap + 1]
    except OSError:
        return None
    truncated = len(data) > cap
    if truncated:
        data = data[:cap]
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    if truncated:
        text += "\n[truncated]"
    return text


def read_memory(workspace: Path) -> str | None:
    return _read_capped(workspace / "MEMORY.md", MEMORY_MAX_BYTES)


def read_soul(workspace: Path) -> str | None:
    return _read_capped(workspace / "SOUL.md", SOUL_MAX_BYTES)
