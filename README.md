# hermit

An autonomous, privacy-first local personal agent powered by Ollama and Gemma. Talk to it from your terminal, Telegram, WhatsApp, or Google Chat — every model call stays on your machine.

> **Status:** design-stage. See [`DESIGN.md`](DESIGN.md) (local agent core) and [`DESIGN-v2.md`](DESIGN-v2.md) (channels) for the full build spec. v1 ships the combination of both.

---

## Why hermit

- **Local only.** Every model call hits `http://localhost:11434` (Ollama). No outbound to OpenAI, Anthropic, or Google.
- **Single user.** No auth, no multi-tenancy. Your machine is the trust boundary.
- **Transparent state.** Sessions, memory, and config are human-readable files (`.json`, `.md`, `.env`). `cat`, `git diff`, and `vim` work.
- **Small, replaceable core.** Model client, tool registry, session store, and each channel are one file behind a tiny interface. Swap Ollama for llama.cpp later in a 50-line patch.
- **Chat-from-anywhere.** One agent, four front-ends (CLI, Telegram, WhatsApp, Google Chat). Same session can be driven from any of them.

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

### Channels (v2 additions)
- **Long-running daemon** (`hermit serve`) so chat channels can deliver inbound messages 24/7.
- **`Channel` abstraction** — one tiny Protocol (`start`, `send`, `stop`) plus a shared `InboundMessage` queue. CLI, Telegram, WhatsApp, Google Chat all implement it the same way.
- **Router with per-peer sessions** — `(channel, peer)` → session, editable via `~/.hermit/router.json`. Fuse Telegram + WhatsApp into one conversation, or keep them separate.
- **Allowlist + DM pairing** — unknown senders get a single pairing-code reply, then are silently dropped. Approve via `hermit pair approve <CODE>`.
- **Confirm-over-chat** — when a tool needs `y/n`, the agent sends the question as a chat message and parks the session until you reply.
- **Telegram** — native Python (`python-telegram-bot`), long polling, no public URL required.
- **WhatsApp** — via a local Baileys/whatsmeow bridge sidecar (wuzapi recommended). Hermit talks to the bridge over loopback; the bridge holds your linked-device session.
- **Google Chat** — Workspace bot via service account, inbound through a Cloudflare Tunnel / Tailscale Funnel that terminates at `localhost:8787`.
- **`exec` is off for chat channels by default.** Chat peers can read/write files; only the CLI can run shell. Toggle with `HERMIT_ALLOW_CHANNELS_TOOL_EXEC=1` at your own risk.

### Operational
- `hermit doctor` — pings Ollama, validates each enabled channel, prints workspace and state paths.
- `hermit sessions list|show|rm|new` — inspect and manage the session log directly.
- `hermit allow|deny <channel> <peer>` — allowlist management.
- launchd (macOS) and systemd user-unit templates under `deploy/` for always-on hosting.

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

**One direction of dependency.** Nothing imports the CLI; nothing in the model/tool/session layers imports each other. The daemon binds `127.0.0.1` only — never `0.0.0.0`. The only inbound HTTP path is the GChat webhook, which arrives via tunnel.

---

## Project layout

