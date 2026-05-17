"""hermit CLI: run, chat, sessions, doctor."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from hermit.agent import Agent
from hermit.config import Config
from hermit.ollama_client import OllamaClient
from hermit.session import Session, SessionStore
from hermit.tools import Tool, ToolRegistry
from hermit.tools.filesystem import make_read_file, make_write_file
from hermit.tools.shell import make_exec
from hermit.tools.web import make_fetch_url


# ──────────────────────────────────────────────────────────────────────────────
# wiring
# ──────────────────────────────────────────────────────────────────────────────


def _build_registry(cfg: Config) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(make_read_file(cfg.workspace))
    reg.register(make_write_file(cfg.workspace))
    reg.register(make_exec(cfg.workspace))
    reg.register(make_fetch_url(cfg.allow_network))
    return reg


def _build_client(cfg: Config) -> OllamaClient:
    return OllamaClient(cfg.ollama_host, cfg.model, timeout_sec=cfg.timeout_sec)


def _apply_overrides(
    cfg: Config, workspace: str | None, state_dir: str | None
) -> Config:
    overrides: dict[str, Any] = {}
    if workspace:
        ws = Path(workspace).expanduser().resolve()
        ws.mkdir(parents=True, exist_ok=True)
        overrides["workspace"] = ws
    if state_dir:
        sd = Path(state_dir).expanduser().resolve()
        sd.mkdir(parents=True, exist_ok=True)
        overrides["state_dir"] = sd
    return dataclasses.replace(cfg, **overrides) if overrides else cfg


def workspace_option(f):
    return click.option(
        "--workspace",
        "workspace_override",
        default=None,
        help="Override HERMIT_WORKSPACE for this command.",
    )(f)


def state_dir_option(f):
    return click.option(
        "--state-dir",
        "state_dir_override",
        default=None,
        help="Override HERMIT_STATE_DIR for this command.",
    )(f)


_always_session: set[str] = set()


async def _interactive_confirm(tool: Tool, args: dict[str, Any]) -> bool:
    if tool.name in _always_session:
        return True
    summary = (
        tool.confirm_summary(args) if tool.confirm_summary else f"{tool.name}({args})"
    )
    click.echo()
    click.secho(f"hermit wants to: {tool.name}", fg="yellow", bold=True)
    click.echo(f"  {summary}")
    choice = click.prompt(
        "[y]es  [n]o  [a]lways for this session",
        default="n",
        show_default=False,
    ).strip().lower()
    if choice in ("a", "always"):
        _always_session.add(tool.name)
        return True
    return choice in ("y", "yes")


# ──────────────────────────────────────────────────────────────────────────────
# commands
# ──────────────────────────────────────────────────────────────────────────────


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="hermit")
def main() -> None:
    """hermit — privacy-first local personal agent."""


@main.command("run")
@click.argument("prompt", nargs=-1, required=True)
@click.option("--session", "session_id", default=None, help="Resume a session.")
@workspace_option
@state_dir_option
def cmd_run(
    prompt: tuple[str, ...],
    session_id: str | None,
    workspace_override: str | None,
    state_dir_override: str | None,
) -> None:
    """One-shot: send a single prompt and print the final answer."""
    text = " ".join(prompt)
    cfg = _apply_overrides(Config.load(), workspace_override, state_dir_override)
    asyncio.run(_run_once(cfg, text, session_id))


def _stream_to_stdout(s: str) -> None:
    click.echo(s, nl=False)
    sys.stdout.flush()


async def _run_once(cfg: Config, text: str, session_id: str | None) -> None:
    store = SessionStore(cfg.state_dir)
    if session_id:
        session = store.load(session_id)
        if session is None:
            click.secho(f"session not found: {session_id}", fg="red")
            sys.exit(2)
    else:
        session = Session.new(model=cfg.model)

    async with _build_client(cfg) as client:
        agent = Agent(
            client=client,
            registry=_build_registry(cfg),
            store=store,
            workspace=cfg.workspace,
            max_steps=cfg.max_steps,
            confirm=_interactive_confirm,
            max_history_messages=cfg.max_history_messages,
            max_history_chars=cfg.max_history_chars,
        )
        result = await agent.run_turn(session, text, on_chunk=_stream_to_stdout)

    click.echo()
    click.secho(
        f"[session={session.id} steps={result.steps} stopped={result.stopped}]",
        fg="cyan",
        dim=True,
    )


@main.command("chat")
@click.option("--session", "session_id", default=None, help="Resume a session.")
@workspace_option
@state_dir_option
def cmd_chat(
    session_id: str | None,
    workspace_override: str | None,
    state_dir_override: str | None,
) -> None:
    """Interactive REPL. Ctrl-D or `/exit` to leave."""
    cfg = _apply_overrides(Config.load(), workspace_override, state_dir_override)
    asyncio.run(_chat_loop(cfg, session_id))


async def _chat_loop(cfg: Config, session_id: str | None) -> None:
    store = SessionStore(cfg.state_dir)
    if session_id:
        session = store.load(session_id)
        if session is None:
            click.secho(f"session not found: {session_id}", fg="red")
            sys.exit(2)
    else:
        session = Session.new(model=cfg.model)

    click.secho(
        f"hermit chat — model={cfg.model}  session={session.id}  workspace={cfg.workspace}",
        fg="green",
    )
    click.echo("Type /exit to quit.\n")

    async with _build_client(cfg) as client:
        agent = Agent(
            client=client,
            registry=_build_registry(cfg),
            store=store,
            workspace=cfg.workspace,
            max_steps=cfg.max_steps,
            confirm=_interactive_confirm,
            max_history_messages=cfg.max_history_messages,
            max_history_chars=cfg.max_history_chars,
        )
        while True:
            try:
                user = click.prompt(">", prompt_suffix=" ", default="", show_default=False)
            except (click.exceptions.Abort, EOFError):
                click.echo()
                break
            user = user.strip()
            if not user:
                continue
            if user in ("/exit", "/quit"):
                break

            result = await agent.run_turn(session, user, on_chunk=_stream_to_stdout)
            click.echo()
            if result.stopped == "max_steps":
                click.secho("[hit max_steps]", fg="yellow", dim=True)
            click.echo()


@main.group("sessions")
def cmd_sessions() -> None:
    """Manage saved sessions."""


@cmd_sessions.command("list")
def sessions_list() -> None:
    cfg = Config.load()
    store = SessionStore(cfg.state_dir)
    rows = store.list()
    if not rows:
        click.echo("(no sessions)")
        return
    for r in rows:
        ts = datetime.fromtimestamp(r.get("updated_at", 0)).strftime("%Y-%m-%d %H:%M")
        click.echo(
            f"{r['id']}  {ts}  {r.get('message_count', 0):>3} msgs  "
            f"{r.get('model', '?')}  {r.get('title', '')!r}"
        )


@cmd_sessions.command("show")
@click.argument("session_id")
def sessions_show(session_id: str) -> None:
    cfg = Config.load()
    store = SessionStore(cfg.state_dir)
    s = store.load(session_id)
    if s is None:
        click.secho("not found", fg="red")
        sys.exit(2)
    click.secho(f"session {s.id}  model={s.model}  msgs={len(s.messages)}", fg="cyan")
    for m in s.messages:
        ts = datetime.fromtimestamp(m.timestamp).strftime("%H:%M:%S")
        role = m.role.upper()
        head = f"[{ts}] {role}"
        if m.tool_name:
            head += f" ({m.tool_name})"
        click.secho(head, fg="yellow")
        click.echo(m.content)
        click.echo()


@cmd_sessions.command("rm")
@click.argument("session_id")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
def sessions_rm(session_id: str, yes: bool) -> None:
    cfg = Config.load()
    store = SessionStore(cfg.state_dir)
    if not yes and not click.confirm(f"delete session {session_id}?", default=False):
        return
    if store.delete(session_id):
        click.echo("deleted")
    else:
        click.secho("not found", fg="red")
        sys.exit(2)


@cmd_sessions.command("new")
def sessions_new() -> None:
    cfg = Config.load()
    store = SessionStore(cfg.state_dir)
    s = Session.new(model=cfg.model)
    store.save(s)
    click.echo(s.id)


@main.command("doctor")
@workspace_option
@state_dir_option
def cmd_doctor(workspace_override: str | None, state_dir_override: str | None) -> None:
    """Sanity-check the install."""
    cfg = _apply_overrides(Config.load(), workspace_override, state_dir_override)
    click.echo(f"model:        {cfg.model}")
    click.echo(f"ollama:       {cfg.ollama_host}")
    click.echo(f"workspace:    {cfg.workspace}")
    click.echo(f"state dir:    {cfg.state_dir}")
    click.echo(f"allow net:    {cfg.allow_network}")
    click.echo(f"max steps:    {cfg.max_steps}")

    async def _check() -> int:
        async with _build_client(cfg) as client:
            ok = await client.ping()
            click.echo(f"ollama ping:  {'ok' if ok else 'FAIL'}")
            if not ok:
                return 1
            try:
                r = await client.chat(
                    messages=[{"role": "user", "content": "Reply with the word 'ok'."}],
                    system="You are terse.",
                )
                click.echo(f"model reply:  {r.text.strip()[:60]!r}")
            except Exception as exc:  # noqa: BLE001
                click.secho(f"model FAIL:   {exc}", fg="red")
                return 1
        return 0

    sys.exit(asyncio.run(_check()))


@main.group("memory")
def cmd_memory() -> None:
    """Inspect or edit MEMORY.md in the workspace."""


@cmd_memory.command("show")
@workspace_option
def memory_show(workspace_override: str | None) -> None:
    cfg = _apply_overrides(Config.load(), workspace_override, None)
    p = cfg.workspace / "MEMORY.md"
    if not p.exists():
        click.echo("(no MEMORY.md)")
        return
    click.echo(p.read_text(encoding="utf-8"))


@cmd_memory.command("edit")
@workspace_option
def memory_edit(workspace_override: str | None) -> None:
    cfg = _apply_overrides(Config.load(), workspace_override, None)
    p = cfg.workspace / "MEMORY.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(
            "# MEMORY.md\n\nDurable notes hermit will read on every turn.\n",
            encoding="utf-8",
        )
    editor = os.environ.get("EDITOR") or shutil.which("vim") or shutil.which("vi") or "vi"
    subprocess.call([editor, str(p)])


@cmd_memory.command("path")
@workspace_option
def memory_path(workspace_override: str | None) -> None:
    cfg = _apply_overrides(Config.load(), workspace_override, None)
    click.echo(str(cfg.workspace / "MEMORY.md"))


if __name__ == "__main__":
    main()
