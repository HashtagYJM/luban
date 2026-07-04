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

_SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")

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


def valid_slug(name: str) -> bool:
    return bool(_SLUG_RX.match(name))


def _fact_path(name: str) -> Path:
    return MEMORY_DIR / f"{name}.md"


def _fact_description(text: str) -> str:
    first = text.splitlines()[0] if text.splitlines() else ""
    if first.lower().startswith("description:"):
        return first[len("description:"):].strip()
    return first.strip()[:80]


def _rebuild_index() -> None:
    lines = ["# Long-term memory index"]
    try:
        facts = sorted(p for p in MEMORY_DIR.glob("*.md") if p.name != "MEMORY.md")
        for p in facts:
            text = p.read_text(encoding="utf-8", errors="replace")
            lines.append(f"- [{p.stem}] {_fact_description(text)}")
        (MEMORY_DIR / "MEMORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # index rebuild is best-effort; facts on disk stay authoritative


def read_fact(name: str) -> str | None:
    if not valid_slug(name):
        return None
    try:
        return _fact_path(name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def remember(name: str, description: str, body: str) -> str:
    if not valid_slug(name):
        return f"Invalid memory name: {name!r} (kebab-case: a-z, 0-9, dashes, max 64)."
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        _fact_path(name).write_text(
            f"description: {description.strip()}\n\n{body.strip()}\n", encoding="utf-8"
        )
        _rebuild_index()
    except OSError as exc:
        return f"Could not save memory: {exc}"
    return f"Remembered '{name}'."


def forget(name: str) -> str:
    if not valid_slug(name):
        return f"Invalid memory name: {name!r}."
    path = _fact_path(name)
    if not path.exists():
        return f"No memory named '{name}'."
    try:
        path.unlink()
        _rebuild_index()
    except OSError as exc:
        return f"Could not delete memory: {exc}"
    return f"Forgot '{name}'."


def recall(query: str) -> str:
    q = query.lower().strip()
    hits: list[str] = []
    if MEMORY_DIR.is_dir():
        for p in sorted(MEMORY_DIR.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if q in p.stem.lower() or q in text.lower():
                hits.append(f"[{p.stem}]\n{text.strip()}")
    journal_dir = MEMORY_DIR / "journal"
    if journal_dir.is_dir():
        for p in sorted(journal_dir.glob("*.md")):
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            hits.extend(f"{p.stem}: {ln.strip()}" for ln in lines if q in ln.lower())
    out = "\n\n".join(hits) or "(no matches)"
    if len(out) > RECALL_MAX:
        out = out[:RECALL_MAX] + "\n[recall truncated]"
    return out


def journal_append(text: str) -> None:
    try:
        journal_dir = MEMORY_DIR / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M")
        path = journal_dir / f"{date.today().isoformat()}.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {text.strip()}\n")
    except Exception:
        pass  # journaling must never break the loop
