#!/usr/bin/env python3
"""Local coding agent powered by Ollama + gemma4:e4b (or any tool-capable model).

Uses Ollama's native tool-calling API — the model emits structured tool calls
through the API channel, so we don't have to parse JSON out of free text.

Usage:
    python agent.py                          # interactive REPL
    python agent.py "fix the bug in foo.py"  # one-shot task
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from ollama import Client

MODEL = os.environ.get("AGENT_MODEL", "gemma4:e4b")
HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
WORKDIR = Path(os.environ.get("AGENT_WORKDIR", os.getcwd())).resolve()
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "25"))


# ---------- ANSI ----------
class C:
    DIM = "\033[2m"
    RED = "\033[31m"
    GRN = "\033[32m"
    YEL = "\033[33m"
    BLU = "\033[34m"
    MAG = "\033[35m"
    CYN = "\033[36m"
    BLD = "\033[1m"
    END = "\033[0m"


# ---------- Tool implementations ----------
def _safe_path(rel: str) -> Path:
    p = (WORKDIR / rel).resolve() if not os.path.isabs(rel) else Path(rel).resolve()
    try:
        p.relative_to(WORKDIR)
    except ValueError:
        raise PermissionError(f"path {p} is outside workdir {WORKDIR}")
    return p


def tool_read_file(path: str) -> str:
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    text = p.read_text(errors="replace")
    lines = text.splitlines() or [""]
    numbered = "\n".join(f"{i + 1:5d}  {ln}" for i, ln in enumerate(lines))
    return f"--- {path} ({len(lines)} lines) ---\n{numbered}"


def tool_write_file(path: str, content: str) -> str:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"OK: wrote {len(content)} bytes to {path}"


def tool_edit_file(path: str, old: str, new: str) -> str:
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    text = p.read_text()
    count = text.count(old)
    if count == 0:
        return f"ERROR: old_string not found in {path}"
    if count > 1:
        return f"ERROR: old_string appears {count} times in {path}; make it more specific"
    p.write_text(text.replace(old, new, 1))
    return f"OK: replaced 1 occurrence in {path}"


def tool_list_dir(path: str = ".") -> str:
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: path not found: {path}"
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    entries = []
    for child in sorted(p.iterdir()):
        if child.name.startswith("."):
            continue
        marker = "/" if child.is_dir() else ""
        size = "" if child.is_dir() else f" ({child.stat().st_size}b)"
        entries.append(f"  {child.name}{marker}{size}")
    return f"--- {path} ---\n" + ("\n".join(entries) if entries else "  (empty)")


def tool_run_bash(command: str) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        parts = [f"exit_code: {result.returncode}"]
        if result.stdout.strip():
            parts.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr.strip():
            parts.append(f"stderr:\n{result.stderr.strip()}")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 60s"
    except Exception as e:
        return f"ERROR: {e}"


TOOLS: dict[str, Callable[..., str]] = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "list_dir": tool_list_dir,
    "run_bash": tool_run_bash,
}

# ---------- Tool schemas for Ollama ----------
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file and return its contents with 1-based line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the working directory."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing one. Use for new files; prefer edit_file for small changes to existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the working directory."},
                    "content": {"type": "string", "description": "Full file contents to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a unique substring in an existing file. The old_string must appear exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string", "description": "Exact substring to replace; must be unique in the file."},
                    "new": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path, defaults to '.'."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Run a shell command in the working directory. 60s timeout. Use for builds, tests, git, grep, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."}
                },
                "required": ["command"],
            },
        },
    },
]

SYSTEM_PROMPT = f"""You are a careful, capable local coding agent on the user's machine.
You help with software engineering tasks: reading code, editing files, running commands, debugging, writing tests.

Working directory: {WORKDIR}

