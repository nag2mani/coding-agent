# Hermit — Design Document

> An autonomous, privacy-first local personal agent powered by Ollama and Gemma.
> Python, single-user, offline-first, no remote model APIs.

This doc is a build spec, not a marketing page. It tells a future implementer (you, or Claude in a later session) exactly what to build, in what order, and why each piece looks the way it does. References to OpenClaw (`/Users/nagmani/nag2mani/openclaw`) point at the *patterns* worth borrowing — not code to fork.

---

## 1. Goals and non-goals

### Goals
- **Local only.** Every model call hits `http://localhost:11434` (Ollama). No outbound to OpenAI/Anthropic/Google.
- **Single user.** No auth, no multi-tenancy, no pairing flows. The host machine is the trust boundary.
- **Transparent state.** Sessions, memory, and config live as human-readable files (`.json`, `.md`, `.env`). You can `cat`, `git diff`, and `vim` everything.
- **Small core, replaceable parts.** Model client, tool registry, and session store are each one file behind a tiny interface. Swapping Ollama for llama.cpp later should be a 50-line patch.
- **Practical day-1 utility.** Read/write files in a workspace, run shell commands, fetch URLs. Anything beyond that is opt-in.

### Non-goals (explicitly)
- Channels (WhatsApp/Telegram/Slack/Discord/etc.) — OpenClaw's bread and butter, not ours.
- Companion apps (macOS menu bar, iOS/Android nodes), voice wake, live canvas.
- Multi-agent routing, sandboxing backends (Docker/SSH/OpenShell), gateway daemon.
- MCP server compatibility (v1 — could be added later as one module).
- Plugin system, extensions marketplace, skills hub.
- Embeddings / RAG / vector search. Memory is plain Markdown.
- Prompt caching, model failover, provider rotation.

If you find yourself building any of the non-goals in v1, stop and re-read this section.

---

