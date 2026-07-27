# luban changelog

Release notes, newest first. Bundled inside the package so luban can show
"what's new" and reconcile its enhancement tracker offline, with no network.
Each entry tags the tracker IDs (E-/F-) it resolves.

## v0.5.16 — memory that stays small and true

v0.5.15 made `recall`'s keyword matching smarter. That was the wrong fix: it tuned a
mechanism that shouldn't have been carrying the work. This release removes it from the
critical path and puts the effort into **curation** instead.

The reasoning is worth stating, because it changes how you should think about luban's
memory. Your fact store is small — a few dozen atomic notes — and luban already receives
an **index of every fact on every turn**. So looking something up was never a search
problem. It's a *fetch*: pick the entry from the index, read that file. Anthropic's own
agent memory works exactly this way and has no search layer at all.

### Reading memory

- **Fetch by name is now the primary path.** Passing a fact's slug returns that one fact
  directly — no ranking, no competing hits. The tool now tells the model to do this, which
  it previously did not: the parameter had no description at all, so the model guessed with
  prose and landed on the weak path while the exact name sat in its context.
- **A miss no longer implies absence.** "(no matches)" read to the model as *"that fact
  doesn't exist"* — so it would helpfully save a new one, and your store grew a duplicate.
  That was the real engine of memory clutter. The message now points at the index and says
  explicitly not to save a fact just because a search missed.
- Keyword matching still exists for exploring when you don't know the name. It's just no
  longer what the system depends on.

### Curating memory

- **`/reflect` now sees your entire fact store.** It previously inspected memory through
  the same capped search everything else used — about a tenth of what it was asked to
  curate. Rationing the curator is why consolidation never really happened. It now receives
  the whole store, plus a list of facts that look like duplicates of each other.
- **`/reflect` is a procedure, not a wish**: survey → merge duplicates → resolve
  contradictions → delete what's already recorded elsewhere → **graduate** → report.
- **Graduation is the part that was missing.** A preference that keeps coming up about how
  you want work done isn't a look-up detail — it's a standing instruction, and it belongs
  in `USER.md`, which is always in context. A stored fact can't shape behaviour you need
  *before* you'd think to look it up. `/reflect` now proposes that promotion.
- Deleting is safe, and the prompt says so: **every session transcript and journal day is
  kept permanently on disk.** Nothing is lost by pruning a fact, so the bar for keeping one
  should be high.

### When memory outgrows its budget

luban keeps a size budget on the always-on index. That isn't about saving money — the
whole store is a rounding error against the context window. It's that a bloated always-on
block **measurably reduces how well the model follows what's in it**.

Previously, going over budget silently trimmed descriptions to fit — the problem stayed
hidden forever. Now luban also *tells the model*, so it can suggest `/reflect` and explain
why. The budget becomes a prompt to consolidate rather than a quiet quota.

### Also

- Fixed the pre-push leak guard flagging ordinary English: a three-letter internal acronym
  was matching as a substring inside words like *evaluate* and *graduate*. Short acronyms
  now match whole words only; longer identifiers are unchanged.

### Deliberately not built

No fuzzy-matching library, no embeddings or vector database (they'd break the
zero-dependency offline install), no extra model call on every lookup, and no dumping the
whole store into context. Each was considered and rejected for a stated reason — the spec
records them.

## v0.5.15 — recall that finds things, context you can see, and turns that stop paying twice

### `recall` was silently missing facts it had on disk (E26)

The biggest fix here, and the root cause of a problem that looked like something else.

`recall` required **every** word of your query to appear in a fact — one ordinary absent
word ("how", "their", "against") zeroed an otherwise perfect match, and you got
"(no matches)" for a fact sitting right there. Field-reproduced on a real 24-fact store:
**6 of 8 natural-language questions failed.**

That doesn't just make search annoying — it *rots your memory*. The model asks, hears "no
such fact", reasonably concludes it doesn't exist, and saves a near-duplicate. And
`/reflect` couldn't clean up the mess, because `recall` is the tool it inspects memory with.

