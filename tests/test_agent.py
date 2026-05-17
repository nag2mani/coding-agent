import asyncio
from pathlib import Path

from hermit.agent import Agent
from hermit.ollama_client import ChatResponse, StreamChunk, ToolCall
from hermit.session import Session, SessionStore
from hermit.tools import Tool, ToolRegistry


class FakeClient:
    """Drop-in for OllamaClient that returns pre-queued responses."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def chat(self, messages, system=None, tools=None, options=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        if not self._responses:
            raise AssertionError("FakeClient ran out of responses")
        return self._responses.pop(0)

    async def astream_chat(self, messages, system=None, tools=None, options=None):
        self.stream_calls.append({"messages": messages, "system": system, "tools": tools})
        if not self._responses:
            raise AssertionError("FakeClient ran out of responses (stream)")
        resp = self._responses.pop(0)
        # split text into a few chunks for realism
        text = resp.text or ""
        if text:
            mid = max(1, len(text) // 2)
            yield StreamChunk(delta=text[:mid])
            yield StreamChunk(delta=text[mid:])
        yield StreamChunk(delta="", done=True, final=resp)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _registry_with_echo() -> ToolRegistry:
    async def handler(args):
        return f"echoed:{args.get('text', '')}"

    reg = ToolRegistry()
    reg.register(
        Tool(
            name="echo",
            description="Echo input.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=handler,
        )
    )
    return reg


def test_no_tool_calls_returns_final(tmp_path: Path) -> None:
    client = FakeClient([ChatResponse(text="hello world")])
    store = SessionStore(tmp_path)
    agent = Agent(client, ToolRegistry(), store, workspace=tmp_path, max_steps=4)
    session = Session.new(model="m")
    result = run(agent.run_turn(session, "say hi"))
    assert result.stopped == "final"
    assert result.steps == 1
    assert result.text == "hello world"


def test_tool_call_then_final(tmp_path: Path) -> None:
    client = FakeClient(
        [
            ChatResponse(
                text="",
                tool_calls=[ToolCall(name="echo", arguments={"text": "hi"}, id="c0")],
            ),
            ChatResponse(text="done"),
        ]
    )
    store = SessionStore(tmp_path)
    reg = _registry_with_echo()
    agent = Agent(client, reg, store, workspace=tmp_path, max_steps=4)
    session = Session.new(model="m")
    result = run(agent.run_turn(session, "echo hi please"))

    assert result.stopped == "final"
    assert result.text == "done"
    tool_msgs = [m for m in session.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "echoed:hi"
    assert tool_msgs[0].tool_name == "echo"


def test_unknown_tool_surfaces_error(tmp_path: Path) -> None:
    client = FakeClient(
        [
            ChatResponse(
                text="",
                tool_calls=[ToolCall(name="bogus", arguments={}, id="c0")],
            ),
            ChatResponse(text="ok, gave up"),
        ]
    )
    store = SessionStore(tmp_path)
    agent = Agent(client, _registry_with_echo(), store, workspace=tmp_path, max_steps=4)
    session = Session.new(model="m")
    result = run(agent.run_turn(session, "do a bogus thing"))
    assert result.stopped == "final"
    tool_msgs = [m for m in session.messages if m.role == "tool"]
    assert "unknown tool" in tool_msgs[0].content


def test_max_steps_cap(tmp_path: Path) -> None:
    # model keeps emitting tool calls forever
    looping = [
        ChatResponse(
            text="",
            tool_calls=[ToolCall(name="echo", arguments={"text": str(i)}, id=f"c{i}")],
        )
        for i in range(10)
    ]
    client = FakeClient(looping)
    store = SessionStore(tmp_path)
    agent = Agent(
        client,
        _registry_with_echo(),
        store,
        workspace=tmp_path,
        max_steps=3,
    )
    session = Session.new(model="m")
    result = run(agent.run_turn(session, "loop"))
    assert result.stopped == "max_steps"
    assert result.steps == 3


def test_confirm_denied(tmp_path: Path) -> None:
    async def denying_confirm(_tool, _args):
        return False

    async def write_handler(args):  # would write if reached
        return "wrote"

    reg = ToolRegistry()
    reg.register(
        Tool(
            name="write_thing",
            description="x",
            parameters={"type": "object", "properties": {}},
            handler=write_handler,
            requires_confirm=True,
        )
    )

    client = FakeClient(
        [
            ChatResponse(
                text="",
                tool_calls=[ToolCall(name="write_thing", arguments={}, id="c0")],
            ),
            ChatResponse(text="acknowledged"),
        ]
    )
    store = SessionStore(tmp_path)
    agent = Agent(
        client,
        reg,
        store,
        workspace=tmp_path,
        max_steps=4,
        confirm=denying_confirm,
    )
    session = Session.new(model="m")
    result = run(agent.run_turn(session, "please write"))
    tool_msgs = [m for m in session.messages if m.role == "tool"]
    assert tool_msgs[0].content == "user denied tool call"
    assert result.text == "acknowledged"


def test_streaming_callback_invoked(tmp_path: Path) -> None:
    client = FakeClient([ChatResponse(text="hello world")])
    store = SessionStore(tmp_path)
    agent = Agent(client, ToolRegistry(), store, workspace=tmp_path, max_steps=4)
    session = Session.new(model="m")
    chunks: list[str] = []
    result = run(agent.run_turn(session, "stream please", on_chunk=chunks.append))
    assert "".join(chunks) == "hello world"
    assert result.text == "hello world"
    assert result.stopped == "final"
    assert client.stream_calls and not client.calls  # used stream path


def test_streaming_with_tool_call(tmp_path: Path) -> None:
    client = FakeClient(
        [
            ChatResponse(
                text="",
                tool_calls=[ToolCall(name="echo", arguments={"text": "hi"}, id="c0")],
            ),
            ChatResponse(text="done"),
        ]
    )
    store = SessionStore(tmp_path)
    agent = Agent(client, _registry_with_echo(), store, workspace=tmp_path, max_steps=4)
    session = Session.new(model="m")
    chunks: list[str] = []
    result = run(agent.run_turn(session, "echo hi", on_chunk=chunks.append))
    assert "".join(chunks) == "done"  # tool-call step has empty text
    assert result.stopped == "final"


def test_session_saved_each_step(tmp_path: Path) -> None:
    client = FakeClient(
        [
            ChatResponse(
                text="",
                tool_calls=[ToolCall(name="echo", arguments={"text": "x"}, id="c0")],
            ),
            ChatResponse(text="done"),
        ]
    )
    store = SessionStore(tmp_path)
    reg = _registry_with_echo()
    agent = Agent(client, reg, store, workspace=tmp_path, max_steps=4)
    session = Session.new(model="m")
    run(agent.run_turn(session, "go"))

    reloaded = store.load(session.id)
    assert reloaded is not None
    roles = [m.role for m in reloaded.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
