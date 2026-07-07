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


def log(entry: dict, path: Path | None = None) -> None:
    p = path if path is not None else AUDIT_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"ts": datetime.now().isoformat(timespec="seconds"), **entry},
            ensure_ascii=False,
        )
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # auditing is a side channel — it must never raise into the agent loop
