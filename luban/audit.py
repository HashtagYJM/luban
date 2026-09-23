"""Audit trail — one JSON line per tool call, at ~/.luban/audit.jsonl.

Standard library only. Auditing is a side channel: it must NEVER raise into
the agent loop, so all filesystem errors are swallowed.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from luban import paths

AUDIT_PATH = paths.luban_home() / "audit.jsonl"

# Why the last write failed, for the caller to say so ONCE. The log is best-effort
# diagnostics: a full disk or a locked file must not stop the work, but a trail that
# silently stopped is worse than none, because it reads as "nothing happened".
last_error = ""


def log(entry: dict, path: Path | None = None) -> bool:
    global last_error
    p = path if path is not None else AUDIT_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"ts": datetime.now().isoformat(timespec="seconds"), **entry},
            ensure_ascii=False,
        )
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception as exc:  # auditing must never raise into the agent loop
        last_error = f"{type(exc).__name__}: {exc}"
        return False