Workflow:
1. For non-trivial tasks, briefly think about the steps, then USE TOOLS to do real work.
2. Call exactly one tool at a time, observe the result, then decide the next step.
3. To create new files use write_file. To make small changes use edit_file. To inspect first use read_file or list_dir.
4. After finishing, send a plain text reply (no tool call) summarizing what you did and the files you touched.
5. Be concise. Do not narrate every thought — let your tool calls speak.
"""


# ---------- Helpers ----------
def _coerce_args(raw: Any) -> dict[str, Any]:
    """Tool arguments may arrive as dict or as a JSON string depending on model/SDK."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
    return {"_unknown": str(raw)}


def execute_tool(name: str, args: dict[str, Any]) -> str:
    fn = TOOLS.get(name)
    if fn is None:
        return f"ERROR: unknown tool '{name}'. Available: {', '.join(TOOLS)}"
    try:
        return fn(**args)
    except TypeError as e:
        return f"ERROR: bad args for {name}: {e}"
    except Exception as e:
        return f"ERROR: {name} raised {type(e).__name__}: {e}"


def _truncate(s: str, n: int = 2000) -> str:
    return s if len(s) <= n else s[:n] + f"\n... [truncated, {len(s) - n} more chars]"


# ---------- Agent loop ----------
def run_task(client: Client, task: str, history: list[dict[str, Any]]) -> None:
    history.append({"role": "user", "content": task})

    for step in range(1, MAX_STEPS + 1):
        print(f"\n{C.DIM}── step {step}/{MAX_STEPS} ──{C.END}")

        response = client.chat(
            model=MODEL,
            messages=history,
            tools=TOOL_DEFINITIONS,
            stream=False,
        )
        message = response["message"]
        content = (message.get("content") or "").strip()
        tool_calls = message.get("tool_calls") or []

        if content:
            print(f"{C.MAG}assistant ▸{C.END} {content}")

        # Store assistant turn (include tool_calls so the model sees its own actions next round)
        history.append(
            {
                "role": "assistant",
                "content": content,
                **({"tool_calls": tool_calls} if tool_calls else {}),
            }
        )

        if not tool_calls:
            # Final answer — no more tool calls means the model is done.
            print(f"\n{C.GRN}✓ done{C.END}")
            return

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "?")
            args = _coerce_args(fn.get("arguments"))
            arg_preview = ", ".join(f"{k}={_truncate(str(v), 60)!r}" for k, v in args.items())
            print(f"{C.CYN}tool ▸ {name}({arg_preview}){C.END}")

            result = execute_tool(name, args)
            print(f"{C.DIM}{_truncate(result)}{C.END}")

            history.append({"role": "tool", "name": name, "content": result})

    print(f"\n{C.RED}✗ hit max steps ({MAX_STEPS}) without finishing{C.END}")


# ---------- Entry point ----------
def main() -> int:
    client = Client(host=HOST)
    try:
        client.list()
    except Exception as e:
        print(f"{C.RED}cannot reach ollama at {HOST}: {e}{C.END}", file=sys.stderr)
        print("start ollama with: ollama serve", file=sys.stderr)
        return 1

    print(f"{C.BLD}gemma coding agent{C.END}  model={MODEL}  workdir={WORKDIR}")
    print(f"{C.DIM}commands: /reset  /history  /workdir  /exit{C.END}\n")

    history: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if len(sys.argv) > 1:
        run_task(client, " ".join(sys.argv[1:]), history)
        return 0

    while True:
        try:
            user = input(f"{C.BLU}you ▸{C.END} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user:
            continue
        if user in ("/exit", "/quit"):
            return 0
        if user == "/reset":
            history = [{"role": "system", "content": SYSTEM_PROMPT}]
            print(f"{C.DIM}(history cleared){C.END}")
            continue
        if user == "/history":
            for m in history:
                role = m.get("role")
                snippet = (m.get("content") or "")[:200]
                extra = ""
                if m.get("tool_calls"):
                    extra = f" [{len(m['tool_calls'])} tool_calls]"
                print(f"{C.DIM}[{role}]{extra}{C.END} {snippet}")
            continue
        if user == "/workdir":
            print(f"workdir: {WORKDIR}")
            continue
        run_task(client, user, history)


if __name__ == "__main__":
    sys.exit(main())
