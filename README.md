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

In-session commands: `/auto`, `/model` (list models / switch), `/skills`,
`/skill <name>`, `/compact`, `/reflect`, `/sessions`, `/clear`, `/exit`.

> **Warning:** `--auto` (and the `/auto` command) run file writes and shell
> commands WITHOUT asking. Only use it in a project directory you trust. Deny rules from `[permissions]` still apply under `--auto`.

## Sessions

Every session is saved automatically (after each completed turn) to
`~/.luban/sessions/` — never inside your project folder.

```bash
luban --continue      # -c: reopen the most recent session for this folder
luban --resume        # -r: pick a past session for this folder from a list
luban --resume --all  # pick from every folder's sessions
```

Resuming restores the full conversation and the model it was using, and shows
the last exchange so you remember where you were. In-session: `/sessions`
lists this folder's saved sessions; `/clear` starts a fresh session (the old
one stays on disk). Resuming another folder's session (via --all) moves that
session to your current folder.

## Skills

Teach luban your conventions with plain markdown files — no code. A skill is
one `.md` file whose first line is a one-line description:

```markdown
description: How this project structures research outputs

Raw downloads go in output/raw_data/, computed signals in output/signals/ ...
```

Put personal skills in `~/.luban/skills/` and project skills in
`<project>/.luban/skills/` (commit those with the project — teammates get
them automatically; a project skill overrides a global one with the same
name). The model sees each skill's name and description and loads the full
instructions itself when relevant; `/skills` lists them and `/skill <name>`
applies one to your next message.

## Permissions

Cut down confirmation prompts — and add guardrails — with rules in
`~/.luban/config.toml`:

```toml
[permissions]
allow = ["run_command:python *", "run_command:git status*"]
deny  = ["run_command:del *", "write_file:*.env"]
```

A rule is `"<tool>"` (every call) or `"<tool>:<pattern>"` (glob matched against
the command for `run_command`, the path for file tools). **deny > allow > ask**,
and deny applies even in `--auto` mode. Allowed actions still show their
diff/command — they just skip the prompt. Rules live only in your home config,
never in the project: a cloned repo can't grant itself permissions.

## Project memory

luban looks for a memory file in the project root — **`LUBAN.md`**, then
**`CLAUDE.md`**, then **`AGENTS.md`** (first found wins) — and injects its
contents into every turn as standing project instructions (conventions, layout,
do's and don'ts). Already keeping a `CLAUDE.md` for Claude Code? It just works.
Need luban-specific instructions? Add a `LUBAN.md`; it takes precedence. Commit
the file with the project — teammates get it automatically. Unlike skills
(loaded on demand), project memory is always on.

To pin a specific file instead of the chain, set it in `~/.luban/config.toml`:

```toml
memory_file = "CLAUDE.md"   # exact file to use; no fallback
```

## Long-term memory & SOUL.md

Unlike project memory (per-repo), long-term memory follows *you*. It lives in
your home directory and is loaded at the start of every session, in every
project:

- **`~/.luban/SOUL.md`** — luban's identity when working with you: who you
  are, how it should behave, conventions to always follow. Created with a
  template on first run; it's your file — edit it freely (luban never
  rewrites it).
- **`~/.luban/memory/`** — durable facts, one small `.md` file each, with an
  always-loaded one-line index in `MEMORY.md`.
- **`~/.luban/memory/journal/`** — daily notes; today's and yesterday's are
  loaded automatically.

luban maintains this itself with four tools: `remember` (save/update a fact —
you see a diff and confirm, like any write), `recall` (search memory),
`forget` (delete a stale fact) and `journal` (note what happened). Before
`/compact` summarizes a long conversation, luban first banks anything durable
to memory — so compaction never loses what it learned. Type **`/reflect`**
occasionally to consolidate: it promotes journal items into facts and prunes
stale ones, with your confirmation on every change.

Trust it? Cut the prompts with permission rules:
`allow = ["remember", "journal"]`. Want none of it? `memory_enabled = false`
in `~/.luban/config.toml` turns the whole feature off.

> Note: memory writes are confirmed by default on purpose — text in a cloned
> repo could try to talk the model into planting bad "facts". The confirm
> plus the audit log is your guard.

## Audit log

Every tool call (including denials) is appended to `~/.luban/audit.jsonl` —
timestamp, project, tool, target, decision, error flag. A compliance-friendly
record of everything the agent did.

## Compacting long conversations

`/compact` asks the model to summarize the conversation, saves the full
transcript to disk (still resumable via `--resume`), and continues in a fresh
session seeded with the summary — keeping context small. luban suggests it
when the conversation grows large.

## Config

luban reads **`~/.luban/config.toml`**, created automatically on first run with
your detected platform. Edit it any time:

```toml
# ~/.luban/config.toml — luban settings (edit me)
platform = "windows"   # windows | mac | linux
memory_enabled = true
```

`platform` tells the assistant which shell conventions to use (e.g. Windows
`dir`/`type` vs. POSIX `ls`/`cat`).

## Troubleshooting

- **Reasoning models:** their internal "thinking" is streamed live, dimmed,
  ahead of the answer — so a model that reasons before replying no longer looks
  blank. If you'd rather not stream at all, `--no-stream` returns the full
  response in one go.
- **Shell commands can't hang the session:** commands run with stdin closed
  (interactive prompts end immediately) and are killed — including child
  processes — after their timeout (default 120s, max 600s).
