# Gemma coding agent: Without rate limits

A tiny local coding agent powered by [Ollama](https://ollama.com?utm_source=chatgpt.com) that can read, edit, and run code directly on your machine using any tool-capable local LLM.

Default model: `gemma4:e4b`

No cloud APIs. No API keys. No external services.
Just:

* a local Ollama server
* one Python file
* local tools

---

# Features

* Local-first coding assistant
* Uses Ollama native tool calling
* Reads and edits files
* Runs shell commands
* Sandboxed workspace access
* Interactive REPL mode
* One-shot task execution
* Works with multiple tool-capable models

---

# Architecture

The agent uses Ollama's native tool-calling API.

Instead of generating raw JSON in text, the model emits structured tool calls which are:

1. Executed locally by the agent
2. Returned back to the model
3. Used for the next reasoning step

The loop continues until the model produces a final response without additional tool calls.

---

# Available Tools

| Tool         | Description                          |
| ------------ | ------------------------------------ |
| `read_file`  | Read files with 1-based line numbers |
| `write_file` | Create or overwrite files            |
| `edit_file`  | Replace a unique substring in a file |
| `list_dir`   | List directory contents              |
| `run_bash`   | Execute shell commands (60s timeout) |

---

# Security & Sandboxing

All file operations are restricted to the configured working directory:

```bash
AGENT_WORKDIR
```

The agent cannot access files outside this directory.

---

# Requirements

* Python 3.8+
* [Ollama](https://ollama.com/download?utm_source=chatgpt.com) installed and running
* A tool-capable model pulled locally

Start Ollama:

```bash
ollama serve
```

Pull the default model:

```bash
ollama pull gemma4:e4b
```

Other compatible models include:

* `gemma3`
* `llama3.1`
* `qwen2.5-coder`

You can verify tool support using:

```bash
ollama show <model>
```

Look for:

```text
Capabilities:
  tools
```

---

# Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

Minimal dependency list:

```text
ollama>=0.4.0
```

---

# Usage

## Interactive REPL

Launch the interactive coding assistant:

```bash
python3 agent.py
```

Example:

```text
gemma coding agent  model=gemma4:e4b  workdir=/path/to/project

commands:
  /reset
  /history
  /workdir
  /exit

you ▸ summarize this project
```

---

## One-Shot Tasks

Run a single autonomous task:

```bash
python3 agent.py "write a binary search function in search.py and add tests"
```

Another example:

```bash
python3 agent.py "create a FastAPI hello world app"
```

---

# REPL Commands

| Command    | Action                        |
| ---------- | ----------------------------- |
| `/reset`   | Clear conversation history    |
| `/history` | Show compact message history  |
| `/workdir` | Show active working directory |
| `/exit`    | Exit the agent                |

---

# Configuration

Configuration is handled entirely through environment variables.

| Variable          | Default                  | Description                  |
| ----------------- | ------------------------ | ---------------------------- |
| `AGENT_MODEL`     | `gemma4:e4b`             | Tool-capable Ollama model    |
| `OLLAMA_HOST`     | `http://localhost:11434` | Ollama server endpoint       |
| `AGENT_WORKDIR`   | Current directory        | Sandbox root directory       |
| `AGENT_MAX_STEPS` | `25`                     | Maximum tool-call iterations |

---

# Configuration Example

Run against another project using a different model:

```bash
AGENT_WORKDIR=~/code/myapp \
AGENT_MODEL=qwen2.5-coder:7b \
python3 agent.py
```

---

# Example Session

```text
$ python3 agent.py "create fizzbuzz.py and run it for n=15"

── step 1/25 ──
tool ▸ write_file(
  path='fizzbuzz.py',
  content='def fizzbuzz(n): ...'
)

OK: wrote 187 bytes to fizzbuzz.py

── step 2/25 ──
tool ▸ run_bash(
  command='python3 -c "from fizzbuzz import fizzbuzz; fizzbuzz(15)"'
)

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
assistant ▸ Created fizzbuzz.py and verified output successfully.

✓ done
```

---

# Project Structure

```text
.
├── agent.py
├── requirements.txt
└── README.md
```

| File               | Purpose                   |
| ------------------ | ------------------------- |
| `agent.py`         | Main agent implementation |
| `requirements.txt` | Python dependencies       |
| `README.md`        | Documentation             |

---

# Troubleshooting

## Cannot Reach Ollama

Error:

```text
cannot reach ollama at http://localhost:11434
```

Fix:

```bash
ollama serve
```

---

## Model Not Found

Error:

```text
model "X" not found
```

Fix:

```bash
ollama pull X
```

---

## Model Does Not Use Tools

If the model only chats and never calls tools:

```bash
ollama show <model>
```

Ensure the model supports:

```text
Capabilities:
  tools
```

---

## Python Command Not Found

Error:

```text
python: command not found
```

Use:

```bash
python3
```

or create a symlink if needed.

---

# Design Philosophy

This project intentionally stays:

* minimal
* transparent
* hackable
* dependency-light
* fully local

The goal is to provide a simple foundation for experimenting with local coding agents powered by open models.

---