- Matching is now **scored, not all-or-nothing**: any meaningful word counts, and facts are
  ranked by how many distinct query words they match. Common words ("how", "the", "does")
  no longer count — so a naturally-phrased question finds the fact, while a query of
  nothing but filler correctly matches nothing.
- Plurals and possessives normalise: "coding styles" and "the user's coding style" both
  find `coding-style`.
- **The journal gets its own lane.** It matches line-by-line, so a short query used to bury
  a handful of facts under dozens of diary lines. Facts and journal are capped separately —
  and on truncation the journal keeps its **newest** entries, where before the newest were
  exactly what got cut.

Still pure standard library. No embeddings, no index to rebuild.

### `/context` — see what luban is actually loaded with

New command showing the always-on context sent every turn: each section (SOUL, USER,
project memory, tool guidance, skills, memory index, journal) with its size, the
stable/volatile split, tool-schema cost, and your conversation size.

It reports **real** token counts via the API rather than luban's internal estimate — that
estimate assumes ~4 characters per token and measured **~28% low** against the real
tokenizer, which is why the `/compact` nudge tends to fire late.

### Turns stop re-paying for the same context

luban re-sent the entire system prompt at full price every single turn. It now marks the
**stable** half (identity, your profile, project instructions, tool guidance, skills) as
cacheable — cache reads bill about a tenth of normal input.

Making that work needed a reordering: the **memory index and journal now come last**.
Caching matches on a prefix, so anything that changes invalidates everything after it — and
those two are exactly what luban rewrites mid-session whenever it saves a fact or a journal
line. Kept at the end, a memory write can no longer throw the cache away.

`/context` tells you whether your prefix is actually big enough to cache (below roughly
4,000 tokens nothing caches at all — and the API reports that failure silently). Set
`cache_prompt = false` to turn it off; unsupported backends fall back automatically.

### The tracker can retire an item nobody will ever fix

The enhancement tracker had exactly one way to close an issue: a release fixed it. So
anything *deliberately declined*, or solved outside luban, stayed Open forever — re-probed
on every upgrade, burying the issues that were still real.

An item can now close four ways: a **version** (fixed and verified), **wontfix** (a
deliberate design decision, with the reason recorded), **mitigated** (solved outside luban
core), or **obsolete**. Release notes can carry those verdicts in a **Decisions** section
and luban applies them on upgrade — so a decision reaches your tracker without a code
change having to happen first.

### Also

- Custom tools can contribute usage **`guidance`** to the system prompt, not just a
  description (E25) — useful once you have a suite of tools and the model needs to know
  when to reach for each and how they combine.

### Decisions

- **E22 — wontfix in core.** Standing per-project methodology reattaching at session start
  is best served by the project memory file (`LUBAN.md`/`CLAUDE.md`/`AGENTS.md`), which is
  auto-injected every session. An *enforced* auto-load hook would push skills into the
  always-on budget every session whether relevant or not — the exact opposite of the
  context work in this release. The project-memory gateway is the supported path.

## v0.5.14 — settings that take effect, turns that don't vanish, and a network that fights back

A batch of reliability fixes, most of them about the same failure shape: something goes
wrong and *nothing tells you*. A setting silently ignored, a write silently dropped, a
connection silently cut. Every one of these now speaks up.

### Your config settings actually take effect now (E19 follow-up)

`--sync-config` used to append new keys to the **end** of the file. In TOML, a `[table]`
header (like `[permissions]`) captures every key below it — so any key appended after
your `[permissions]` section became `permissions.effort`, `permissions.thinking`, etc.,
which nothing reads. Your setting was valid, present, and completely ignored.

- New keys are now inserted **above** the first `[table]` header, never at end-of-file.
- `luban --sync-config` now **repairs** an already-broken file: it lifts any swallowed
  top-level setting back above the header, keeping your value exactly as written.
- Every startup now **warns** about any setting that is present but being ignored, naming
  the table that captured it — so this can never hide again.
