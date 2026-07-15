# luban

A small terminal coding agent. It reads and searches your project files,
proposes edits as diffs you confirm, and runs shell commands — all in a tool-use
loop against an Anthropic-compatible client you provide.

luban has **no third-party dependencies** — pure standard library — so it
installs from a single self-contained file, no network required.

- [Install](#install)
- [Configure your client](#configure-your-client-once)
- [Run](#run) · [in-session commands](#in-session-commands)
- [Sessions](#sessions)
- [Skills](#skills)
- [Custom tools](#custom-tools)
- [Permissions & the trust model](#permissions--the-trust-model)
- [Memory](#memory)
- [Config](#config)
- [Sync across devices](#sync-across-devices)
- [Troubleshooting](#troubleshooting)

## Install

Requires Python 3.11+. Pick whichever fits your environment.

### A. Offline wheel (best for locked-down/corporate networks)

Download `luban-<version>-py3-none-any.whl` from the
[Releases page](https://github.com/HashtagYJM/luban/releases) and install the
file directly — no internet, no build, no dependencies to resolve:

```bash
pip install --no-index luban-0.5.14-py3-none-any.whl
```

`--no-index` guarantees pip never contacts a package index. This puts a real
**`luban`** command on your PATH (in the active env). Update later by
downloading a newer wheel and adding `--force-reinstall`.

### B. Run from source (no install; update via `git pull`)

With no dependencies, a bare clone runs as-is:

```bash
git clone https://github.com/HashtagYJM/luban.git
cd luban
python -m luban            # operates on the current folder
git pull                  # update any time
```

For a global `luban` command that works from any folder without installing, put
a small shim on your PATH pointing at the clone (and, if your client lives in a
specific env, that env's Python). Windows `luban.bat`:

```bat
@echo off
set PYTHONPATH=C:\path\to\luban
C:\path\to\python.exe -m luban %*
```

macOS/Linux, an executable `luban` on your PATH:

```bash
#!/usr/bin/env bash
PYTHONPATH=/path/to/luban exec /path/to/python -m luban "$@"
```

### C. From a package index

If your environment allows it, a plain source install works too:

```bash
pip install git+https://github.com/HashtagYJM/luban.git   # or: pip install .
```

This builds from source and needs network access, so it can fail behind strict
proxies or in some conda build environments — use A or B there.

## Configure your client (once)

luban needs a `build_client()` that returns your Anthropic-compatible client.
Create **`~/.luban/client_local.py`** (Windows: `C:\Users\<you>\.luban\client_local.py`) —
see `client_local.example.py` for the shape:

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
luban --version             # print the installed version and exit
```

### In-session commands

| Command | What it does |
|---|---|
| `/model [id]` | Show available models, or switch to one |
| `/thinking [on\|off]` | Extended thinking (on by default) |
| `/effort [low\|medium\|high\|xhigh\|max]` | How hard the model reasons |
| `/verbose [on\|off]` | Show or hide the reasoning text |
| `/config` | Every setting in effect, plus your always-on context budget |
| `/auto` | Stop asking before file writes and shell commands |
| `/skills`, `/skill <name>` | List skills; load one into context |
| `/compact` | Summarize a long conversation and keep going |
| `/reflect` | Tidy long-term memory (dedupe, prune, re-index) |
| `/sessions [all]` | List saved sessions — this folder, or every folder |
| `/resume [n\|id\|name]` | Reopen the last session here, or a specific one |
| `/new [title]` | Save the current thread and start another |
| `/title [text]` | Show or rename the current session |
| `/retry` | Resend a prompt whose turn the network killed |
| `/clear` | Start fresh (the old session stays on disk) |
| `/exit` | Leave (the session is already saved) |

> **`--auto` runs file writes and shell commands without asking.** Use it only in
> a project you trust. `deny` rules from `[permissions]` still apply under `--auto`.

## Sessions

Every session is saved automatically after each completed turn, to
`~/.luban/sessions/` — never inside your project folder.

```bash
luban --continue        # -c: reopen the most recent session for this folder
luban --resume          # -r: list this folder's sessions and pick one
luban --resume market   # -r <n|id|name>: jump straight to one, no picker
luban --resume --all    # pick from every folder's sessions
```

Resuming restores the full conversation and the model it was using, and shows the
last exchange so you know where you left off. Resuming another folder's session
(via `--all`) moves it to your current folder — luban warns loudly when that
happens, since it's rarely what you meant.

**Two threads in one folder.** You don't have to name sessions — the first thing
you type becomes the title, and `/sessions` numbers them, so `/resume 2` is always
enough. Naming earns its keep when you run *parallel* threads in one project (say a
long research thread and a quick bug fix), where auto-titles look alike:

```
/new market update        # saves the current thread, starts a named one
/title portfolio notes    # rename the current thread (saved immediately)
/sessions                 # numbered list; the current thread is marked
/resume market            # back into it — by name, number, or id
```

`/resume` takes anything that identifies one session: its `/sessions` number, its
full id, or a fragment of the title or id. If a fragment matches several, luban
lists them instead of guessing. `/new` always saves the thread you're leaving, so
switching never loses work.

luban can look this up itself: the read-only `sessions` tool lists saved sessions
(add `all: true` for every folder), so you can ask what you were working on
recently. Transcripts are plain JSON under `~/.luban/sessions/` — the model can
`read_file` one directly if you ask it to look closer.

## Skills

Teach luban your conventions with plain markdown — no code. Two layouts, mixable
in one directory:

**Flat file** — `<name>.md` whose first line is a one-line description:

```markdown
description: How this project structures research outputs

Raw downloads go in output/raw_data/, computed signals in output/signals/ ...
```

**Folder skill** — `<name>/SKILL.md`, the Claude Code Agent Skills convention, so
skills written for Claude Code drop in unchanged. The folder name is the skill
name; an optional YAML frontmatter block supplies the `description:` (capped at 240
chars). Supporting files can sit beside `SKILL.md` — luban tells the model the
folder path so it can read them itself.

**Where they live and which wins:**

- Personal skills → `~/.luban/skills/`
- Project skills → `<project>/.luban/skills/` (commit them; teammates get them)
- A project skill overrides a global one of the same name; within one directory, a
  flat `<name>.md` beats a `<name>/SKILL.md` folder.

The model sees each skill's name and description and loads the full instructions
itself when relevant. `/skills` lists them; `/skill <name>` applies one to your
next message.

## Custom tools

Give luban your own in-process tools — no MCP, no plugins, just a Python file you
own. Create `~/.luban/tools_local.py` (or point `LUBAN_TOOLS_LOCAL` at a file)
defining a `TOOLS` list:

```python
from my_company_lib import run_query  # your installed internal package

def query_sql(inp, project_root):
    return run_query(inp["sql"], limit=inp.get("limit", 100))

TOOLS = [
    {
        "name": "query_sql",
        "description": "Run a read-only SQL query against the research database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {"type": "integer", "description": "row cap, default 100"},
            },
            "required": ["sql"],
        },
        "handler": query_sql,          # callable(inp: dict, project_root: Path) -> str
        "read_only": True,             # optional: skips the confirm prompt
        "permission_target": "sql",    # optional: rules can match e.g. "query_sql:DROP*"
        "guidance": "Prefer this over raw shell for DB reads; cap rows with `limit`.",
    },                                 # optional: when/how/cross-tool usage hint
]
```

- `description` tells the model *what* the tool is (it rides on the tool schema).
  `guidance` is optional and tells it *when and how* to use it, and how tools in a
  suite combine — that text is folded into the system prompt. Useful once you have
  more than a couple of custom tools.
- Keep heavy company code in an installed internal package; `tools_local.py` should
  be thin wrappers over its entry points.
- Custom tools go through the same permission rules, confirmation prompts, and audit
  trail as built-ins.
- **User-owned only** — luban never loads tools from a project directory, so a cloned
  repo can't inject executable code.
- A malformed entry is skipped with a warning; a broken file disables custom tools
  entirely. Either way luban starts — never a crash.

## Permissions & the trust model

**What luban can reach.** The file tools (`read_file`, `write_file`, `edit_file`,
`list_dir`, `glob`, `grep`) are jailed to the project folder. That's blast-radius
control and clean diff UX — **not** a security boundary. `run_command` is the
deliberate escape hatch: it can do anything you could from a shell, anywhere on the
machine, behind its own confirm or a permission rule.

**Cutting the prompts.** Add rules to `~/.luban/config.toml`:

```toml
[permissions]
allow = ["run_command:python *", "run_command:git status*"]
deny  = ["run_command:del *", "write_file:*.env"]
```

A rule is `"<tool>"` (every call) or `"<tool>:<pattern>"` (a glob against the command
for `run_command`, the path for file tools). Precedence is **deny > allow > ask**,
and **deny holds even under `--auto`**. Allowed actions still show their diff or
command — they just skip the prompt. Rules live only in your home config, never in
the project, so a cloned repo can't grant itself permissions.

**The guardrails, at a glance:**

| Area | Default | Rule |
|---|---|---|
| File tools outside the project | Off | `allow_out_of_tree_file_edits = true` opts in — then out-of-project paths use the same show-diff-and-confirm flow as `run_command` |
| `~/.luban` (memory, skills, config) | Reachable | So luban maintains its own files with visible diffs instead of blind shell one-liners |
| `~/.luban/*.py` (`client_local.py`, `tools_local.py`) | Off-limits | Never read or written by file tools — one holds credentials, the other runs at startup (matched case-insensitively) |
| `~/.luban/audit.jsonl` | Read-only | Can be read but never written through file tools, so the trail can't be edited away |

Want it stricter? Permission rules apply to `~/.luban` paths too, e.g.
`deny = ["write_file:~/.luban/*"]` stops the agent touching its own files at all.

**Audit log.** Every tool call, including denials, is appended to
`~/.luban/audit.jsonl` — timestamp, project, tool, target, decision, error flag. A
compliance-friendly record of everything the agent did.

## Memory

luban has two kinds of memory: **project memory** (per repo, travels with the code)
and **long-term memory** (per person, follows you everywhere).

### Project memory

luban looks in the project root for **`LUBAN.md`**, then **`CLAUDE.md`**, then
**`AGENTS.md`** (first found wins) and injects it into every turn as standing
instructions — conventions, layout, do's and don'ts. Already keep a `CLAUDE.md` for
Claude Code? It just works. Need luban-specific rules? Add a `LUBAN.md`; it wins.
Commit the file so teammates get it. Pin an exact file with `memory_file = "CLAUDE.md"`
in config to skip the chain.

### Long-term memory

Long-term memory follows *you* — it lives in your home directory and loads at the
start of every session, in every project. Here's the whole picture of what luban
remembers and when it's in context:

| What | Where | In every turn? | It's for |
|---|---|---|---|
| **SOUL.md** | `~/.luban/SOUL.md` | Yes | luban's character & standing behavior — shareable as-is |
| **USER.md** | `~/.luban/USER.md` | Yes | who *you* are: name, role, environment |
| **Facts** | `~/.luban/memory/*.md` + `MEMORY.md` index | Index always; full fact via `recall` | durable truths |
| **Journal** | `~/.luban/memory/journal/` | Today + yesterday only | what happened lately |
| **Sessions** | `~/.luban/sessions/` | No — re-read on demand | the full transcript |

**SOUL.md vs USER.md.** SOUL is character and boundaries — not about you, so a
colleague can drop it into their own `~/.luban/` verbatim. USER is the personal
facts. Both are scaffolded from a template on first run and yours to edit; luban can
also update USER.md as it learns about you, always as a diff you confirm. It never
rewrites SOUL.md on its own.

**The golden rule: a diary entry is not a fact.** These three stores are three levels
of compression, not three copies. Session narrative ("we discussed X today") belongs
in the journal; only what's still true in a month, beyond this one project, should
become a fact. Over-saving is what rots an agent's memory — an empty fact store is
healthier than a noisy one.

luban maintains it with four tools, each write shown as a diff you confirm:
`remember` (save/update a fact), `recall` (search), `forget` (delete a stale fact),
and `journal` (note what happened). Facts are only ever written by an explicit
`remember` or during `/reflect` — never by compaction. Run **`/reflect`** now and
then to consolidate: it promotes journal items into facts and prunes stale ones.

> Memory writes are confirmed by default on purpose: text in a cloned repo could try
> to talk the model into planting bad "facts". The confirm plus the audit log is your
> guard. Turn the whole feature off with `memory_enabled = false`.

For the full model — how the three stores map to episodic/semantic memory, the
"LLM wiki" pattern behind the fact store (plain text, greppable, no vector DB), and
how to run your own well — see
**[docs/memory-architecture.md](docs/memory-architecture.md)**.

### Compacting long conversations

`/compact` summarizes the conversation, saves the full transcript to disk (still
resumable via `--resume`), and continues in a fresh session seeded with the summary,
keeping context small. Before it discards anything, luban writes one short journal
line for the segment. luban suggests `/compact` when a conversation grows large.

### Staying on top of issues

When luban's installed version changes, it prints a **"what's new" banner** read from
a `CHANGELOG.md` bundled inside the package (fully offline). It also keeps a
self-improvement tracker at `~/.luban/memory/enhancements.md` — an **Open** table for
field issues and a **Resolved** table for ones a release fixed. On each upgrade, luban
reconciles Open rows against what shipped, so reported issues get revisited instead of
forgotten. Scaffolded on first run; works for everyone, even with the tracker
untouched.

## Config

luban reads **`~/.luban/config.toml`**, created on first run with your detected
platform. The keys:

| Key | Default | What it does |
|---|---|---|
| `platform` | detected | Shell conventions — Windows `dir`/`type` vs POSIX `ls`/`cat` |
| `model` | built-in | Default model (the `--model` flag wins over it) |
| `thinking` | `true` | Adaptive extended thinking |
| `effort` | `"medium"` | Reasoning depth: `low`…`max` |
| `thinking_verbose` | `false` | Stream the reasoning as dim text |
| `max_tokens` | `32000` | Ceiling on one turn: thinking + text + the tool call |
| `memory_enabled` | `true` | Long-term memory |
| `memory_file` | (chain) | Pin the project memory file |
| `warn_tokens` | `150000` | When to suggest `/compact` |
| `allow_out_of_tree_file_edits` | `false` | Let file tools edit outside the project |
| `web_search` | `false` | Server-side web search tool |
| `subagents` | `false` | `spawn_subagent` tool |

**Precedence for the model:** `--model` flag → `model` in config → luban's built-in
default. Leave it unset to use the built-in.

See what's actually in effect with **`/config`** (handy for spotting capabilities you
haven't enabled). After upgrading luban, run **`luban --sync-config`** — it adds any
new keys as commented lines without touching your values, and moves any setting a
`[table]` header accidentally swallowed back to the top level where it takes effect.

**Thinking.** luban requests adaptive thinking at `effort = "medium"` by default, so
capable models reason before answering without over-thinking easy tasks. It runs
silently; `/verbose on` streams it. A backend that doesn't accept these parameters
degrades to a plain request automatically.

### Optional capabilities (off by default)

- **`web_search = true`** — lets the model pull in current information itself instead
  of asking you to paste it. Needs client/model support; set `web_search_tool_type` to
  match your backend (newer models use `web_search_20260209`; the default
  `web_search_20250305` is broadly available).
- **`subagents = true`** — lets the model spawn a fresh **read-only** sub-agent on a
  focused subtask and get back just the answer. It can read, search, and recall, but
  not write files or run commands. Each sub-run costs extra model calls.

## Sync across devices

By default everything lives under `~/.luban`. To keep memory, skills, and config in
sync across machines, point that folder at a cloud-synced location with the
**`LUBAN_HOME`** environment variable and let OneDrive/Dropbox do the syncing:

```
luban --set-home "C:\Users\you\OneDrive\luban"   # Windows: persists it for you
# or set it yourself:
#   Windows:   setx LUBAN_HOME "C:\Users\you\OneDrive\luban"   (new terminal after)
#   mac/Linux: export LUBAN_HOME="$HOME/OneDrive/luban"        (in your shell profile)
```

Point every device at the **same** folder. luban resolves the home once per run and
routes everything through it — no split location. When `LUBAN_HOME` is active, luban
prints the home path at startup and warns if an old `~/.luban` with data is still
lying around.

Why an environment variable and not a config key? Because `config.toml` lives *inside*
the folder you're relocating — it can't point elsewhere without leaving itself behind.

Two caveats:

- **This syncs `client_local.py` too.** If it holds an internal/company client, you're
  copying it to your cloud provider — only do this on a tenant sanctioned to hold it.
- **Don't run luban on two devices at the same instant** against the same folder —
  OneDrive/Dropbox can create conflict copies. One device at a time is safe.

## Troubleshooting

- **Blank responses from a reasoning model.** Its internal thinking streams live and
  dimmed, ahead of the answer, so a reasoning model no longer looks blank. Prefer it
  all at once? `--no-stream` returns the full response in one go.
- **A dropped connection or "overloaded" mid-response.** Corporate gateways cut long
  streaming responses and backends get saturated. luban retries automatically
  (backing off harder on overload, honoring the server's `retry-after`); if retries
  run out, **`/retry`** resends your prompt verbatim — you never lose what you typed. On
  a long turn that keeps getting cut, a lower `/effort` shortens time on the wire.
- **"It said it wrote the file but nothing changed."** A turn that hits `max_tokens`
  mid-write has its truncated tool call dropped. luban now tells you and retries
  smaller; if it recurs, raise `max_tokens` in config or lower `/effort` (thinking
  shares that ceiling with the tool call).
- **A setting in `config.toml` seems ignored.** A `[table]` header (like
  `[permissions]`) captures every key below it, so a top-level setting placed after one
  becomes a table entry nothing reads. Run **`luban --sync-config`** once to move it
  back; startup also warns when this happens.
- **Garbled characters on Windows.** luban pins UTF-8 for all file and terminal I/O
  regardless of OS locale, so arrows, em-dashes, emoji, and CJK read and write
  correctly.
- **Commands can't hang the session.** Shell commands run with stdin closed
  (interactive prompts end immediately) and are killed — including child processes —
  after their timeout (default 120s, max 600s).
