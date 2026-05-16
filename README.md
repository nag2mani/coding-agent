# Gemma coding agent: Without rate limits

A tiny local coding agent that runs on your machine via [Ollama](https://ollama.com) and uses any tool-capable model (default: `gemma4:e4b`) to read, edit, and run code on your behalf.

No cloud API, no keys — just your local Ollama server and a single Python file.

## How it works

The agent uses Ollama's native tool-calling API. The model emits structured tool calls (not free-text JSON), which the agent executes locally and feeds back. The loop continues until the model returns a final text message with no more tool calls.

Available tools:

| Tool         | Purpose                                                    |
| ------------ | ---------------------------------------------------------- |
| `read_file`  | Read a file (with 1-based line numbers).                   |
| `write_file` | Create a new file or overwrite an existing one.            |
| `edit_file`  | Replace a unique substring in a file.                      |
| `list_dir`   | List directory entries.                                    |
| `run_bash`   | Run a shell command (60s timeout, runs in the working dir).|

All file paths are sandboxed to the working directory — the agent cannot read or write outside `AGENT_WORKDIR`.

## Prerequisites

- Python 3.8+
- [Ollama](https://ollama.com/download) installed and running (`ollama serve`)
- A tool-capable model pulled locally. Default: `gemma4:e4b`
  ```bash
  ollama pull gemma4:e4b
  ```
  Any model whose `ollama show <model>` output lists `tools` under Capabilities will work (e.g. `gemma3`, `llama3.1`, `qwen2.5-coder`).

## Install

```bash
pip install -r requirements.txt
```

## Usage

**Interactive REPL:**

```bash
python3 agent.py
```

```
gemma coding agent  model=gemma4:e4b  workdir=/path/to/project
commands: /reset  /history  /workdir  /exit

you ▸ summarize what's in this directory
```

**One-shot task:**

```bash
python3 agent.py "write a binary search function in search.py and a unittest for it"
```

**REPL commands:**

| Command     | Effect                                |
| ----------- | ------------------------------------- |
| `/reset`    | Clear conversation history.           |
| `/history`  | Print a compact view of the messages. |
| `/workdir`  | Show the working directory.           |
| `/exit`     | Quit.                                 |

## Configuration

All optional, via environment variables:

| Variable          | Default                  | Notes                                          |
| ----------------- | ------------------------ | ---------------------------------------------- |
| `AGENT_MODEL`     | `gemma4:e4b`             | Any tool-capable Ollama model.                 |
| `OLLAMA_HOST`     | `http://localhost:11434` | Point at a remote Ollama if needed.            |
| `AGENT_WORKDIR`   | current directory        | Sandbox for all file operations.               |
| `AGENT_MAX_STEPS` | `25`                     | Max tool-call iterations per task.             |

Example — run against a different project, with a different model:

```bash
AGENT_WORKDIR=~/code/myapp AGENT_MODEL=qwen2.5-coder:7b python3 agent.py
```

## Example session

```
$ python3 agent.py "create a fizzbuzz.py and run it for n=15"

── step 1/25 ──
tool ▸ write_file(content='def fizzbuzz(n):...', path='fizzbuzz.py')
OK: wrote 187 bytes to fizzbuzz.py

── step 2/25 ──
tool ▸ run_bash(command='python3 -c "from fizzbuzz import fizzbuzz; fizzbuzz(15)"')
exit_code: 0
stdout:
1
2
Fizz
4
Buzz
...
FizzBuzz

── step 3/25 ──
assistant ▸ Created fizzbuzz.py and verified output for n=15. The function
prints Fizz/Buzz/FizzBuzz correctly.

✓ done
```

## Troubleshooting

- **`cannot reach ollama at http://localhost:11434`** — start the server with `ollama serve`.
- **`model "X" not found`** — pull it: `ollama pull X`.
- **Tool calls never appear, model just chats** — the model probably lacks tool-calling. Check `ollama show <model>` for `tools` under Capabilities.
- **`python: command not found` from `run_bash`** — your system only has `python3`; ask the agent to use `python3` explicitly, or symlink it.

## Files

- `agent.py` — the entire agent (~280 lines).
- `requirements.txt` — just `ollama>=0.4.0`.