```
hermit/
├── README.md
├── DESIGN.md                    # v1 build spec (local agent core)
├── DESIGN-v2.md                 # v2 build spec (channels delta)
├── LICENSE
├── pyproject.toml               # uv / pip-installable
├── .env.example                 # commented env var template
├── hermit/                      # package
│   ├── __init__.py
│   ├── __main__.py              # `python -m hermit` entrypoint
│   ├── cli.py                   # click commands: chat, run, sessions, serve, allow, pair, doctor
│   ├── config.py                # env loading, defaults
│   ├── agent.py                 # the agent loop
│   ├── ollama_client.py         # HTTP client for /api/chat
│   ├── prompts.py               # build_system_prompt(...)
│   ├── memory.py                # MEMORY.md read/update helpers
│   ├── session.py               # JSON-backed message log
│   ├── daemon.py                # `hermit serve` orchestrator
│   ├── router.py                # inbound-queue consumer + session map
│   ├── allowlist.py             # allowlist + pairing flow
│   ├── confirm.py               # pending_confirm state, parse y/n/always
│   ├── http_server.py           # aiohttp app for webhooks + admin API
│   ├── tools/
│   │   ├── __init__.py          # ToolRegistry, Tool dataclass
│   │   ├── filesystem.py        # read_file, write_file
│   │   ├── shell.py             # exec (confirm-gated)
│   │   └── web.py               # fetch_url (optional, network-gated)
│   └── channels/
│       ├── __init__.py          # Channel Protocol, InboundMessage
│       ├── cli.py               # wraps existing REPL behind Channel
│       ├── telegram.py          # python-telegram-bot, long polling
│       ├── whatsapp.py          # HTTP/WS client to local bridge
│       └── gchat.py             # webhook receiver + REST sender
├── tests/
│   ├── test_agent_loop.py
│   ├── test_tools.py
│   ├── test_ollama_client.py    # mocked HTTP
│   ├── test_router.py
│   └── test_allowlist.py
├── deploy/
│   ├── launchd/                 # macOS plist template
│   ├── systemd/                 # Linux user-unit template
│   └── whatsapp-bridge/         # docker-compose for wuzapi, sample env
├── docs/
│   ├── telegram-setup.md        # BotFather, token, group caveats
│   ├── whatsapp-bridge.md       # bridge sidecar setup
│   └── gchat-setup.md           # GCP + tunnel walk-through
└── workspace/                   # default workspace dir (gitignored)
    └── MEMORY.md                # user-editable persistent memory
```

**~1700-2100 LOC for v1 of the combined spec.** If a file grows past ~300 lines, suspect it's doing too much.

---

## Requirements

