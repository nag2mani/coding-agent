"""HTTP client for Ollama's /api/chat endpoint.

Single-provider, single-method abstraction. If you ever swap Ollama for
llama.cpp or vLLM, this is the only file that needs to change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    """One streaming delta. `done=True` means the final chunk; `final` is
    populated with the assembled ChatResponse."""

    delta: str
    done: bool = False
    final: ChatResponse | None = None


_USAGE_KEYS = (
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "eval_count",
    "eval_duration",
)


def _parse_tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCall]:
    parsed: list[ToolCall] = []
    for i, raw_call in enumerate(raw_calls or []):
        fn = raw_call.get("function") or {}
        name = fn.get("name") or ""
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        parsed.append(ToolCall(name=name, arguments=args, id=f"call_{i}"))
    return parsed


def _parse_usage(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: payload.get(k) for k in _USAGE_KEYS if k in payload}


class OllamaClient:
    def __init__(self, host: str, model: str, timeout_sec: float = 120.0) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OllamaClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def ping(self) -> bool:
        try:
            r = await self._client.get(f"{self.host}/api/tags", timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        options: dict[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload_messages: list[dict[str, Any]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
        if options:
            body["options"] = options
        return body

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> ChatResponse:
        body = self._build_body(messages, system, tools, options, stream=False)
        r = await self._client.post(f"{self.host}/api/chat", json=body)
        r.raise_for_status()
        data = r.json()
        msg = data.get("message") or {}
        return ChatResponse(
            text=msg.get("content") or "",
            tool_calls=_parse_tool_calls(msg.get("tool_calls") or []),
            usage=_parse_usage(data),
            raw=data,
        )

    async def astream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream chunks as they arrive. The final chunk has `done=True`
        and a populated `final` ChatResponse with any tool_calls.

        Notes on Ollama streaming semantics observed against gemma4:e4b:
        - `message.content` and `message.thinking` are separate streams.
          We only yield visible `content` deltas to the caller; `thinking`
          is the model's internal trace and is not echoed.
        - `tool_calls` may appear in any chunk (typically one with
          `done:false`, just before the terminal chunk). We accumulate
          them as they appear, not only at done.
        - The `done:true` chunk carries usage stats but usually has
          empty content / no tool_calls.
        """
        body = self._build_body(messages, system, tools, options, stream=True)
        text_parts: list[str] = []
        accumulated_tool_calls: list[dict[str, Any]] = []
        last_payload: dict[str, Any] = {}

        async with self._client.stream(
            "POST", f"{self.host}/api/chat", json=body
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = chunk.get("message") or {}
                delta = msg.get("content") or ""
                if delta:
                    text_parts.append(delta)
                    yield StreamChunk(delta=delta, done=False)
                if msg.get("tool_calls"):
                    accumulated_tool_calls.extend(msg["tool_calls"])
                if chunk.get("done"):
                    last_payload = chunk

        final = ChatResponse(
            text="".join(text_parts),
            tool_calls=_parse_tool_calls(accumulated_tool_calls),
            usage=_parse_usage(last_payload),
            raw=last_payload,
        )
        yield StreamChunk(delta="", done=True, final=final)
