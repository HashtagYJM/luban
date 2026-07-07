# luban changelog

Release notes, newest first. Bundled inside the package so luban can show
"what's new" and reconcile its enhancement tracker offline, with no network.
Each entry tags the tracker IDs (E-/F-) it resolves.

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
