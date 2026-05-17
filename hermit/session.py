"""JSON-backed session store.

One file per session under {state_dir}/sessions/{id}.json. Atomic writes via
tmp+rename. An index.json lists session metadata for `hermit sessions list`.

Also exposes trim_messages() — a pure helper used by the agent loop to bound
what gets sent to Ollama without touching what's persisted on disk.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


Role = Literal["user", "assistant", "tool", "system"]


@dataclass
class Message:
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    # For assistant turns that called tools: full list of calls, in order.
    # Format: [{"name": str, "arguments": dict}, ...]
    tool_calls: list[dict[str, Any]] | None = None
    timestamp: float = field(default_factory=time.time)

    def to_ollama(self) -> dict[str, Any]:
        """Shape Ollama's /api/chat expects."""
        if self.role == "tool":
            return {
                "role": "tool",
                "content": self.content,
                "tool_name": self.tool_name or "",
            }
        if self.role == "assistant" and self.tool_calls:
            return {
                "role": "assistant",
                "content": self.content,
                "tool_calls": [
                    {"function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in self.tool_calls
                ],
            }
        return {"role": self.role, "content": self.content}


@dataclass
class Session:
    id: str
    messages: list[Message]
    model: str
    created_at: float
    updated_at: float
    title: str = ""
    pending_confirm: dict[str, Any] | None = None

    @classmethod
    def new(cls, model: str) -> "Session":
        now = time.time()
        return cls(
            id=uuid.uuid4().hex[:12],
            messages=[],
            model=model,
            created_at=now,
            updated_at=now,
        )

    def append(self, msg: Message) -> None:
        self.messages.append(msg)
        self.updated_at = time.time()
        if not self.title and msg.role == "user":
            self.title = msg.content.strip().splitlines()[0][:60]


class SessionStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.sessions_dir = self.state_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass
        self.index_path = self.sessions_dir / "index.json"

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def load(self, session_id: str) -> Session | None:
        p = self._session_path(session_id)
        if not p.exists():
            return None
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        msgs = [Message(**m) for m in raw.pop("messages", [])]
        return Session(messages=msgs, **raw)

    def save(self, session: Session) -> None:
        p = self._session_path(session.id)
        tmp = p.with_suffix(".json.tmp")
        payload = asdict(session)
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        self._touch_index(session)

    def delete(self, session_id: str) -> bool:
        p = self._session_path(session_id)
        if not p.exists():
            return False
        p.unlink()
        idx = self._read_index()
        idx.pop(session_id, None)
        self._write_index(idx)
        return True

    def list(self) -> list[dict[str, Any]]:
        idx = self._read_index()
        rows = list(idx.values())
        rows.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
        return rows

    def _touch_index(self, session: Session) -> None:
        idx = self._read_index()
        idx[session.id] = {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": len(session.messages),
        }
        self._write_index(idx)

    def _read_index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        try:
            with self.index_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_index(self, idx: dict[str, dict[str, Any]]) -> None:
        tmp = self.index_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.index_path)


# Always keep at least this many trailing messages, regardless of caps.
_TAIL_FLOOR = 4


def trim_messages(
    messages: list[Message],
    max_messages: int = 40,
    max_chars: int = 24_000,
) -> list[Message]:
    """Drop oldest non-system messages until under both caps.

    Rules:
    - System messages (if any) at the head are always preserved.
    - The most recent _TAIL_FLOOR messages are always preserved.
    - Never strand a `tool` message without its preceding `assistant` that
      called it: when dropping, advance through trailing `tool`/dangling
      assistant turns until the next `user` boundary.
    """
    if not messages:
        return messages

    head: list[Message] = []
    body_start = 0
    for i, m in enumerate(messages):
        if m.role == "system":
            head.append(m)
            body_start = i + 1
        else:
            break
    body = list(messages[body_start:])

    def total_chars(seq: list[Message]) -> int:
        return sum(len(m.content) for m in seq)

    def under_caps(seq: list[Message]) -> bool:
        return len(seq) <= max_messages and total_chars(seq) <= max_chars

    while not under_caps(head + body) and len(body) > _TAIL_FLOOR:
        # Drop one logical turn from the head of body: pop until we land on a
        # boundary (next `user` message starts a fresh exchange).
        body.pop(0)
        while body and body[0].role in ("assistant", "tool") and len(body) > _TAIL_FLOOR:
            body.pop(0)

    return head + body

