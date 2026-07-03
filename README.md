# luban

A small terminal coding agent. It reads and searches your project files,
proposes edits (with a diff you confirm), and runs shell commands — all in a
tool-use loop against an Anthropic-compatible client you provide.

luban has **no third-party dependencies** — it's pure standard library — so it
installs from a single self-contained file with no network access required.

## Install

Requires Python 3.11+. Pick whichever fits your environment.

### A. Offline wheel (best for locked-down/corporate networks)

Download `luban-<version>-py3-none-any.whl` from the
[Releases page](https://github.com/HashtagYJM/luban/releases) and install the
file — no internet, no build, no dependencies to resolve:

```bash
pip install --no-index luban-0.1.0-py3-none-any.whl
```

`--no-index` guarantees pip never contacts a package index. This puts a real
**`luban`** command on your PATH (in the active env). Update later by
downloading a newer wheel and adding `--force-reinstall`.

### B. Run from source (no install; updates via `git pull`)

Since there are no dependencies, a bare clone runs as-is:

```bash
git clone https://github.com/HashtagYJM/luban.git
cd luban
python -m luban            # operates on the current folder
git pull                  # update any time
```

To get a global `luban` command that works from any folder **without**
installing, drop a tiny shim on your PATH pointing at the clone (and, if your
client lives in a specific env, that env's Python). On Windows, `luban.bat`:

```bat
@echo off
set PYTHONPATH=C:\path\to\luban
C:\path\to\python.exe -m luban %*
```

On macOS/Linux, an executable `luban` on your PATH:

```bash
#!/usr/bin/env bash
PYTHONPATH=/path/to/luban exec /path/to/python -m luban "$@"
```

### C. From a package index

If your environment allows it, a plain source install also works:

```bash
pip install git+https://github.com/HashtagYJM/luban.git   # or: pip install .
```

(Note: this builds from source and needs network access, so it can fail behind
strict corporate proxies or in some conda build environments — use A or B
there.)

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

- **Reasoning models:** their internal "thinking" is streamed live, dimmed,
  ahead of the answer — so a model that reasons before replying no longer looks
  blank. If you'd rather not stream at all, `--no-stream` returns the full
  response in one go.
