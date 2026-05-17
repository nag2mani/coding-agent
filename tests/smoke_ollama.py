"""Smoke test for OllamaClient.

Run: python tests/smoke_ollama.py

Verifies:
  1. /api/tags is reachable.
  2. A plain text completion returns non-empty content.
  3. The configured model emits a native tool_call when given a tools schema.
     (If 3 fails, we'll need the text-fenced fallback path from DESIGN §6.2.)
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hermit.ollama_client import OllamaClient  # noqa: E402


HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("AGENT_MODEL", "gemma4:e4b")


async def main() -> int:
    print(f"host={HOST}  model={MODEL}")

    async with OllamaClient(HOST, MODEL, timeout_sec=120.0) as client:
        if not await client.ping():
            print("FAIL: cannot reach Ollama at", HOST)
            return 1
        print("PASS: ping")

        plain = await client.chat(
            messages=[{"role": "user", "content": "Say the single word: ready"}],
            system="You are a terse assistant.",
        )
        if not plain.text.strip():
            print("FAIL: plain completion returned empty text")
            return 1
        print(f"PASS: plain completion -> {plain.text.strip()!r}")

        tools_schema = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                        },
                        "required": ["city"],
                    },
                },
            }
        ]

        toolish = await client.chat(
            messages=[
                {
                    "role": "user",
                    "content": "What is the weather in Tokyo right now? Use the get_weather tool.",
                }
            ],
            system="You are an assistant with tools. Use a tool when relevant.",
            tools=tools_schema,
        )
        if toolish.tool_calls:
            call = toolish.tool_calls[0]
            print(f"PASS: native tool_call -> {call.name}({call.arguments})")
        else:
            print("WARN: model did not emit a native tool_call.")
            print(f"      text was: {toolish.text[:200]!r}")
            print("      v1 fallback: text-fenced parsing (DESIGN §6.2 path B).")

        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
