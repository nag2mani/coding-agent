# hermit

An autonomous, privacy-first local personal agent powered by Ollama and Gemma. Talk to it from your terminal every model call stays on your machine.

---

## Why hermit

- **Local only.** Every model call hits `http://localhost:11434` (Ollama). No outbound to OpenAI, Anthropic, or Google.
- **Single user.** No auth, no multi-tenancy. Your machine is the trust boundary.
- **Transparent state.** Sessions, memory, and config are human-readable files (`.json`, `.md`, `.env`). `cat`, `git diff`, and `vim` work.
- **Small, replaceable core.** Model client, tool registry, session store, and each channel are one file behind a tiny interface. Swap Ollama for llama.cpp later in a 50-line patch.

---

## Features

### Core (v1 baseline)
- Ollama-backed agent loop with bounded tool-use iteration (default 8 steps/turn).
- **Four day-1 tools**: `read_file`, `write_file`, `exec` (shell), `fetch_url` (optional, network-gated).
- **JSON session store** — one file per session under `~/.hermit/sessions/`, atomically written.
- **`MEMORY.md`** — user-editable Markdown file loaded into every system prompt for durable preferences.
- **`SOUL.md`** (optional) — tone/personality overlay.
- **Workspace containment** — file tools refuse paths that escape the configured workspace directory.
- **Confirm gate** — destructive tools (`write_file`, `exec`) prompt for `y/n/always` before running.
- **Ollama tool calling** with text-fenced fallback for models that don't support native function-calling.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         hermit daemon (serve)                        │
│                                                                      │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐       │
│   │ CLI/REPL │    │ Telegram │    │ WhatsApp │    │  GChat   │       │
│   │ channel  │    │ channel  │    │ channel  │    │ channel  │       │
│   └────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘       │
│        │               │ long-poll     │ ws to         │ webhook     │
│        │               │ /getUpdates   │ baileys       │ via tunnel  │
│        │               │               │ bridge        │             │
│        └────────┬──────┴───────┬───────┴───────┬───────┘             │
│                 ▼              ▼               ▼                     │
│            ┌───────────────────────────────────────┐                 │
│            │      Inbound queue (asyncio.Queue)    │                 │
│            │   items: InboundMessage(channel,peer, │                 │
│            │                        text, attaches)│                 │
│            └────────────────┬──────────────────────┘                 │
│                             ▼                                        │
│            ┌────────────────────────────────────┐                    │
│            │  Router + Allowlist                │                    │
│            │  - drops unknown peers (pairing)   │                    │
│            │  - resolves (channel,peer) → sess  │                    │
│            └────────────────┬───────────────────┘                    │
│                             ▼                                        │
│            ┌────────────────────────────────────────────────────┐    │
│            │              Agent loop                            │    │
│            │  ┌──────────────┐  ┌─────────────┐  ┌───────────┐  │    │
│            │  │ system prompt│  │ OllamaClient│─▶│localhost: │  │    │
│            │  │ + MEMORY.md  │  │  /api/chat  │  │   11434   │  │    │
│            │  └──────────────┘  └─────────────┘  └───────────┘  │    │
│            │  ┌──────────────┐  ┌────────────────────────────┐  │    │
│            │  │ ToolRegistry │  │   SessionStore (JSON)      │  │    │
│            │  │ read/write/  │  │   ~/.hermit/sessions/*.json│  │    │
│            │  │ exec/fetch   │  │                            │  │    │
│            │  └──────────────┘  └────────────────────────────┘  │    │
│            └────────────────┬───────────────────────────────────┘    │
│                             ▼                                        │
│            ┌────────────────────────────────────┐                    │
│            │      Outbound dispatcher           │                    │
│            │  routes reply back to channel.send │                    │
│            └────────────────────────────────────┘                    │
└──────────────────────────────────────────────────────────────────────┘
       │                  │                   │
       ▼                  ▼                   ▼
   Telegram          Baileys bridge       Cloudflare Tunnel
   /api.bot          (Node sidecar,         (public URL →
                      localhost:8788)        localhost:8787)
                                                  │
                                                  ▼
                                          Google Chat events
```

---

## Requirements

- **Python 3.11+**
- **[Ollama](https://ollama.com)** running locally with a tool-calling-capable model pulled.
  - Default in `.env.example` is `AGENT_MODEL=gemma4:e4b`. Verify the tag with `ollama list`. If it's not on your machine, `gemma3:4b`, `gemma3n:e4b`, or `qwen2.5:7b-instruct` are good substitutes.

---

## Quick start (macOS)

A complete walk-through from a fresh machine to a working `hermit chat`. Assumes [Homebrew](https://brew.sh) is installed.

### 1. Install Ollama and pull a model

```bash
brew install ollama
brew services start ollama        # runs Ollama in the background; survives reboots

# Verify the daemon is up
curl -s http://localhost:11434/api/tags

# Pull a tool-calling-capable model (pick one)
ollama pull gemma3:4b              # ~3 GB, fastest
# ollama pull gemma3n:e4b          # ~5 GB, "effective 4B"
# ollama pull qwen2.5:7b-instruct  # ~5 GB, very reliable tool calls

ollama list                        # confirm the tag is local
```

> `gemma4:e4b` is what shows up in `.env.example` as a default — if you don't have it, edit `.env` to point at whichever tag `ollama list` shows.

### 2. Install Python 3.11+ (if needed)

macOS ships an old Python. Either Homebrew or `pyenv` works:

```bash
brew install python@3.12
python3.12 --version              # should print 3.12.x
```

(If you use rbenv/asdf/pyenv, point them at any 3.11+ interpreter.)

### 3. Clone the repo

```bash
cd ~/code                          # or wherever you keep projects
git clone <your-fork-or-this-repo-url> hermit
cd hermit
```

### 4. Create a virtualenv and install hermit

There's **no `requirements.txt`** — dependencies live in `pyproject.toml`. `pip install -e .` reads them from there.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"            # editable install; [dev] adds pytest, respx
```

After this, the `hermit` binary is on your PATH (only while the venv is active).

### 5. Set up config

```bash
cp .env.example .env
# Edit .env if you pulled a different model in step 1:
#   AGENT_MODEL=gemma3:4b
```

Defaults for everything else are fine for a first run.

### 6. Sanity check

```bash
hermit doctor
```

Expected output: model name, Ollama ping `ok`, and a final `model reply: 'ok'`. If anything fails, fix that before continuing.

### 7. Use it

```bash
# One-shot
hermit run "summarize what hermit does in two sentences"

# Interactive REPL
hermit chat

# Inspect saved sessions
hermit sessions list

# Edit durable memory the agent reads on every turn
hermit memory edit
```

That's the whole setup. Sessions live in `~/.hermit/sessions/*.json`. Workspace files (the agent's read/write scratch space) live in `./workspace/` by default — override with `--workspace /path` or `HERMIT_WORKSPACE=/path`.

### Optional: install globally with `pipx`

If you want `hermit` available outside the project dir without activating a venv:

```bash
brew install pipx
pipx install -e .                  # editable; code changes show up live
hermit doctor                      # works from any directory now
```

### Updating after `git pull`

```bash
cd ~/code/hermit
git pull
source .venv/bin/activate
pip install -e ".[dev]"            # picks up any new deps from pyproject.toml
pytest -q                          # confirm nothing regressed (38 tests)
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `command not found: hermit` | `source .venv/bin/activate` (or use `.venv/bin/hermit` directly). |
| `ModuleNotFoundError: No module named 'hermit'` from `python hermit/agent.py` | Don't run module files directly. Use the `hermit` CLI. |
| `Failed to connect to localhost port 11434` | Ollama isn't running. `brew services start ollama` (or `ollama serve` in a terminal). |
| `model 'gemma4:e4b' not found` | `ollama list` to see what you have; edit `.env` to match. Or `ollama pull <tag>`. |
| `hermit run` returns empty / loops | Make sure your model supports tool calling. `gemma3:4b`, `gemma3n:e4b`, `qwen2.5:7b-instruct`, and the `llama3.1` family all do. Smaller/older models often don't. |
| `externally-managed-environment` from `pip install` | You're not in a venv. Run `source .venv/bin/activate` first. |

---

## Configuration

Copy `.env.example` to `.env` (cwd) or `~/.hermit/.env`. Process env wins; cwd `.env` wins over `~/.hermit/.env`.

```bash
# --- Required ---
AGENT_MODEL=gemma4:e4b
OLLAMA_HOST=http://localhost:11434

# --- Core ---
HERMIT_WORKSPACE=./workspace
HERMIT_STATE_DIR=~/.hermit
HERMIT_ALLOW_NETWORK=0                # 1 to enable fetch_url
HERMIT_MAX_STEPS=8                    # tool-call iterations per turn cap
HERMIT_TIMEOUT_SEC=120                # per-request Ollama timeout

# --- Daemon ---
HERMIT_DAEMON_BIND=127.0.0.1:8787     # admin + webhooks. NEVER 0.0.0.0
HERMIT_LOG_LEVEL=INFO
HERMIT_PAIRING_TTL_SEC=600

# --- Safety ---
HERMIT_ALLOW_CHANNELS_TOOL_EXEC=0     # chat channels can't run `exec` unless 1
```

---

## Usage

### CLI (single-shot or REPL)

```bash
hermit run "summarize TODO.md and suggest the next three things to ship"

hermit chat                          # new session, interactive REPL
hermit chat --session <id>           # resume a specific session

hermit sessions list
hermit sessions show <id>
hermit sessions rm <id>
```

### Daemon (chat channels)

```bash
hermit serve                                    # enables channels per .env
hermit serve --channels telegram                # explicit override
hermit serve --channels telegram,whatsapp,gchat
```

### Allowlist + pairing

```bash
hermit allow telegram 123456789
hermit deny telegram 123456789

hermit pair list
hermit pair approve 482915
hermit pair deny 482915
```

### Routing (which (channel, peer) maps to which session)

```bash
hermit router show
hermit router pin telegram 123456789 <session_id>
```

### Diagnostics

```bash
hermit doctor    # pings Ollama, validates each enabled channel
```
