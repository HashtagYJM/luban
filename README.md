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
luban --version             # print the installed version and exit
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

luban can look this up itself, too: the read-only `sessions` tool lists this
folder's saved sessions (or every folder's, with `all: true`) so you can ask
it what you were working on recently. Full transcripts are plain JSON files
under `~/.luban/sessions/` — nothing stops the model from reading one
directly with `read_file` if you ask it to look closer.

## Skills

Teach luban your conventions with plain markdown files — no code. Two
layouts, mixable in the same directory:

- **Flat file** — `<name>.md` whose first line is a one-line description:

  ```markdown
  description: How this project structures research outputs

  Raw downloads go in output/raw_data/, computed signals in output/signals/ ...
  ```

- **Folder skill** — `<name>/SKILL.md`, the Claude Code Agent Skills
  convention, so skills written for Claude Code drop in unchanged. The skill
  name is the folder's name; an optional leading `---`-delimited YAML
  frontmatter block holds a single-line `description:` that feeds the catalog
  (quotes are stripped automatically, and it's capped at 240 characters), and
  any supporting files can sit alongside `SKILL.md` in the folder — when
  luban loads a folder skill it tells the model the folder's path so it can
  read those files itself (via `run_command`).

Put personal skills in `~/.luban/skills/` and project skills in
`<project>/.luban/skills/` (commit those with the project — teammates get
them automatically). Same name, two tie-breakers: a project skill overrides
a global one, and within a single directory a flat `<name>.md` file wins
over a `<name>/SKILL.md` folder. The model sees each skill's name and
description and loads the full instructions itself when relevant; `/skills`
lists them and `/skill <name>` applies one to your next message.

## Custom tools (`tools_local.py`)

Teach luban your own in-process tools — no MCP, no plugins, just a Python file
you own. Create `~/.luban/tools_local.py` (or point `LUBAN_TOOLS_LOCAL` at a
file) defining a `TOOLS` list:

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
        "permission_target": "sql",    # optional: lets rules match e.g. "query_sql:DROP*"
    },
]
```

- Keep the lengthy company code in an installed internal package;
  `tools_local.py` should be thin wrappers over its entry points.
- Custom tools go through the same permission rules, confirmation prompts, and
  audit trail as built-ins. Mutating tools show their input and ask before
  running; `deny` rules block them even in `--auto`.
- The file is **user-owned only** — luban never loads tools from a project
  directory, so a cloned repo can't inject executable code.
- A malformed tool entry is skipped with a warning while the rest still
  load; a broken file (syntax error, `TOOLS` not a list) disables custom
  tools entirely — either way luban starts, never a crash.

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

## Trust model

The project-root jail on the file tools (`read_file`, `write_file`, `edit_file`,
`list_dir`, `glob`, `grep`) is blast-radius control and visible-diff UX, **not**
a security boundary. It keeps ordinary edits confined to the folder you pointed
luban at and makes every change show up as a diff you confirm. `run_command` is
the deliberate escape hatch: it can do anything you could do from a shell,
anywhere on the machine — same as it always could — behind its own confirm (or
a permission rule). When a file tool *is* pointed outside its scope (a path that
escapes the project root, or one that doesn't exist), it now returns a clear
error rather than a misleading empty result, so the agent isn't led to a wrong
"nothing found" conclusion.

If you *want* the file tools to reach outside the project — e.g. to edit a
sibling repo without dropping to `run_command` one-liners — set
`allow_out_of_tree_file_edits = true` in `~/.luban/config.toml`. It's **off by
default** (corporate-safe); when on, an out-of-project path falls through to the
same show-diff-and-confirm flow `run_command` uses (and, like `run_command`,
`--auto` will auto-approve it). Your `~/.luban` area keeps its own guards
regardless (its `.py` files stay off-limits to the file tools).

File tools can also reach **`~/.luban`** — memory, skills, `config.toml` — so
luban maintains its own files the same visible-diff, confirm-first way instead
of falling back to blind shell one-liners. Two things stay off-limits even
there: Python files (`client_local.py`, `tools_local.py` — matched
case-insensitively, so `.PY`/`.Py` are caught too) can never be read or written
by file tools, since one holds your credentials and the other executes code at
startup; and `~/.luban/audit.jsonl` can be read but never written through file
tools, so the trail can't be edited away.

Want it stricter? Permission rules apply to `~/.luban` paths too —
`deny = ["write_file:~/.luban/*"]` stops the agent from touching its own files
at all — and, as above, deny beats `--auto`.

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

## Long-term memory, SOUL.md & USER.md

Unlike project memory (per-repo), long-term memory follows *you*. It lives in
your home directory and is loaded at the start of every session, in every
project:

- **`~/.luban/SOUL.md`** — luban's character and standing behavior: how it
  should work, conventions to always follow, boundaries it should never
  cross. This one's *not* about you — it's shareable as-is, a starter a
  colleague could drop into their own `~/.luban/` verbatim.
- **`~/.luban/USER.md`** — who you are: name, role, expertise, environment —
  the personal facts that used to live in SOUL.md. Both files are created
  with a template on first run and are yours to edit freely; luban also
  reads and can update USER.md itself via the file tools as it learns things
  about you, with the usual diff-and-confirm on every write. luban never
  rewrites SOUL.md on its own.

Both templates now guide with HTML comments (`<!-- like this -->`) instead of
placeholder headings, so an untouched file doesn't render as a wall of empty
sections in a markdown editor. And an untouched template isn't injected into
the system prompt at all — no "blank slate" noise on a fresh install. A
section only shows up once you've actually written something into it.

> **Upgrading from before USER.md?** Your existing `SOUL.md` is left exactly
> as it is — nothing is deleted or migrated automatically. A fresh `USER.md`
> is scaffolded alongside it on your next run. Move any personal facts you'd
> written into SOUL.md over to USER.md whenever it's convenient, or ask luban
> to do it for you — it'll propose the split as a diff for you to confirm,
> same as any other file edit.

- **`~/.luban/memory/`** — durable facts, one small `.md` file each, with an
  always-loaded one-line index in `MEMORY.md`.
- **`~/.luban/memory/journal/`** — daily notes; today's and yesterday's are
  loaded automatically.
- **`~/.luban/memory/enhancements.md`** — a self-improvement tracker,
  scaffolded automatically on first run: an **Open** table for issues seen in
  the field (with a suggested fix) and a **Resolved** table for ones confirmed
  fixed in a later release. It's indexed in `MEMORY.md` like any other fact,
  and edits go through the same file-tool diff/confirm as everything else
  under `~/.luban` — nothing lands in it silently.

luban maintains this itself with four tools: `remember` (save/update a fact —
you see a diff and confirm, like any write), `recall` (search memory),
`forget` (delete a stale fact) and `journal` (note what happened). Durable
facts are only ever written by an explicit `remember` or during `/reflect` —
compaction itself never saves facts. Before `/compact` discards the old
conversation, luban runs a flush turn that writes one short journal entry
summarizing the session; that turn is offered *only* the `journal` tool, and
luban enforces the offered set at dispatch (any other tool call comes back
"Tool not available in this turn"), so compaction is structurally unable to
touch the fact store. Each session segment gets exactly one journal entry:
the flush writes it if you compact, otherwise a single line is written when
you exit — never both. `/compact` resets that per-segment flag, so the next
segment (after the summary) earns its own entry too. Type **`/reflect`**
occasionally to consolidate: it promotes journal items into facts and prunes
stale ones, with your confirmation on every change.

When luban notices its own installed version changed since the last run, it
prints a **"what's new" banner** — read straight from a `CHANGELOG.md` bundled
inside the package, so it works fully offline with no network — and then, on
your next message, reconciles the Open rows in `~/.luban/memory/enhancements.md`
against that release: anything the release fixed is moved to Resolved. So
field-reported issues get revisited automatically on every upgrade instead of
sitting there forgotten. The banner shows for everyone (even with memory
disabled); the tracker reconciliation runs when memory is on. This is a default
feature — a colleague who never touched the tracker still gets it, scaffolded on
first run.

Trust it? Cut the prompts with permission rules:
`allow = ["remember", "journal"]`. Want none of it? `memory_enabled = false`
in `~/.luban/config.toml` turns the whole feature off.

> Note: memory writes are confirmed by default on purpose — text in a cloned
> repo could try to talk the model into planting bad "facts". The confirm
> plus the audit log is your guard.

### The mental model (worth understanding before you rely on it)

These three stores aren't redundant — they're three levels of compression, each
for a different kind of information:

- **Sessions** are the full, verbatim transcript of a conversation — the raw
  record. Kept on disk, re-readable, but *not* injected into future prompts.
- **The journal** is a lightweight diary: a couple of timestamped lines per
  session about what happened. Only today's and yesterday's are loaded, so it
  naturally fades — it's for *what happened*.
- **Facts** are the few durable truths distilled out of all that — a preference,
  a standing decision, an environment truth. They're indexed and injected into
  every future session, so the bar is high: it's for *what stays true*.

The golden rule that keeps memory healthy: **a diary entry is not a fact.**
Session narrative ("we discussed X today") belongs in the journal and the
transcript; only things still true in a month and beyond this one project should
become facts. Over-saving is what makes an agent's memory rot — an empty fact
store is healthier than a noisy one. That's why compaction only ever journals,
and durable facts are saved only by an explicit `remember` or during `/reflect`.

luban's fact store follows the Markdown "LLM wiki" pattern (atomic one-fact
files + a machine-maintained index, à la Karpathy) — plain text, greppable,
git-able, no vector database. If you want to run your own memory well — or set up
a shared starter for your team — see
**[docs/memory-architecture.md](docs/memory-architecture.md)** for the full
model and maintenance practices.

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
platform = "windows"     # windows | mac | linux
model = "your-model-id"  # default model to use
memory_enabled = true
```

