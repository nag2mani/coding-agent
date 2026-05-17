"""The agent loop.

Per turn: assemble system prompt, call Ollama, dispatch tool calls, append
results, repeat until the model returns text without tool calls or we hit
`max_steps`. Saves the session after every step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from hermit.ollama_client import ChatResponse, OllamaClient, ToolCall
from hermit.prompts import build_system_prompt
from hermit.session import Message, Session, SessionStore, trim_messages
from hermit.tools import Tool, ToolRegistry


log = logging.getLogger("hermit.agent")


ConfirmFn = Callable[[Tool, dict], Awaitable[bool]]
ChunkFn = Callable[[str], None]


async def _auto_confirm(_tool: Tool, _args: dict) -> bool:
    return True


@dataclass
class TurnResult:
    text: str
    steps: int
    stopped: str  # "final" | "max_steps" | "denied"


class Agent:
    def __init__(
        self,
        client: OllamaClient,
        registry: ToolRegistry,
        store: SessionStore,
        workspace: Path,
        max_steps: int = 8,
        confirm: ConfirmFn = _auto_confirm,
        max_history_messages: int = 40,
        max_history_chars: int = 24_000,
    ) -> None:
        self.client = client
        self.registry = registry
        self.store = store
        self.workspace = workspace
        self.max_steps = max_steps
        self.confirm = confirm
        self.max_history_messages = max_history_messages
        self.max_history_chars = max_history_chars

    async def run_turn(
        self,
        session: Session,
        user_input: str,
        on_chunk: ChunkFn | None = None,
    ) -> TurnResult:
        session.append(Message(role="user", content=user_input))
        self.store.save(session)

        system_prompt = build_system_prompt(self.workspace, self.registry)
        tools_schema = self.registry.to_ollama_schema() or None

        steps = 0
        for steps in range(1, self.max_steps + 1):
            response = await self._call_model(
                session, system_prompt, tools_schema, on_chunk
            )

            assistant_msg = Message(
                role="assistant",
                content=response.text or "",
                tool_calls=(
                    [{"name": c.name, "arguments": c.arguments} for c in response.tool_calls]
                    if response.tool_calls
                    else None
                ),
            )
            session.append(assistant_msg)
            self.store.save(session)

            if not response.tool_calls:
                return TurnResult(text=response.text or "", steps=steps, stopped="final")

            for call in response.tool_calls:
                result_text = await self._dispatch_tool(call)
                session.append(
                    Message(
                        role="tool",
                        content=result_text,
                        tool_name=call.name,
                        tool_call_id=call.id,
                    )
                )
                self.store.save(session)

        return TurnResult(
            text="(hit max_steps without final answer)",
            steps=steps,
            stopped="max_steps",
        )

    async def _call_model(
        self,
        session: Session,
        system_prompt: str,
        tools_schema: list[dict] | None,
        on_chunk: ChunkFn | None,
    ) -> ChatResponse:
        trimmed = trim_messages(
            session.messages,
            max_messages=self.max_history_messages,
            max_chars=self.max_history_chars,
        )
        messages = [m.to_ollama() for m in trimmed]
        log.debug(
            "ollama.chat msgs=%d/%d tools=%s stream=%s",
            len(messages),
            len(session.messages),
            bool(tools_schema),
            bool(on_chunk),
        )

        if on_chunk is None or not hasattr(self.client, "astream_chat"):
            return await self.client.chat(
                messages=messages,
                system=system_prompt,
                tools=tools_schema,
            )

        final: ChatResponse | None = None
        async for chunk in self.client.astream_chat(
            messages=messages,
            system=system_prompt,
            tools=tools_schema,
        ):
            if chunk.delta:
                try:
                    on_chunk(chunk.delta)
                except Exception:  # noqa: BLE001 — never let UI fault stop the loop
                    log.exception("on_chunk callback raised; continuing")
            if chunk.done:
                final = chunk.final
        if final is None:
            # Stream ended without a done marker — shouldn't happen, but be safe.
            final = ChatResponse(text="", tool_calls=[], usage={}, raw={})
        return final

    async def _dispatch_tool(self, call: ToolCall) -> str:
        tool = self.registry.get(call.name)
        if tool is None:
            return f"error: unknown tool {call.name!r}"

        if tool.requires_confirm:
            try:
                approved = await self.confirm(tool, call.arguments)
            except (KeyboardInterrupt, EOFError):
                return "error: user cancelled"
            if not approved:
                return "user denied tool call"

        try:
            return await tool.handler(call.arguments)
        except Exception as exc:  # noqa: BLE001 — surface any tool fault to the model
            log.exception("tool %s failed", call.name)
            return f"error: tool {call.name} raised: {exc}"
