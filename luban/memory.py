"""Long-term memory — SOUL.md + USER.md identity, fact store, and daily journal.

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

from luban import paths

SOUL_PATH = paths.luban_home() / "SOUL.md"
USER_PATH = paths.luban_home() / "USER.md"
MEMORY_DIR = paths.luban_home() / "memory"

SOUL_MAX = 4000
# A user profile is at least as load-bearing as the agent's character — a real
# professional profile does not fit in 2,000 chars (a 3,158-char USER.md was being
# silently truncated in the field, dropping the user's hard coding rules and whole
# Environment section). Caps stay: an uncapped always-on file bloats EVERY turn
# with no signal. A cap you can see (cap_warnings) beats no cap.
USER_MAX = 4000
INDEX_MAX = 4000
JOURNAL_MAX = 3000
RECALL_MAX = 8000

_SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")

_SOUL_TEMPLATE = (
    "<!-- SOUL.md — luban's character and standing behavior when working with you. -->\n"
    "<!-- Edit freely; luban reads this at the start of every session. -->\n"
    "<!-- Facts about you personally go in USER.md instead. -->\n"
    f"<!-- Keep it under {SOUL_MAX:,} characters: anything past that is NOT sent to -->\n"
    "<!-- the model. Move task-specific detail into a skill instead. -->\n"
    "\n"
    "## How I should work\n"
    "<!-- standing behavior, e.g. 'add type hints', 'ask before installing', 'keep changes minimal' -->\n"
    "\n"
    "## Conventions\n"
    "<!-- company/team practices to always follow -->\n"
    "\n"
    "## Boundaries\n"
    "<!-- things to never do -->\n"
)

_USER_TEMPLATE = (
    "<!-- USER.md — who luban is working with. luban reads this every session and -->\n"
    "<!-- may update it (with your confirmation) as it learns about you. -->\n"
    f"<!-- Keep it under {USER_MAX:,} characters: anything past that is NOT sent to -->\n"
    "<!-- the model. luban warns you at startup if you go over. -->\n"
    "\n"
    "## About me\n"
    "<!-- your name, role, team -->\n"
    "\n"
    "## Expertise & preferences\n"
    "<!-- languages and tools you use; how you like work presented -->\n"
    "\n"
    "## Environment\n"
    "<!-- OS, key tools, anything luban should assume about your setup -->\n"
)

_ENHANCEMENTS_TEMPLATE = (
    "description: Self-improvement tracker — luban issues seen in the field, to ship to the maintainer\n"
    "\n"
    "# Luban — Self-Improvement Tracker\n"
    "\n"
    "Runtime/tooling issues to flag but NOT fix locally. Share Open items with the\n"
    "maintainer (screenshot or text). Lifecycle: OPEN -> SHARED (sent to maintainer)\n"
    "-> FIXED (confirmed working in a release). After an upgrade, review Open items\n"
    "against the release notes and MOVE fixed rows to Resolved (keep the audit trail).\n"
    "\n"
    "## Open\n"
    "\n"
    "| ID | Sev | Area | Status | Issue -> suggested fix |\n"
    "|----|-----|------|--------|------------------------|\n"
    "\n"
    "## Resolved\n"
    "\n"
    "| ID | Fixed in | Notes |\n"
    "|----|----------|-------|\n"
)

_journal_writes = 0

_HYGIENE = (
    "Long-term memory: you have remember/recall/forget/journal tools. Save durable "
    "facts about the user and their practices with remember (update or forget stale "
    "facts instead of duplicating); use recall to fetch details behind the index; "
    "do not store what the project's own files already record. You may also read and "
    "edit your own files under ~/.luban directly with the file tools (memory component "
    "files like the enhancements tracker, skills, config.toml) — every write shows a "
    "diff and asks. Never edit ~/.luban/memory/MEMORY.md itself: it is a machine-"
    "rebuilt index; edit the component files instead."
    " The journal is for what happened; facts are for what stays true."
    " For a project whose details live in its own files, save a short POINTER fact "
    "(path + status + 'details live at …') rather than copying code that will go "
    "stale, and cross-reference related facts by name with [[slug]] — recall follows "
    "those links."
    " CONTINUITY — to recover what you were doing, read the SESSION TRANSCRIPT: list "
    "them with the sessions tool and read ~/.luban/sessions/<id>.json with read_file. "
    "The journal is a TIMELINE of what happened, not a state store — its newest entry "
    "may belong to a different project, so never infer 'where we left off' from it. "
    "Always use the ~/.luban path alias with the file tools; a shell '~' resolves to "
    "the OS home, which on a relocated LUBAN_HOME silently finds nothing."
    " WHERE TO WRITE — route by how the knowledge will be USED, not by whichever "
    "tool is handiest: a standing preference about the user or how they want work "
    "done -> EDIT USER.md (it is always in your context); your own character or "
    "behavior -> SOUL.md; a detail only needed once it becomes relevant -> remember "
    "(a fact); a repeatable procedure for a class of task -> a skill; something true "
    "only inside one codebase -> that project's memory file. NEVER store always-on "
    "behavior as a recallable fact: you cannot know to recall it before you act, so "
    "by the time you would look it up you have already done the thing the wrong way."
)


def ensure_scaffold() -> None:
    """First-run setup: SOUL.md template, empty index, journal dir. Idempotent."""
    try:
        (MEMORY_DIR / "journal").mkdir(parents=True, exist_ok=True)
        if not SOUL_PATH.exists():
            SOUL_PATH.parent.mkdir(parents=True, exist_ok=True)
            SOUL_PATH.write_text(_SOUL_TEMPLATE, encoding="utf-8")
        if not USER_PATH.exists():
            USER_PATH.parent.mkdir(parents=True, exist_ok=True)
            USER_PATH.write_text(_USER_TEMPLATE, encoding="utf-8")
        index = MEMORY_DIR / "MEMORY.md"
        if not index.exists():
            index.write_text("# Long-term memory index\n", encoding="utf-8")
        tracker = MEMORY_DIR / "enhancements.md"
        if not tracker.exists():
            tracker.write_text(_ENHANCEMENTS_TEMPLATE, encoding="utf-8")
            _rebuild_index()  # index the new component immediately
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


def read_user() -> str:
    return _read_capped(USER_PATH, USER_MAX, "USER.md")


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


_SCAFFOLD_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _raw_len(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return 0


def _raw_journal_len() -> int:
    parts = []
    for d in (date.today() - timedelta(days=1), date.today()):
        path = MEMORY_DIR / "journal" / f"{d.isoformat()}.md"
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            parts.append(f"## {d.isoformat()}\n{text}")
    return len("\n".join(parts))


def always_on_usage() -> list[tuple[str, int, int]]:
    """(label, actual_chars, cap) for each memory file injected into EVERY turn."""
    return [
        ("SOUL.md", _raw_len(SOUL_PATH), SOUL_MAX),
        ("USER.md", _raw_len(USER_PATH), USER_MAX),
        ("memory index", _raw_len(MEMORY_DIR / "MEMORY.md"), INDEX_MAX),
        ("journal", _raw_journal_len(), JOURNAL_MAX),
    ]


def cap_warnings(usage: list[tuple[str, int, int]]) -> list[str]:
    """Human-facing warnings for any always-on file whose tail is being DROPPED.

    The `[label truncated]` marker _read_capped appends only ever reached the
    MODEL — the human was never told, so an over-cap USER.md looked like luban
    ignoring their instructions when it had simply never seen them. Say it out loud.
    """
    return [
        f"warning: {label} is {size:,} chars but the cap is {cap:,} — the last "
        f"{size - cap:,} chars are NOT being sent to the model. Trim it, or move "
        "task-specific detail into a skill."
        for label, size, cap in usage
        if size > cap
    ]


def _is_untouched(text: str, template: str = "") -> bool:
    """True when the file still holds no user-authored content — only scaffold
    (HTML comments and empty section headings).

    Checked STRUCTURALLY, not by exact-matching one template's text: the old
    equality check meant that editing a template (e.g. to add the char budget)
    silently un-suppressed every existing user's untouched scaffold, spraying it
    into the prompt as noise. `template` is accepted and ignored for call-site
    compatibility.
    """
    body = _SCAFFOLD_COMMENT.sub("", text)
    authored = [
        ln.strip() for ln in body.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return not authored


def bootstrap_block() -> str:
    """The global-memory block injected into the system prompt each turn."""
    parts = [_HYGIENE]
    soul = read_soul()
    if soul and not _is_untouched(soul, _SOUL_TEMPLATE):
        parts.append(f"Identity & standing instructions (SOUL.md):\n{soul}")
    user = read_user()
    if user and not _is_untouched(user, _USER_TEMPLATE):
        parts.append(f"Who you are working with (USER.md):\n{user}")
    index = read_index()
    if index and any(line.lstrip().startswith("- [") for line in index.splitlines()):
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


def _recall_match(query: str, *fields: str) -> bool:
    """Match if the whole query is a substring, OR every whitespace token of the
    query appears somewhere in the fields. Token matching lets multi-word queries
    like "coding style" find a fact slugged "yjm-coding-style" whose body mentions
    both words, without needing the exact contiguous phrase or slug."""
    hay = " ".join(fields).lower()
    q = query.lower().strip()
    if not q:
        return True
    if q in hay:
        return True
    tokens = q.split()
    return bool(tokens) and all(t in hay for t in tokens)


_WIKILINK = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)\]\]")


def _fact_text(slug: str) -> str | None:
    p = _fact_path(slug)
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    except OSError:
        return None


def recall(query: str) -> str:
    hits: list[str] = []
    matched: set[str] = set()
    if MEMORY_DIR.is_dir():
        for p in sorted(MEMORY_DIR.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _recall_match(query, p.stem, text):
                hits.append(f"[{p.stem}]\n{text.strip()}")
                matched.add(p.stem)
        # E9: follow [[wikilinks]] one level so a "pointer" fact that references
        # another (e.g. active-work → [[project-x]]) pulls the linked fact in too.
        for p in sorted(MEMORY_DIR.glob("*.md")):
            if p.stem not in matched:
                continue
            body = _fact_text(p.stem) or ""
            for slug in _WIKILINK.findall(body):
                if slug in matched:
                    continue
                linked = _fact_text(slug)
                if linked is not None:
                    hits.append(f"[{slug}] (linked from [{p.stem}])\n{linked.strip()}")
                    matched.add(slug)
    journal_dir = MEMORY_DIR / "journal"
    if journal_dir.is_dir():
        for p in sorted(journal_dir.glob("*.md")):
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            hits.extend(
                f"{p.stem}: {ln.strip()}" for ln in lines if _recall_match(query, ln)
            )
    out = "\n\n".join(hits) or "(no matches)"
    if len(out) > RECALL_MAX:
        out = out[:RECALL_MAX] + "\n[recall truncated]"
    return out


def journal_append(text: str) -> None:
    global _journal_writes
    try:
        journal_dir = MEMORY_DIR / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M")
        path = journal_dir / f"{date.today().isoformat()}.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {text.strip()}\n")
        _journal_writes += 1
    except Exception:
        pass  # journaling must never break the loop
