# luban

A small terminal coding agent. It reads and searches your project files,
proposes edits (with a diff you confirm), and runs shell commands — all in a
tool-use loop against an Anthropic-compatible client you provide.

## Quick start

```bash
# 1. install (into the Python env that has your client package)
pip install git+https://github.com/HashtagYJM/luban.git

# 2. one-time: create ~/.luban/client_local.py with your build_client()
#    (Windows: C:\Users\<you>\.luban\client_local.py) — see client_local.example.py

# 3. run — a real command now, no `python -m`
luban
```

At the `you>` prompt, type what you want in plain English; luban reads your
files, shows a diff before any change (`y`/`n`), and can run commands. `/exit`
to quit.

## Install

Requires Python 3.11. Install into the environment that has your
Anthropic-compatible client package:

```bash
pip install git+https://github.com/HashtagYJM/luban.git
```

(or, from a local clone: `pip install .`). This puts a **`luban`** command on
your PATH — so after activating your environment you can just type `luban`.

## Configure your client (once)

luban needs a `build_client()` that returns your Anthropic-compatible client.
Create the file **`~/.luban/client_local.py`** (on Windows:
`C:\Users\<you>\.luban\client_local.py`) — see `client_local.example.py` for the
shape:

```python
def build_client():
    # return any client exposing .messages.create(...) / .messages.stream(...)
    ...
```

This file is yours and is never committed. (You can also point the
`LUBAN_CLIENT_LOCAL` environment variable at a file instead.)

## Run

```bash
conda activate <your-env>
luban                       # session opens; operates on the current folder
luban --dir path/to/project # operate on another folder
luban --auto                # skip confirmations
luban --no-stream           # if responses come back empty (some reasoning models)
luban --model <id>          # pick a model
```

In-session commands: `/auto`, `/model <id>`, `/clear`, `/exit`.

> **Warning:** `--auto` (and the `/auto` command) run file writes and shell
> commands WITHOUT asking. Only use it in a project directory you trust.

## Config

luban reads **`~/.luban/config.toml`**, created automatically on first run with
your detected platform. Edit it any time:

```toml
# ~/.luban/config.toml — luban settings (edit me)
platform = "windows"   # windows | mac | linux
```

`platform` tells the assistant which shell conventions to use (e.g. Windows
`dir`/`type` vs. POSIX `ls`/`cat`).

## Troubleshooting

- **No text appears (empty responses) in streaming mode?** Some reasoning
  models stream only their internal "thinking" and no visible text. Run with
  `--no-stream` — the full response is returned correctly without streaming.
