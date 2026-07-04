"""Long-term memory — SOUL.md identity, fact store, and daily journal.

Standard library only. All memory lives under the user's home (~/.luban),
never in the project: a cloned repo must not be able to plant global memory.
Every read uses errors="replace" and every function is non-raising — memory
must never break the agent loop. Path constants are module-level and looked
up at call time so tests can monkeypatch them (sessions.py pattern).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

SOUL_PATH = Path.home() / ".luban" / "SOUL.md"
MEMORY_DIR = Path.home() / ".luban" / "memory"

SOUL_MAX = 4000
INDEX_MAX = 4000
JOURNAL_MAX = 3000
RECALL_MAX = 8000

_SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_SOUL_TEMPLATE = (
    "# SOUL.md — who luban is when working with you\n"
    "# Edit freely; luban reads this at the start of every session.\n"
    "\n"
    "## Who I'm working with\n"
    "# (your role, expertise, preferences)\n"
    "\n"
    "## How I should work\n"
    "# (standing behavior, e.g. 'add type hints', 'ask before installing')\n"
    "\n"
    "## Conventions\n"
    "# (company/team practices to always follow)\n"
)

_HYGIENE = (
    "Long-term memory: you have remember/recall/forget/journal tools. Save durable "
    "facts about the user and their practices with remember (update or forget stale "
    "facts instead of duplicating); use recall to fetch details behind the index; "
    "do not store what the project's own files already record."
)


def ensure_scaffold() -> None:
    """First-run setup: SOUL.md template, empty index, journal dir. Idempotent."""
    try:
        (MEMORY_DIR / "journal").mkdir(parents=True, exist_ok=True)
        if not SOUL_PATH.exists():
            SOUL_PATH.parent.mkdir(parents=True, exist_ok=True)
            SOUL_PATH.write_text(_SOUL_TEMPLATE, encoding="utf-8")
        index = MEMORY_DIR / "MEMORY.md"
        if not index.exists():
            index.write_text("# Long-term memory index\n", encoding="utf-8")
    except Exception:
        pass  # memory must never break startup


def _read_capped(path: Path, cap: int, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) > cap:
        text = text[:cap] + f"\n[{label} truncated]"
    return text


def read_soul() -> str:
    return _read_capped(SOUL_PATH, SOUL_MAX, "SOUL.md")


def read_index() -> str:
    return _read_capped(MEMORY_DIR / "MEMORY.md", INDEX_MAX, "memory index")


def read_recent_journal() -> str:
    """Today's and yesterday's journal, tail-biased truncation (newest survives)."""
    parts = []
    for d in (date.today() - timedelta(days=1), date.today()):
        path = MEMORY_DIR / "journal" / f"{d.isoformat()}.md"
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            parts.append(f"## {d.isoformat()}\n{text}")
    combined = "\n".join(parts)
    if len(combined) > JOURNAL_MAX:
        combined = "[journal truncated]\n" + combined[-JOURNAL_MAX:]
    return combined


def bootstrap_block() -> str:
    """The global-memory block injected into the system prompt each turn."""
    parts = [_HYGIENE]
    soul = read_soul()
    if soul:
        parts.append(f"Identity & standing instructions (SOUL.md):\n{soul}")
    index = read_index()
    if index:
        parts.append(f"Long-term memory index (use recall for details):\n{index}")
    journal = read_recent_journal()
    if journal:
        parts.append(f"Recent journal:\n{journal}")
    return "\n\n".join(parts)