## 2. High-level architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      hermit (CLI)                           │
│                                                             │
│   ┌────────────┐    ┌────────────┐    ┌─────────────────┐   │
│   │  chat/run  │───▶│ AgentLoop  │───▶│ system prompt   │   │
│   │  command   │    │            │    │ + MEMORY.md     │   │
│   └────────────┘    │            │    └─────────────────┘   │
│                     │            │    ┌─────────────────┐   │
│                     │            │───▶│  OllamaClient   │───┼──▶ localhost:11434
│                     │            │    └─────────────────┘   │
│                     │            │    ┌─────────────────┐   │
│                     │            │───▶│  ToolRegistry   │   │
│                     │            │    │  (read/write/   │   │
│                     │            │    │   exec/fetch)   │   │
│                     │            │    └─────────────────┘   │
│                     │            │    ┌─────────────────┐   │
│                     │            │───▶│ SessionStore    │───┼──▶ ~/.hermit/sessions/*.json
│                     └────────────┘    └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
        │                                       │
        ▼                                       ▼
   .env / ~/.hermit/.env              workspace/ (user files
                                       hermit reads & writes)
```

Five components, one direction of dependency: `cli → agent_loop → {model_client, tool_registry, session_store, prompts}`. Nothing imports the CLI; nothing in the model/tool/session layers imports each other.

---

## 3. Project layout

```
hermit/
├── README.md
├── DESIGN.md                    # this file
├── LICENSE
├── pyproject.toml               # uv / pip-installable
├── .env.example                 # commented env var template
├── hermit/                      # package
│   ├── __init__.py
│   ├── __main__.py              # `python -m hermit` entrypoint
│   ├── cli.py                   # click commands: chat, run, sessions
│   ├── config.py                # env loading, defaults
│   ├── agent.py                 # the loop (the core file)
│   ├── ollama_client.py         # HTTP client for /api/chat
│   ├── tools/
│   │   ├── __init__.py          # ToolRegistry, Tool dataclass
│   │   ├── filesystem.py        # read, write, edit
│   │   ├── shell.py             # exec (with confirm gate)
│   │   └── web.py               # fetch (optional, v1.1)
│   ├── session.py               # JSON-backed message log
│   ├── prompts.py               # build_system_prompt(...)
│   └── memory.py                # MEMORY.md read/update helpers
├── tests/
│   ├── test_agent_loop.py
│   ├── test_tools.py
│   └── test_ollama_client.py    # mocked HTTP
└── workspace/                   # default workspace dir (gitignored)
    └── MEMORY.md                # user-editable persistent memory
```

**~800–1200 LOC for v1.** If a file grows past 300 lines, suspect it's doing too much.

---

## 4. Data model

Three core types. Keep them as plain dataclasses or TypedDicts — no ORM, no Pydantic v2 magic unless you want validation badly.

### 4.1 `Message`

```python
@dataclass
class Message:
    role: Literal["user", "assistant", "tool", "system"]
    content: str                      # plain text for user/assistant/system
    tool_call_id: str | None = None   # only for role="tool"
    tool_name: str | None = None
    tool_input: dict | None = None    # for role="assistant" when it made a call
    timestamp: float = field(default_factory=time.time)
```

Why plain dataclass and not Pydantic: Ollama's chat API takes `{"role": "...", "content": "..."}` and that's the on-disk format too. Adding validation buys nothing here.

### 4.2 `Session`

```python
@dataclass
class Session:
    id: str                           # uuid4 hex, also the filename
    messages: list[Message]
    model: str                        # which model produced these (audit trail)
    created_at: float
    updated_at: float
```

Persisted as `~/.hermit/sessions/{id}.json`. Write atomically: `write tmp → fsync → rename`.

### 4.3 `Tool`

```python
@dataclass
class Tool:
    name: str                         # e.g. "read_file"
    description: str                  # one sentence, shown in system prompt
    parameters: dict                  # JSON Schema (flat, no nesting)
    handler: Callable[[dict], Awaitable[str]]   # returns string result
    requires_confirm: bool = False    # gate for shell exec, write, etc.
```

Tools are registered in a list. No availability conditions, no plugin loading. If you want a new tool, add a file under `hermit/tools/` and append to the registry.

OpenClaw reference: `src/tools/types.ts:1-98` and `src/agents/pi-tools.ts:1-250`. We're stripping ~95% of that.

---

## 5. The agent loop

This is **the** file. Everything else exists to serve it. Pseudocode:

```python
async def run_turn(session: Session, user_input: str, max_steps: int = 8) -> str:
    session.messages.append(Message(role="user", content=user_input))

    system_prompt = build_system_prompt(workspace_dir, tool_registry)

    for step in range(max_steps):
        response = await ollama.chat(
            model=config.model,
            system=system_prompt,
            messages=session.messages,
            tools=tool_registry.to_ollama_schema(),  # see §6.2
        )

        assistant_msg = Message(
            role="assistant",
            content=response.text or "",
        )
        session.messages.append(assistant_msg)

        if not response.tool_calls:
            session_store.save(session)
            return response.text

        for call in response.tool_calls:
            tool = tool_registry.get(call.name)
            if tool is None:
                result = f"error: unknown tool {call.name}"
            elif tool.requires_confirm and not user_confirmed(call):
                result = "user denied tool call"
            else:
                try:
                    result = await tool.handler(call.arguments)
                except Exception as e:
                    result = f"error: {e}"

            session.messages.append(Message(
                role="tool",
                content=result,
                tool_call_id=call.id,
                tool_name=call.name,
            ))

        session_store.save(session)   # checkpoint after every step

    return "(hit max_steps without final answer)"
```

### What's in here, why
- **`max_steps` cap.** Cheap insurance against runaway loops. 8 is enough for a tool-heavy turn; raise to 20 if you regret it.
- **Save-after-every-step.** If you Ctrl-C mid-loop, you can resume from the partial state. OpenClaw's `session-raw-append-message.ts` does the same — append, don't replay.
- **No compaction.** When the message list overflows context, just truncate the oldest user/assistant pair (keep system + last N). Fancy compaction is a v2 problem. OpenClaw spends a lot of code on this; you don't need to.
- **No retry loop yet.** If Ollama errors, surface it. Retry/backoff is a v1.1 add.

OpenClaw reference: `src/agents/pi-embedded-runner/run.ts:1-300` and `run/attempt.ts`. Their loop carries way more state (usage, stop reasons, compaction triggers, profile failover); ours has 30 lines of substance.

---

## 6. Ollama integration

### 6.1 The HTTP surface

Ollama exposes an OpenAI-style chat completion at `POST /api/chat`. Body:

```json
{
  "model": "gemma4:e4b",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "tools": [...],
  "stream": false,
  "options": {"temperature": 0.7, "num_ctx": 8192}
}
```

Response (non-streaming):

```json
{
  "message": {
    "role": "assistant",
    "content": "...",
    "tool_calls": [
      {"function": {"name": "read_file", "arguments": {"path": "..."}}}
    ]
  },
  "done": true,
  "total_duration": 1234567890
}
```

Use `httpx.AsyncClient` with a generous timeout (120s — local inference on a laptop can be slow on first token). Stream support is nice-to-have for the chat UX; v1 can be non-streaming.

### 6.2 Tool calling — two paths

**Path A: native tool calling (preferred).** Ollama supports a `tools` field on `/api/chat` for models that announce tool-calling capability. As of Ollama 0.3+, models like `llama3.1`, `qwen2.5`, `mistral-nemo` support it cleanly. Gemma's tool-calling support varies by version and quant.

> ⚠️ **Verify the model tag.** The user-supplied default is `gemma4:e4b`. Confirm with `ollama list` and `ollama show <tag>` — at time of writing, the Gemma 3n family ships as `gemma3n:e4b` / `gemma3n:e2b`, and `gemma4` may or may not exist as a tag yet. The env-var default in `.env.example` should match whatever `ollama pull` succeeds for you. Hermit reads `AGENT_MODEL` and trusts it.

**Path B: fenced text fallback.** If the model doesn't honor `tools`, instruct it via system prompt to emit calls as fenced blocks:

```
<tool>
{"name": "read_file", "arguments": {"path": "notes.md"}}
</tool>
```

Parse those with a regex. This is annoying but works for any model. Implement Path A first; keep Path B as a single function `parse_text_tool_calls(text) -> list[ToolCall]` that runs *after* the native parser comes back empty.

### 6.3 The client interface

```python
class OllamaClient:
    def __init__(self, host: str, model: str): ...

    async def chat(
        self,
        messages: list[Message],
        system: str,
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        ...

@dataclass
class ChatResponse:
    text: str
    tool_calls: list[ToolCall]
    usage: dict          # {prompt_tokens, completion_tokens, total_duration}
```

That's it — ~80 lines. OpenClaw's harness layer (`src/agents/harness/selection.ts:88-227`) is the closest analog; it's much larger because it multiplexes Anthropic/OpenAI/Codex. We have one provider.

---

## 7. Tool registry — day-1 tools

Four tools. None of them require remote services.

### 7.1 `read_file`
```
description: Read a UTF-8 text file from the workspace.
parameters:
  path: string (relative to workspace dir)
  max_bytes: int = 65536
```
Refuses paths that escape the workspace (resolve, check `is_relative_to`). No symlink following outside workspace.

### 7.2 `write_file`
```
description: Write or overwrite a UTF-8 text file in the workspace.
parameters:
  path: string
  content: string
requires_confirm: True
```
Same workspace containment. Atomic write (tmp + rename). Confirm gate prints a diff against existing file when applicable, then asks `[y/N]`.

### 7.3 `exec`
```
description: Run a shell command in the workspace. Returns combined stdout+stderr.
parameters:
  command: string
  timeout_sec: int = 30
requires_confirm: True
```
Run with `asyncio.create_subprocess_shell`, `cwd=workspace_dir`. Truncate output at 4KB and tell the model so. Confirm gate prints the command and asks.

### 7.4 `fetch_url` (optional, v1.1)
```
description: HTTP GET a URL and return text body (max 200KB). Outbound only; for reference lookup.
parameters:
  url: string
```
This is the *one* place hermit reaches outside localhost. Gate behind an env flag `HERMIT_ALLOW_NETWORK=1` if you want a strict offline mode.

### Confirm gate UX

For `requires_confirm=True`, the loop pauses and prints:

```
hermit wants to run: exec
  command: rm -rf old_logs/
[y]es  [n]o  [a]lways for this session  >
```

Persist "always" only for the current session — not on disk.

OpenClaw equivalent is the sandbox + per-tool allowlist system in `src/agents/pi-tools.ts`. We're collapsing that into a single per-call yes/no prompt.

---

## 8. System prompt construction

`build_system_prompt(workspace_dir, registry)` returns a single string. Sections, in order:

1. **Identity.** `"You are hermit, a local personal agent running on the user's machine via Ollama. You are offline-first and privacy-respecting."` Two or three lines, no marketing.
2. **Capabilities.** `"You can read and write files in the workspace, run shell commands (with user confirmation), and recall persistent notes from MEMORY.md."`
3. **Tool list.** For each registered tool, one line: `- name(param1, param2): description`. The full JSON Schema goes in the `tools` array of the API call, not the prompt.
4. **Workspace.** `"Workspace directory: {path}. Treat this as your scratch space."`
5. **MEMORY.md content** (if file exists, capped at 4KB). Preface with: `"Persistent user notes. Treat as durable preferences. Suggest edits when relevant; the user updates this file."`
6. **SOUL.md content** (if file exists, capped at 1KB). Tone/personality overlay. Optional.
7. **Behavior guidance.** A short list: `"- Confirm before destructive actions. - Prefer reading before writing. - Quote file paths exactly. - If you don't know, say so."`

Keep the whole thing under ~2000 tokens. Local models with 8k context can't afford a giant preamble.

OpenClaw reference: `src/agents/system-prompt.ts:1-200`. They have many more sections (channels, subagents, auto-reply, compaction guidance) — none apply here.

---

## 9. Session persistence

- **One file per session.** `~/.hermit/sessions/{uuid}.json`.
- **Append-on-save.** The whole file is rewritten each turn (it's small — a few KB to maybe 100KB). Atomic via tmp+rename.
- **Index file.** `~/.hermit/sessions/index.json` lists `{id, title, model, updated_at}` for `hermit sessions list`. Title is derived from the first user message (first 60 chars).
- **No archival, no compression.** A session over a few MB is a sign you should `hermit sessions new`.
- **No encryption.** The directory has user-only permissions (`chmod 700` on creation). If you need at-rest encryption, use FileVault / LUKS at the OS layer.

OpenClaw reference: their `SessionManager` (from the `@earendil-works/pi-coding-agent` library) is far more capable. We don't need any of it.

---

## 10. Config

### `.env.example`
```bash
# Required
AGENT_MODEL=gemma4:e4b
OLLAMA_HOST=http://localhost:11434

# Optional
HERMIT_WORKSPACE=./workspace          # default: ./workspace
HERMIT_STATE_DIR=~/.hermit            # sessions, memory live here
HERMIT_ALLOW_NETWORK=0                # 1 to enable fetch_url
HERMIT_MAX_STEPS=8                    # cap on tool-call iterations per turn
HERMIT_TIMEOUT_SEC=120                # per-request Ollama timeout
```

### Loading
- `python-dotenv` reads `./.env`, then `~/.hermit/.env`.
- Process env wins.
- `config.py` exposes a single `Config` dataclass loaded once at startup.

No yaml, no profiles, no validation framework. If a required var is missing, fail fast with a one-line error message that says exactly which var.

OpenClaw equivalent: `.env.example` (96 lines) + `src/config/` (293 files for the gateway/channels/auth). Almost all of that is for things we don't have.

---

## 11. CLI

`hermit` is a Click app with three commands:

### `hermit chat [--session ID] [--workspace DIR]`
Interactive REPL. New session if `--session` is omitted. Prints assistant messages as they come, prompts for confirmation on gated tools, persists after every turn. `Ctrl-C` exits cleanly (session is already saved).

### `hermit run "prompt" [--session ID]`
One-shot: send a single user message, print the assistant's final answer, save the session, exit. Useful for shell scripting.

### `hermit sessions [list|show ID|rm ID|new]`
- `list`: prints id, title, updated_at, message count.
- `show ID`: dumps the session as readable text.
- `rm ID`: deletes the file (confirm first).
- `new`: creates an empty session and prints its id.

### `hermit doctor` (optional)
Pings Ollama, checks the configured model is `ollama pull`ed, prints workspace/state paths. Useful when nothing works.

OpenClaw reference: `openclaw.mjs` + `src/entry.ts:1-220` + `src/cli/`. They handle Node version pinning, compile cache, respawn, multi-command routing. We don't.

---

## 12. Build order

Don't build top-to-bottom. Build inside-out, in this sequence. Each step is a working program you can run.

1. **`ollama_client.py` + a 20-line smoke script.** Verify `await OllamaClient(...).chat([Message("user", "hello")])` returns text from the configured model. **Stop here until this works.** If `gemma4:e4b` isn't a real tag, you'll discover it now, not after writing the agent loop.
2. **`session.py` + tests.** Load/save round-trip. Atomic write. Index file maintained.
3. **`tools/__init__.py` and `tools/filesystem.py`.** Just `read_file` and `write_file`. Unit-test workspace containment edge cases (`../etc/passwd`, symlinks, absolute paths).
4. **`prompts.py`.** Build a system prompt from a registry. Test that empty registry, missing MEMORY.md, etc. all produce valid strings.
5. **`agent.py`.** The loop from §5, wired to mocks first, then real Ollama. **Test path A (native tool calling) before bothering with path B.** If the model you chose doesn't support `tools`, decide now whether to switch models or implement path B.
6. **`cli.py`.** `hermit run` first (easiest to test), then `hermit chat`, then `hermit sessions`.
7. **`tools/shell.py`.** Add `exec` with confirm gate. Try it end-to-end: "create a file called hello.txt with the word hi".
8. **MEMORY.md flow.** Verify the agent can read MEMORY.md from the system prompt and propose edits via `write_file`.
9. **(Optional) `tools/web.py`.** Only if you actually want network fetch.

If you can do steps 1–6 and have a working REPL that can read/write files in the workspace, you've shipped v1. Steps 7–9 are bonus.

---

## 13. Open questions and risks

- **Gemma + tool calling.** Biggest unknown. If `gemma4:e4b` (or whatever you actually pull) doesn't reliably produce tool calls in Ollama's native format, you'll spend time on path B (text-fenced parsing). Mitigation: keep a fallback model like `qwen2.5:7b-instruct` in `.env.example` as a comment — known-good for tool calling.
- **Context window.** Local Gemma variants often ship with 8k context. A long session blows it out fast. v1 mitigation: truncate the oldest non-system messages. v2: a real compaction step.
- **Latency.** First-token latency on a 4B model on Apple Silicon is single-digit seconds; on CPU it can be 30s+. Streaming responses (`stream=true`) hides this. Worth implementing in v1.1 if `chat` mode feels sluggish.
- **Shell exec safety.** The confirm gate is a thin guard. Anyone who can type at hermit's REPL can run arbitrary commands. Not a vulnerability per se — that's the design — but document it in README so nobody is surprised.
- **No sandboxing.** Unlike OpenClaw, hermit runs every tool on the host. That's the privacy-first trade-off (no Docker dep). If you ever want to isolate the exec tool, the cleanest path is a `--sandbox podman` flag that swaps the handler.

---

## 14. Appendix: openclaw references for future plundering

When you outgrow v1, these are the openclaw files most worth re-reading:

| Area | OpenClaw path | What's worth stealing |
|---|---|---|
| Agent loop & retry | `src/agents/pi-embedded-runner/run.ts`, `run/attempt.ts` | The state-machine shape: assistant-text + tool-results accumulator |
| Provider adapter | `src/agents/harness/selection.ts:88-227` | The "one method, one struct out" `AgentHarness` interface |
| Tool descriptors | `src/tools/types.ts`, `src/agents/pi-tools.ts` | JSON Schema for tool params; availability flag pattern |
| System prompt assembly | `src/agents/system-prompt.ts:1-200` | Section ordering, MEMORY.md overlay logic |
| Memory file | `src/memory/root-memory-files.ts:1-64` | The "MEMORY.md is durable, user-edits it" contract |
| Session log shape | `src/agents/session-raw-append-message.ts` | Append-only message log; flush after every step |
| Confirm/sandbox pattern | `src/agents/pi-tools.ts` (search "sandbox") | Per-tool gate; v2 could borrow the policy model |
| CLI entrypoint | `openclaw.mjs`, `src/entry.ts:1-220` | The "fast path on `--version`" trick — useful when startup gets heavy |

Everything else (channels/, apps/, gateway/, daemon/, voice, canvas, MCP, plugins, sandboxing backends) is out of scope and likely will stay that way.

---

## TL;DR for the implementer

- Python package, ~1000 LOC.
- One agent loop file, one Ollama HTTP client, one tool registry, one JSON session store, one prompt builder.
- Four tools day-1: `read_file`, `write_file`, `exec`, optional `fetch_url`.
- Persistent memory is a Markdown file the user edits.
- Three CLI commands: `chat`, `run`, `sessions`.
- Build inside-out: Ollama smoke test first, agent loop last. If the model can't tool-call, find out on day 1, not day 5.
