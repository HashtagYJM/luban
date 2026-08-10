"""luban's process-wide filesystem invariants: where its tree lives, and how a file in
that tree is replaced.

Both belong here for the same reason — everything else imports this module and it imports
nothing, so there is exactly one answer to each question and no import cycle. `tools.py`
imports `memory`, so the write helper cannot live in `tools.py` where it started.

## The home directory

By default luban keeps everything (memory, sessions, skills, config, the local
client) under ``~/.luban``. Set the ``LUBAN_HOME`` environment variable to point
that whole tree somewhere else — e.g. a OneDrive/Dropbox folder — so it follows
you across devices.

The location is read **only** from the environment, never from a config file or
any project file: the home dir is the trusted, user-owned root for long-term
memory, permission rules, and the file-tool jail, so a cloned repo must never be
able to redirect it. It is resolved **once per process** (cached) so every module
agrees on exactly one location — no split that could produce duplicate or
out-of-sync memory/journal copies.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def luban_home() -> Path:
    """Resolve luban's home dir: ``$LUBAN_HOME`` if set, else ``~/.luban``.

    Cached: computed once and reused, so all callers share one location. Tests
    that vary ``LUBAN_HOME`` mid-process must call ``luban_home.cache_clear()``.
    """
    env = os.environ.get("LUBAN_HOME")
    base = Path(env).expanduser() if env else Path.home() / ".luban"
    return base.resolve()


def atomic_write_text(target: Path, text: str) -> None:
    """Write UTF-8 to a temp file, then os.replace over the target.

    `Path.write_text` opens with mode "w", which TRUNCATES the target to zero bytes before
    the first byte of new content is written — so every failure in between destroys content
    that was never at risk of being wrong. On a full disk or a quota'd home that outcome is
    certain rather than racy: the truncate always succeeds and the write always fails.
    Writing a temp and renaming inverts the order, so nothing is destroyed until the
    replacement is complete on disk and a reader sees the whole old file or the whole new
    one, never a gap.

    Always UTF-8, never the platform default codec — cp1252 on Windows cannot encode
    arrows, em-dashes or emoji, and would raise mid-write.

    `os.replace`, not `os.rename`: only `replace` overwrites an existing file on Windows.

    Raises on failure, leaving the target untouched; callers decide whether that is a tool
    error, a swallowed best-effort, or a message to the user.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
