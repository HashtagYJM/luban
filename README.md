# luban

A small terminal coding agent. It reads and searches your project files,
proposes edits (with a diff you confirm), and runs shell commands — all in a
tool-use loop against an Anthropic-compatible client you provide.

## Setup

1. Use a Python 3.11 environment that has your Anthropic-compatible client
   package installed. Install this tool's only dependency:
   ```bash
   pip install rich
   ```
2. Create your client provider (gitignored, never committed):
   ```bash
   cp luban/client_local.example.py luban/client_local.py
   ```
   Edit `build_client()` to return your client (must expose
   `.messages.create(...)`; `.messages.stream(...)` optional).

## Run

```bash
cd <your-project>
python -m luban                 # confirm before writes/commands
python -m luban --auto          # skip confirmations
python -m luban --no-stream     # if your client lacks streaming
python -m luban --model <id>    # pick a model
```

> **Warning:** `--auto` (and the `/auto` command) run file writes and shell commands WITHOUT asking. Only use it in a project directory you trust.

In-session commands: `/auto`, `/model <id>`, `/clear`, `/exit`.
