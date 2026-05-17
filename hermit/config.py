"""Env-loaded config. Fail fast if required vars are missing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    load_dotenv(dotenv_path=Path.home() / ".hermit" / ".env", override=False)


@dataclass(frozen=True)
class Config:
    model: str
    ollama_host: str
    workspace: Path
    state_dir: Path
    allow_network: bool
    max_steps: int
    timeout_sec: float
    max_history_messages: int
    max_history_chars: int

    @classmethod
    def load(cls) -> "Config":
        _load_env()

        model = os.environ.get("AGENT_MODEL", "gemma4:e4b")
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        workspace = Path(os.environ.get("HERMIT_WORKSPACE", "./workspace")).expanduser()
        state_dir = Path(os.environ.get("HERMIT_STATE_DIR", "~/.hermit")).expanduser()

        workspace.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            model=model,
            ollama_host=host,
            workspace=workspace.resolve(),
            state_dir=state_dir.resolve(),
            allow_network=os.environ.get("HERMIT_ALLOW_NETWORK", "0") == "1",
            max_steps=int(os.environ.get("HERMIT_MAX_STEPS", "8")),
            timeout_sec=float(os.environ.get("HERMIT_TIMEOUT_SEC", "120")),
            max_history_messages=int(os.environ.get("HERMIT_MAX_HISTORY_MESSAGES", "40")),
            max_history_chars=int(os.environ.get("HERMIT_MAX_HISTORY_CHARS", "24000")),
        )