- `load_config` no longer swallows a parse error in silence; an unreadable `config.toml`
  says so on stderr instead of quietly reverting every setting to its default.

**If a setting of yours seems ignored, run `luban --sync-config` once.** It moves it back.

### A tool call cut off mid-turn no longer vanishes (E23, E24)

`max_tokens` is the ceiling on **one whole turn** — thinking + text + the tool call
combined. The old default (8192) was set before extended thinking existed, and raising
`effort` grows the thinking allocation without moving that ceiling. So reasoning could
consume the budget and the tool call at the end would be **cut off mid-write**.

luban has to strip a half-finished tool call (an unanswered one breaks the next request),
and it used to do that **silently** — so the model announced a write, no file changed, no
error appeared, and the model itself never learned the call was dropped, so it reported
success. That is the "it said it did it but nothing happened" symptom.

- A turn cut off mid-tool-call now tells **you** (a clear warning that nothing was
  written) and tells the **model** (so it retries the write smaller instead of assuming
  success). Bounded retries.
- `max_tokens` is now a **`config.toml` key** (default raised to 32000). Raise it if you
  run high/xhigh effort or ask for large writes. `--no-stream` clamps it (a large
  non-streamed response times out on the wire).
- The system prompt no longer invites the failure: an "I'll write the file now" and the
  actual tool call must be in the **same turn** — luban won't end a turn on work not done.

### The network fights back (transient-drop resilience)