`platform` tells the assistant which shell conventions to use (e.g. Windows
`dir`/`type` vs. POSIX `ls`/`cat`). `model` sets the default model; precedence
is **`--model` flag > `model` in config.toml > luban's built-in default** —
leave it unset (or commented out) to fall back to the built-in.
`allow_out_of_tree_file_edits` (default `false`) lets the file tools edit files
outside the current project — see the trust-model section above.

luban requests **adaptive extended thinking** by default with **`effort = "high"`**,
so capable models reason before answering. Adjust per-session with `/thinking
[on|off]` and `/effort [low|medium|high|xhigh|max]`, or set the `thinking` / `effort`
defaults in `config.toml`. A backend that doesn't accept these parameters degrades
to a plain request automatically.

Three more optional capabilities, all **off by default**:

- `web_search = true` — offers the model the API's **server-side web search**
  tool, so it can pull in current information itself instead of asking you to
  paste it. Requires that your client/model supports it; set
  `web_search_tool_type` to match your backend (newer models use
  `web_search_20260209`, the default `web_search_20250305` is broadly available).
- `subagents = true` — offers a `spawn_subagent` tool: the model can run a fresh
  **read-only** sub-agent on a focused subtask (research or investigate in
  parallel) and get back just the answer. The sub-agent can read/search/recall
  but can't write files or run commands. Each sub-run costs extra model calls.

