# luban changelog

Release notes, newest first. Bundled inside the package so luban can show
"what's new" and reconcile its enhancement tracker offline, with no network.
Each entry tags the tracker IDs (E-/F-) it resolves.

## v0.5.11 — calmer thinking defaults (medium + silent)

- Tuned the thinking defaults after field use: **effort now defaults to `medium`**
  (not `high`) so easy tasks stay fast, and thinking now runs **silently by
  default** — no more grey reasoning text on every turn.
- New `/verbose [on|off]` (and `thinking_verbose` in config.toml) shows the
  reasoning text when you want to watch it. `/effort` still goes up to `xhigh`/`max`
  for the hardest tasks, and `/thinking off` turns it off entirely.

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