Corporate gateways and proxies cut long-lived streaming responses ("peer closed
connection without sending complete message body") and return overloaded errors under
load. The SDK's own retries cannot cover a stream that dies **after** it started — only a
fresh request can.

- luban now **retries** a turn killed mid-stream, automatically and announced (the
  response restarts, so it says so rather than looking like the model repeating itself).
- **Overload (429/529) backs off far harder** than a dropped stream — and honors the
  server's `retry-after`. Retries are jittered so many clients behind one gateway don't
  march back in lockstep and sustain the overload.
- When retries are exhausted, **`/retry`** resends your prompt verbatim — a flaky gateway
  no longer costs you the message you just typed.
- Failure messages name the real cause (a proxy hung up, or the backend is saturated) and
  the actual remedy — never a misleading "raise your timeout."

### Sessions are named threads you can pick (E21 follow-up)

Running two threads in one project folder now works.

- `/new [title]` saves the current thread and starts another; `/title` renames the
  current one; both make sessions tell-apart-able.
- `/resume <number|id|name>` reopens a specific session (from `/sessions`, now numbered);
  `luban -r <number|id|name>` does the same from the shell. Bare `/resume` and `-r` are
  unchanged. `/sessions all` spans every folder.
- Fixed: switching threads used to carry the journal flag across, so the thread you
  switched *to* silently skipped its journal entry.

### Memory hygiene (H1–H3)

- The journal window now shows the two most recent **non-empty** days, so it no longer
  goes blank after a weekend gap.
- The memory index, when it overflows its budget, now drops **descriptions** before it
  ever drops a **fact** — so a fact never silently disappears from what luban knows exists
  (roughly 200 facts fit instead of ~50).
- Fixed an inverted cap warning that claimed your *newest* journal entries were being
  dropped when the opposite was true.

### Also

- A rejected file path now names luban's real home directory in the error, so on a
  relocated home (`LUBAN_HOME`) the next attempt can use the right `~/.luban` alias.
- README documents the full in-session command set and the two-threads-in-one-folder
  workflow.

## v0.5.13 — always-on context you can see, and continuity you can trust

**Your always-on files are no longer silently truncated.** Every turn, luban injects
these into the system prompt — and anything past a cap was being dropped with only
the *model* told, never you:

| # | Layer | Cap (chars) |
|---|-------|------|
| 1 | base prompt + platform + memory hygiene | — |
| 2 | **SOUL.md** (identity & standing instructions) | 4,000 |
| 3 | **USER.md** (who you're working with) | **4,000** (was 2,000) |
| 4 | **memory index** (one line per fact) | 4,000 |
| 5 | **journal** (today + yesterday) | 3,000 |
| 6 | **project memory** (LUBAN.md → CLAUDE.md → AGENTS.md) | 8,000 |
| 7 | skills catalog (names + descriptions) | — |

- **Over-cap files now warn YOU** — at startup and in `/config` — naming the file,
  its size, the cap, and how much is being dropped. Previously the `[truncated]`
  marker only ever reached the model, so an over-long USER.md looked like luban
  ignoring your instructions when it had simply never seen them.
- **`USER_MAX` raised 2,000 → 4,000** (peer of SOUL.md). Caps stay: an uncapped
  always-on file bloats every turn with no signal.
- **`/config` prints your always-on budget**, so you can see it before it bites.
- **Write-routing** is now part of luban's memory conventions: a standing preference
  → edit **USER.md**; luban's character → **SOUL.md**; a detail needed only when
  relevant → **remember** (a fact); a repeatable procedure → **a skill**;
  codebase-only → **the project memory file**. And the rule behind it: *never store
  always-on behaviour as a recallable fact — it can't know to recall it before it
  acts.*

**Continuity is restored, not re-narrated.**

- New **`/resume`** restores this project's last session **from its transcript** —
  deterministic and project-scoped, so it can't wander onto another project's thread
  the way inferring "where we left off" from the journal could.
- Resume now **leads with the project name**, and warns loudly if a session belongs
  to a different project.
- On a plain `luban` start, if this folder has a saved session (e.g. one you
  compacted then exited), luban **reminds you it's there** and how to resume it —
  so a compacted session no longer looks lost. Set `auto_continue = true` to reopen
  it automatically.

**Compaction nudge no longer cries wolf.**

- The token estimate now counts the message **text**, not the Python dict repr (which
  was inflating every count with keys and punctuation).
- The nudge threshold is now the **`warn_tokens`** config key, default **150,000**
  (was a hardcoded 60,000 — a fraction of a modern context window).

## v0.5.12 — UTF-8 across the whole process tree (child processes)

- Holistic fix for the cp1252 family across the whole process tree: luban now sets
  UTF-8 mode (`PYTHONUTF8`) in the environment so every child process it spawns
  starts in UTF-8 — a Python script run via `run_command` no longer crashes on an
  arrow or emoji — and it decodes those children's output as UTF-8 too. This closes
  the "spawned children" surface that E12 (own streams) didn't cover, and a code
  guard now keeps every UTF-8 surface (streams, files, env, child pipes) honest. (E20)

## v0.5.11 — calmer thinking, config discovery, cumulative upgrade notes, grep alias

- Tuned the thinking defaults after field use: **effort now defaults to `medium`**
  (not `high`) so easy tasks stay fast, and thinking now runs **silently by
  default** — no more grey reasoning text on every turn. New `/verbose [on|off]`
  (and `thinking_verbose` in config.toml) shows the reasoning when you want it;
  `/effort` still goes up to `xhigh`/`max`, and `/thinking off` turns it off.
- New: **`/config`** prints your effective settings, and **`luban --sync-config`**
  appends any config keys a newer luban added — as commented lines, preserving all
  your values — so shipped-but-gated features (web search, subagents, thinking
  settings…) are discoverable instead of silently missing from an old config. On
  upgrade luban now points this out. (E19)
- The upgrade "what's new" banner and tracker reconciliation now read the **full
  cumulative span** of releases since your last-seen version, not just the newest —
  so a multi-version jump doesn't miss intermediate fixes. (E17)
- `grep` now resolves the `~/.luban` path alias like the other file tools (still
  never exposing `~/.luban/*.py`). (E18)

## v0.5.10 — extended thinking on by default, adjustable effort

- luban now requests **adaptive extended thinking** by default, with effort set to
  **high** — so capable models actually reason before answering instead of running
  with thinking off. Change either per-session with `/thinking [on|off]` and
  `/effort [low|medium|high|xhigh|max]`, or set the default in config.toml
  (`thinking`, `effort`). Backends that don't support these parameters degrade to a
  plain request automatically.
- Web search turns that hit the API's internal iteration limit (`pause_turn`) now
  resume automatically instead of returning a truncated answer.

## v0.5.9 — web search, subagents, smarter memory

- New (off by default): `web_search = true` in config.toml offers the model the
  API's server-side web search tool, so it can pull in current information instead
  of asking you to paste it. Set `web_search_tool_type` to match your backend. (E11)
- New (off by default): `subagents = true` offers a `spawn_subagent` tool — the
  model can run a fresh read-only sub-agent on a focused subtask (research or
  investigate in parallel) and get back just the answer. (E15)
- `recall` now follows `[[wikilinks]]` between facts, so a short "pointer" fact
  that references another pulls the linked fact in too — keeping project notes as
  live pointers instead of stale copies. (E9)

## v0.5.8 — resume-crash fix, UTF-8 everywhere, optional out-of-tree edits

- Fixed: resuming a session that was closed mid-tool-call (or truncated at
  max_tokens) no longer crashes. luban never persists or replays a history that
  ends in an unanswered tool_use, and repairs already-broken session files on
  resume. A failed turn is reported instead of killing the session. (E14)
- Fixed the cp1252 encoding issue at its root: the standard streams are pinned to
  UTF-8 at startup, `read_file` reads UTF-8, and a policy test now fails the build
  if any file I/O forgets to pin the encoding — so this class of bug can't creep
  back one surface at a time. (E12)
- New (off by default): `allow_out_of_tree_file_edits` in config.toml lets the
  file tools read/write files outside the project (e.g. a sibling repo) via the
  same diff-and-confirm as run_command, instead of forcing clunky shell
  workarounds. Default off for corporate safety. (E16)

## v0.5.7 — file tools reach a relocated home

- Fixed: with `LUBAN_HOME` set to a synced folder (e.g. OneDrive), the file tools
  rejected `~/.luban/…` paths because `~` expanded to the OS home instead of the
  relocated home — so luban couldn't edit its own memory, tracker, config, or
  skills on a synced setup. The `~/.luban` alias now resolves to `LUBAN_HOME`.
  (E10 — a regression from v0.5.5)

## v0.5.6 — Windows write-crash fix + offline upgrade hook

- Fixed a crash when writing files containing non-Latin-1 characters (arrows,
  em-dashes, emoji, CJK) on Windows: all file writes are now UTF-8 and atomic, so
  a failed write can no longer truncate a file to 0 bytes, and an un-encodable
  character is reported instead of crashing the session. (E7, E8)
- `grep` now returns a clear error for a path that doesn't exist or is outside the
  searchable scope, instead of a misleading "(no matches)". (E4a)
- `recall` now matches on fact content by token, so multi-word queries like
  "coding style" find a fact named "yjm-coding-style". (E6)
- Documented that the file tools are intentionally jailed more tightly than
  `run_command` outside the project and ~/.luban. (E4)
- New: on detecting a new installed version, luban prints a "what's new" banner
  from this bundled changelog and reconciles your enhancement tracker against it
  on your next message — offline, and on by default for everyone.

## v0.5.5 — relocatable home for cross-device sync

- `LUBAN_HOME` relocates the whole ~/.luban tree (memory, skills, config,
  sessions, client) to e.g. a OneDrive folder so it syncs across devices;
  `luban --set-home <path>` persists it.

## v0.5.4 — memory that stays clean

- `/compact` no longer writes session narrative into the permanent fact store;
  durable facts come only from an explicit `remember` or `/reflect`.

## v0.5.3 — USER.md split

- Personal user facts moved from SOUL.md into a separate USER.md.

## v0.5.2 — self-improvement batch

- ~/.luban file access, sessions tool, model config key, and the enhancement
  tracker loop. (E1, E2, E3, E5)
