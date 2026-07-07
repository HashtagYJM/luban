"""The single source of truth for luban's home directory.

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