And `recall` now follows `[[wikilinks]]` between memory facts — so a short
"pointer" fact (`path + status + "details live at …"`) that references another by
`[[slug]]` pulls the linked fact in too. Keep project notes as live pointers
rather than stale copies.

luban pins **UTF-8** for all file and terminal I/O regardless of the OS locale,
so arrows, em-dashes, emoji, and CJK read and write correctly on Windows too. And
a session that was closed mid-tool-call (or truncated by the model's token limit)
now resumes cleanly — luban never keeps a history that ends in an unanswered tool
call, and repairs older sessions that do.

## Sync across devices (`LUBAN_HOME`)

By default everything lives under `~/.luban`. To keep your memory, skills, and
config in sync across machines, point that whole folder at a cloud-synced
location with the **`LUBAN_HOME`** environment variable and let OneDrive/Dropbox
do the syncing:

```
luban --set-home "C:\Users\you\OneDrive\luban"   # Windows: persists it for you
# or set it yourself:
#   Windows:  setx LUBAN_HOME "C:\Users\you\OneDrive\luban"   (new terminal after)
#   mac/Linux: export LUBAN_HOME="$HOME/OneDrive/luban"       (in your shell profile)
```

Point every device at the **same** synced folder. luban resolves the home once
per run and routes everything through it — there's no split location. When
`LUBAN_HOME` is active, luban prints the home path at startup, and warns if an
old `~/.luban` with data is still lying around so you don't accidentally run
against two copies.

Why an environment variable and not a config key? Because `config.toml` lives
*inside* the folder you're relocating — it can't point somewhere else without
leaving itself behind. The env var moves the whole thing to one place, which is
the point.

Two caveats worth knowing:

- **This syncs `client_local.py` too.** If that file holds an internal/company
  client, you're copying it to your cloud provider — only do this on a tenant
  that's sanctioned to hold it.
- **Don't run luban on two devices at the same instant** against the same synced
  folder — OneDrive/Dropbox can create conflict copies (`SOUL-DESKTOP.md`) if two
  machines write the same file simultaneously. One device at a time is safe.

## Troubleshooting

- **Reasoning models:** their internal "thinking" is streamed live, dimmed,
  ahead of the answer — so a model that reasons before replying no longer looks
  blank. If you'd rather not stream at all, `--no-stream` returns the full
  response in one go.
- **Shell commands can't hang the session:** commands run with stdin closed
  (interactive prompts end immediately) and are killed — including child
  processes — after their timeout (default 120s, max 600s).