- **Python 3.11+**
- **[Ollama](https://ollama.com)** running locally with a tool-calling-capable model pulled.
  - Default in `.env.example` is `AGENT_MODEL=gemma4:e4b`. Verify the tag with `ollama list`. If it's not on your machine, `gemma3:4b`, `gemma3n:e4b`, or `qwen2.5:7b-instruct` are good substitutes.
- **(Optional, for WhatsApp)** A Baileys/whatsmeow bridge — e.g. [`wuzapi`](https://github.com/asternic/wuzapi). Runs as a separate process on `localhost:8788`.
- **(Optional, for Google Chat)** A GCP project with Google Chat API enabled, a service account, and [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) or `tailscale funnel` for the inbound webhook.
- **(Optional, for Telegram)** A bot token from [`@BotFather`](https://t.me/BotFather).

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

# --- Telegram ---
HERMIT_TELEGRAM_ENABLED=0
HERMIT_TELEGRAM_TOKEN=                # from @BotFather

# --- WhatsApp ---
HERMIT_WHATSAPP_ENABLED=0
HERMIT_WHATSAPP_BRIDGE_URL=http://localhost:8788
HERMIT_WHATSAPP_BRIDGE_TOKEN=

# --- Google Chat ---
HERMIT_GCHAT_ENABLED=0
HERMIT_GCHAT_SA_KEYFILE=~/.hermit/secrets/gchat-sa.json
HERMIT_GCHAT_WEBHOOK_AUDIENCE=        # your tunnel URL
HERMIT_GCHAT_BOT_NAME=hermit
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

---

## Security model

Hermit assumes its host machine is the trust boundary. From that follow a few hard rules:

- **CLI is the trust root.** Anyone with terminal access on the host is already trusted. The CLI channel is always allowlisted; `exec` is always available from it.
- **Chat peers are restricted by default.** An allowlisted peer can read files, write files, and (optionally) fetch URLs. They cannot run `exec` unless `HERMIT_ALLOW_CHANNELS_TOOL_EXEC=1` is set. They cannot drive hermit at all until they've been pair-approved.
- **The daemon binds loopback only.** `HERMIT_DAEMON_BIND=127.0.0.1:8787`. Inbound HTTP for Google Chat arrives via Cloudflare Tunnel / Tailscale Funnel — never by exposing hermit on a public interface.
- **Pairing is single-shot.** An unknown peer gets exactly one pairing-code reply, then further messages are silently dropped until the operator approves the code. No spam vector.
- **The confirm gate trusts the transport.** If your Telegram account is compromised, the attacker can reply `yes` to a confirm prompt. Hermit cannot independently verify "this is really you." Don't run hermit if you don't trust your messenger logins.
- **No sandboxing.** Tools run on the host with the same permissions as the daemon. This is the privacy-first trade-off (no Docker dep). If you ever want to isolate `exec`, the cleanest patch is a `--sandbox podman` flag that swaps the handler.
- **WhatsApp bridge ToS risk.** Running a Baileys/whatsmeow bridge on a personal WhatsApp account is technically against WhatsApp's terms. Risk is low for low-volume personal use; non-zero. Don't use this for a public bot. WhatsApp Web sessions are also subject to periodic forced re-pair (~every 2 weeks idle).

---

## Channel quick reference

| Channel | Inbound transport | Outbound | Sidecar | Public URL? | Notes |
|---|---|---|---|---|---|
| CLI | Local REPL | stdout | — | No | Trust root |
| Telegram | Long polling | Bot API | — | No | Easiest. Get token from @BotFather |
| WhatsApp | Local bridge (HTTP+WS) | Local bridge | wuzapi / Baileys / whatsmeow | No | Scan QR with phone on bridge first run |
| Google Chat | Webhook | REST + SA JWT | Cloudflare Tunnel / Tailscale Funnel | Yes (via tunnel) | Workspace only; personal Gmail not supported |

---

## Memory and personality files

In your workspace directory:

- **`MEMORY.md`** (user-editable) — durable preferences, past decisions, behavioral guidelines. Loaded into every system prompt. The agent may suggest edits; you execute them. Cap ~4KB before considering archival to `MEMORY.archive.md`.
- **`SOUL.md`** (optional) — tone/personality overlay. Cap ~1KB.

Both files are plain Markdown. No embeddings, no RAG, no summarization. Hermit reads them on every turn.

---

## Build order

The design docs lay out an inside-out build order. Summary:

1. `ollama_client.py` + smoke test — confirm your model tag actually works.
2. `session.py` + tests — atomic round-trip.
3. `tools/filesystem.py` — workspace containment edge cases.
4. `prompts.py` — system prompt assembly.
5. `agent.py` — the loop, mocks first then real Ollama.
6. `cli.py` — `hermit run` first, then `chat`, then `sessions`.
7. `tools/shell.py` — `exec` with confirm gate.
8. `MEMORY.md` flow end-to-end.
9. *(v2)* `daemon.py` + CLI channel through the queue.
10. *(v2)* Allowlist + pairing primitives.
11. *(v2)* Telegram end-to-end (proves the channel architecture).
12. *(v2)* Confirm-over-chat.
13. *(v2)* WhatsApp via local bridge.
14. *(v2)* Google Chat last (GCP + tunnel setup can eat an afternoon).

If you finish 1–8 you have v1 of v2's CLI experience. 9–12 give you Telegram. 13–14 are real work; do them only if you actually live in those apps.

---

## Roadmap

| Version | What lands |
|---|---|
| v1 (combined) | Local CLI agent + daemon + Telegram + WhatsApp + Google Chat + allowlist + pairing + confirm-over-chat |
| v1.1 | Per-peer tool restrictions; Gmail-as-channel for personal accounts; advisory file locks on session writes |
| v1.2 | Reply threading, attachment passthrough, streaming responses to chat |
| v1.3 | Inbound image support (multimodal Ollama model); voice transcription via local Whisper sidecar |
| v2 (maybe) | MCP server-mode so other clients can use hermit's tools; remote-but-trusted access via Tailscale + token |

---

## License

MIT. See [`LICENSE`](LICENSE).
