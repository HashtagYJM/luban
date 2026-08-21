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

# ONE budget for everything sent on every turn — SOUL + USER + fact index + journal +
# the project's memory file. There are no per-file caps.
#
# There used to be five, each head-truncating independently and silently, and every time
# one bit the answer was to bump it or make it configurable. That is not architecture:
# a knob is an admission that the right value is unknown, handed to the user. The five
# numbers also could not express the only property that actually matters — how much of
# the model's attention the always-on block consumes IN TOTAL.
#
# Derivation, not taste: hold always-on near 10% of a 150k working budget. At roughly
# 2.9 chars/token that is ~43,500 chars, less what the base prompt and tool schemas take.
ALWAYS_ON_BUDGET = 38_000

# Nothing is silently cut. A single file is only ever trimmed if it ALONE exceeds the
# entire budget — a paste accident, not a big profile — and then it is marked and the
# user is told. Below that, files are rendered WHOLE and the total is reported.
RECALL_MAX = 8000  # a TOOL RESULT bound, not always-on — different concern, stays internal

_SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")

# Maintained DOCUMENTS that live in the memory dir but are not atomic facts. They are
# large, they are edited by hand, and the tracker is self-referential — it quotes past
# search queries verbatim, so it wins any search about a problem it once recorded.
# Ranking tweaks only masked that; the real error was filing a document as a fact.
# Still fully reachable: by its own path, or by naming it exactly.
_DOCUMENTS = {"enhancements"}

_SOUL_TEMPLATE = (
    "<!-- SOUL.md — luban's character and standing behavior when working with you. -->\n"
    "<!-- Edit freely; luban reads this at the start of every session. -->\n"
    "<!-- Facts about you personally go in USER.md instead. -->\n"
    f"<!-- This file is sent on EVERY turn. It shares one {ALWAYS_ON_BUDGET:,}-char budget -->\n"
    "<!-- with USER.md, the fact index, the journal, and the project memory file. -->\n"
    "<!-- Nothing here is ever silently cut: luban tells you if the TOTAL is over, -->\n"
    "<!-- and offers to compact. Move task-specific detail into a skill instead. -->\n"
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
    f"<!-- This file is sent on EVERY turn. It shares one {ALWAYS_ON_BUDGET:,}-char budget -->\n"
    "<!-- with SOUL.md, the fact index, the journal, and the project memory file. -->\n"
    "<!-- Nothing here is ever silently cut: luban tells you if the TOTAL is over. -->\n"
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
    "-> CLOSED. After an upgrade, review Open items against the release notes and move\n"
    "closed rows to Resolved (keep the audit trail).\n"
    "\n"
    "An item can close FOUR ways — put the reason in the Resolution column:\n"
    "  <version>  fixed in a release, verified\n"
    "  wontfix    a deliberate design decision by the maintainer (record WHY)\n"
    "  mitigated  solved outside luban core; no core change is coming\n"
    "  obsolete   no longer applies, or turned out not to be a bug\n"
    "Without the last three, an item the maintainer will never fix stays Open forever,\n"
    "gets re-probed on every upgrade, and buries the issues that are still real.\n"
    "\n"
    "## Open\n"
    "\n"
    "| ID | Sev | Area | Status | Issue -> suggested fix |\n"
    "|----|-----|------|--------|------------------------|\n"
    "\n"
    "## Resolved\n"
    "\n"
    "| ID | Resolution | Notes |\n"
    "|----|------------|-------|\n"
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
    " The journal is for what happened; facts are for what stays true. Write journal "
    "entries as POINTERS — a line or two naming what moved and the file that holds the "
    "detail — because the journal is re-sent on EVERY model call for days afterwards, "
    "while a plan, spec or PROGRESS file costs nothing until someone opens it. Two "
    "things have no cheaper home and belong in the journal in full: a REVERSAL (we did "
    "X, it was wrong, we do Y now) and a SURPRISE (something behaved unlike its "
    "documentation) — plan files record decisions, rarely the reasoning that overturned "
    "one."
    " For a project whose details live in its own files, save a short POINTER fact "
    "(path + status + 'details live at …') rather than copying code that will go "
    "stale, and cross-reference related facts by name with [[slug]] — recall follows "
    "those links."
    " CONTINUITY — the index holds an [active-<project>] pointer for each project, "
    "refreshed automatically at /compact and at exit; read that first, and use the "
    "checkpoint tool to update it whenever a real milestone lands or the next step "
    "changes. Its status line carries the date it was written, so an old date means "
    "nobody has checkpointed since — go to the SESSION TRANSCRIPT it names, or list "
    "them with the sessions tool and read ~/.luban/sessions/<id>.json with read_file. "
    "The journal is a TIMELINE of what happened, not a state store — never infer "
    "'where we left off' from it. "
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
    """First-run setup: SOUL.md template, journal dir, tracker. Idempotent.

    The index is REBUILT here every launch, not merely created when absent. It is derived
    from the fact files, but it was regenerated only as a side effect of remember/forget —
    so a damaged one outlived every restart, because `if not exists()` skips a file that is
    present and truncated. That is what turned a one-second write window into permanent,
    silent memory loss. Unconditional and idempotent: cheaper in code than deciding what
    "damaged" means, and it overwrites no user work, since the index is already rewritten
    wholesale on every fact write.
    """
    try:
        (MEMORY_DIR / "journal").mkdir(parents=True, exist_ok=True)
        if not SOUL_PATH.exists():
            paths.atomic_write_text(SOUL_PATH, _SOUL_TEMPLATE)
        if not USER_PATH.exists():
            paths.atomic_write_text(USER_PATH, _USER_TEMPLATE)
        tracker = MEMORY_DIR / "enhancements.md"
        if not tracker.exists():
            paths.atomic_write_text(tracker, _ENHANCEMENTS_TEMPLATE)
        _rebuild_index()
    except Exception:
        pass  # memory must never break startup


def _read_whole(path: Path, label: str) -> str:
    """Read an always-on file in full.

    Truncation happens only when ONE file exceeds the ENTIRE always-on budget, which
    means something went wrong (a paste, a runaway write) rather than a profile that
    grew. The old per-file caps cut a real profile down to 4,000 chars and told
    nobody who could act on it.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) > ALWAYS_ON_BUDGET:
        text = text[:ALWAYS_ON_BUDGET] + (
            f"\n[{label} EXCEEDS THE ENTIRE ALWAYS-ON BUDGET ON ITS OWN and was cut "
            "here — tell the user; this is not normal]")
    return text


def read_soul() -> str:
    return _read_whole(SOUL_PATH, "SOUL.md")


def read_user() -> str:
    return _read_whole(USER_PATH, "USER.md")


def read_index() -> str:
    """The whole fact index. No per-file cap: the TOTAL always-on budget is what is
    watched, and going over is reported rather than quietly trimmed."""
    return _read_whole(MEMORY_DIR / "MEMORY.md", "memory index")


JOURNAL_DAYS = 2

# The journal is the ONE always-on component that gets a size bound, and it needs one for
# reasons that do not apply to any other (E31: in the field it had grown to the clear
# majority of an always-on block that was itself over budget):
#
#   - It grows BY DESIGN. luban appends an entry itself at every /compact and at session
#     exit, in every project, and the model adds more as work happens. Growth is the
#     feature — a timeline with gaps is not a timeline.
#   - It has NO curation lever. /reflect curates facts; remember/forget act on facts. There
#     is no operation that consolidates a timeline, and there should not be — a journal is
#     append-only by definition.
#   - Trimming it is LOSSLESS. Every day file stays on disk and every transcript is kept, so
#     showing fewer days is choosing a window, not deleting anything.
#
# That last property is what separates this from the five per-file caps deleted in v0.5.18.
# Head-truncating a user's USER.md destroyed instructions they never knew were missing;
# windowing a timeline destroys nothing. The bar for windowing ANY component is exactly
# those two properties: lossless to trim, and no curation path. Today that is the journal
# and nothing else — a second one would be the five caps returning by increments.
JOURNAL_SHARE = 0.30

# The journal is ONE global timeline, but the window is a CONTINUITY device and
# continuity is per-project. An entry is tagged with its project at journal_append —
# the single chokepoint every writer passes through — rather than by each caller
# remembering to prefix it, which is how half the timeline ended up unlabelled (E33's
# lesson: enforce at the chokepoint, never enumerate the call sites).
#
# Filtering the window matters more than tagging it. Untagged, a busy week in another
# project spends this one's whole allowance and blanks its continuity; and a gap here
# reads as "nothing happened" when the real answer is on disk two days back.
_project = ""

# An entry starts with its timestamp; the lines after it are its continuation and
# belong to the same project, so filtering is per ENTRY, not per line.
_ENTRY_START = re.compile(r"^\[\d{2}:\d{2}\] (?:\[([^\]\n]+)\] )?")


def set_project(name: str) -> None:
    """Name the project journal entries are tagged with and the window filtered to.

    Module state rather than a parameter because the readers are deep — the window is
    rendered from bootstrap_volatile on every model call, and nothing on that path
    carries a project root. Unset means no tagging and no filtering, which is what
    every test and every pre-tagging journal file already assumes.
    """
    global _project
    _project = (name or "").strip()


def _for_project(text: str) -> str:
    """One day's entries, narrowed to the current project.

    Untagged entries are kept: they were written before tagging existed, and dropping
    them would silently delete the older half of the timeline.
    """
    if not _project:
        return text
    kept: list[str] = []
    keeping = True  # anything before the first entry header is preamble, not another project's
    for line in text.splitlines():
        m = _ENTRY_START.match(line)
        if m:
            keeping = m.group(1) in (None, _project)
        if keeping:
            kept.append(line)
    return "\n".join(kept).strip()


def _journal_allowance() -> int:
    """The journal's share of the ONE budget — computed, so the total stays authoritative.

    A function rather than a module-level int because tests monkeypatch ALWAYS_ON_BUDGET;
    `int(ALWAYS_ON_BUDGET * SHARE)` at import time would bind before they could.
    """
    return int(ALWAYS_ON_BUDGET * JOURNAL_SHARE)


# A timeline needs a RUN of entries to be a timeline at all. The window keeps the newest
# whole entries, so its usefulness is entry COUNT, not characters — and one entry that
# takes a large share of the window evicts whole earlier days on its own. This is the size
# at which one entry starts doing that.
JOURNAL_ENTRIES_PER_WINDOW = 16


def journal_entry_limit() -> int:
    """The per-entry size guide, derived from the window it has to share.

    The budget cap bounds the journal's TOTAL. It cannot bound entry QUALITY: one
    multi-paragraph entry stays inside the cap and still costs every older day. A soft
    rule in a tool description and the system prompt did not bind — a blanket instruction
    with no per-entry signal gets rationalized past — so the signal is emitted at the
    write, against the entry actually written.
    """
    return max(200, _journal_allowance() // JOURNAL_ENTRIES_PER_WINDOW)


def _day_tail(text: str, budget: int) -> str:
    """The NEWEST whole entries of one day that fit in budget.

    Journal lines are "[HH:MM] ...", so an entry boundary exists and the cut never lands
    mid-line. Needed for real data: a single day file can exceed the whole allowance,
    more than the whole always-on budget.
    """
    kept: list[str] = []
    used = 0
    for line in reversed(text.splitlines()):
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(reversed(kept))


def _recent_journal_text() -> str:
    """The most recent journal days that have content, bounded to the journal's allowance.

    Was calendar-based (literally today and yesterday), so it went completely
    blank after any gap — work Friday, return Monday, and both "today" and
    "yesterday" are empty even though Friday's entries are right there on disk.
    Continuity died exactly when you'd been away and needed it most (H3).

    Returns a STRING with the omission line already in it. An earlier draft returned
    (text, omitted_days, omitted_chars); that rippled into always_on_usage(), which takes
    len() of this, and the char count was never used.
    """
    try:
        files = sorted((MEMORY_DIR / "journal").glob("*.md"))  # names sort chronologically
    except OSError:
        return ""
    budget = _journal_allowance()
    with_content = []
    for path in reversed(files):  # newest first
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        text = _for_project(text)
        if text:
            with_content.append((path.stem, text))

    picked: list[str] = []
    used = 0
    for stem, text in with_content[:JOURNAL_DAYS]:
        block = f"## {stem}\n{text}"
        if used + len(block) <= budget:
            picked.append(block)
            used += len(block) + 1
            continue
        # Doesn't fit whole. If this is the newest day, keep its newest entries rather than
        # dropping the most relevant day entirely; otherwise stop at the day boundary.
        if not picked:
            tail = _day_tail(text, budget - len(stem) - 4)
            if tail:
                picked.append(f"## {stem}\n{tail}")
        break

    if not picked:
        return ""
    shown, total = len(picked), len(with_content)
    body = "\n".join(reversed(picked))  # back to chronological order
    notes = []
    if shown < total or used > budget:
        # State the bound. A bound nobody is told about is indistinguishable from a bug —
        # that is the whole lesson of E31, where a docstring promised a truncation the code
        # had stopped doing and no one noticed for three releases.
        notes.append(f"[journal: showing the {shown} most recent day(s) of {total} within "
                     f"its context allowance — every day file is still on disk in "
                     f"~/.luban/memory/journal/]")
    if _project:
        # Same rule for the filter as for the window: what was left out is stated, and
        # where to find it. Filtering is a narrower view, not a smaller record.
        notes.append(f"[journal: entries for '{_project}' only — other projects' days are "
                     f"on disk in ~/.luban/memory/journal/, and recall searches all of them]")
    return "\n".join([*notes, body]) if notes else body


def read_recent_journal() -> str:
    """Recent journal days, newest-first within the journal's allowance.

    Bounded window, full record on disk — and the omission is stated, never silent.
    """
    return _recent_journal_text()


_SCAFFOLD_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _raw_len(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return 0


def always_on_usage() -> list[tuple[str, int]]:
    """(label, chars) per always-on component. cli adds the project memory file."""
    return [
        ("SOUL.md", _raw_len(SOUL_PATH)),
        ("USER.md", _raw_len(USER_PATH)),
        ("memory index", _raw_len(MEMORY_DIR / "MEMORY.md")),
        ("journal", len(_recent_journal_text())),
    ]


def cap_warnings(usage: list[tuple[str, int]]) -> list[str]:
    """One warning about the TOTAL — the only property that matters.

    Five per-file warnings could all stay silent while the block as a whole was far too
    big, and each per-file cut was invisible to the person who could fix it.
    """
    total = sum(n for _label, n in usage)
    if total <= ALWAYS_ON_BUDGET:
        return []
    worst = ", ".join(f"{lbl} {n:,}" for lbl, n in
                      sorted(usage, key=lambda u: -u[1])[:3] if n)
    # Names the biggest contributors and STOPS. It used to end "Run /reflect to consolidate,
    # or trim a file" — right for exactly one of five contributors and unable to shrink a
    # journal at all. Routing each contributor to its own remedy lives in cli.offer_tidy,
    # which has the config and project root needed to do it properly; this stays a plain
    # statement of fact for /config, the one caller that has no interactive offer.
    return [f"warning: always-on context is {total:,} chars against a "
            f"{ALWAYS_ON_BUDGET:,} budget — it is all still being sent, but a large "
            f"always-on block measurably weakens how well luban follows it. "
            f"Biggest: {worst}."]


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


def bootstrap_stable() -> str:
    """Global memory that rarely changes mid-session: hygiene + SOUL + USER.

    Split out from the volatile half so the prompt prefix can be laid out
    cache-friendly — stable first, volatile last (P2). Prompt caching is a PREFIX
    match, so a byte change anywhere invalidates everything after it; keeping the
    parts luban rewrites during a session (index, journal) out of this block is what
    lets the expensive identity/profile text stay cached across turns.
    """
    parts = [_HYGIENE]
    soul = read_soul()
    if soul and not _is_untouched(soul, _SOUL_TEMPLATE):
        parts.append(f"Identity & standing instructions (SOUL.md):\n{soul}")
    user = read_user()
    if user and not _is_untouched(user, _USER_TEMPLATE):
        parts.append(f"Who you are working with (USER.md):\n{user}")
    return "\n\n".join(parts)


def _over_budget_notice() -> str:
    """Tell the MODEL when the always-on block is over its single shared budget.

    Nothing is cut any more, so this is not a truncation warning — it is a prompt to
    consolidate. A budget that only trims silently teaches nobody; a budget that speaks
    is a forcing function for curation.
    """
    total = sum(n for _l, n in always_on_usage())
    if total <= ALWAYS_ON_BUDGET:
        return ""
    # Deliberately states NO figure. This function can only see memory's own four
    # components; the project memory file is resolved by cli, so any total computed here
    # under-reports what is actually sent, by the scaffold comments it strips.
    # Plumbing cli's row through bootstrap_volatile would need a closure, which reintroduces
    # the E28 stale-snapshot hazard to fix a 5% error in a message whose only job is to
    # suggest consolidation. So drop the claim instead: the model needs to know it is over
    # budget, not by how much. The human gets exact per-component numbers from cli, which
    # has all five. No model-facing text asserts a total it cannot compute correctly.
    return ("NOTE: the always-on context block is over its budget. Nothing has been cut, "
            "but a bloated always-on block degrades how well you follow any of it. Suggest "
            "/reflect: merge duplicates, delete what the transcripts and journal already "
            "hold, and tighten USER.md.")


def bootstrap_volatile() -> str:
    """Global memory luban itself rewrites during a session: the fact index and the
    journal.

    It is placed behind the CONVERSATION cache breakpoint, in the message tail — not last
    in the system prompt, which stopped being last the moment a second breakpoint existed.
    See agent.with_cache_breakpoint."""
    parts = []
    index = read_index()
    if index and any(line.lstrip().startswith("- [") for line in index.splitlines()):
        parts.append(f"Long-term memory index (use recall for details):\n{index}")
    journal = read_recent_journal()
    if journal:
        parts.append(f"Recent journal:\n{journal}")
    notice = _over_budget_notice()
    if notice:
        parts.append(notice)
    return "\n\n".join(parts)


def bootstrap_block() -> str:
    """The whole global-memory block (stable + volatile), in prompt order."""
    return "\n\n".join(p for p in (bootstrap_stable(), bootstrap_volatile()) if p)


def _overlap(a: str, b: str) -> float:
    """Jaccard overlap of content words — a cheap 'these two look like the same idea'."""
    ta, tb = set(_content_tokens(a)), set(_content_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


DUPLICATE_THRESHOLD = 0.34  # tuned to flag candidates for a human/model, not to auto-merge


def duplicate_candidates() -> list[tuple[str, str, float]]:
    """Pairs of facts that look like the same idea, most similar first.

    Purely lexical and deliberately loose: this only ever SUGGESTS a merge to the
    curator, it never merges anything. False positives cost a glance; misses cost a
    duplicate that lives forever.
    """
    facts = []
    if MEMORY_DIR.is_dir():
        for p in sorted(MEMORY_DIR.glob("*.md")):
            if p.name == "MEMORY.md" or is_checkpoint(p.stem):
                continue  # one pointer per project; they are meant to look alike
            try:
                facts.append((p.stem, p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    pairs = []
    for i, (sa, ta) in enumerate(facts):
        for sb, tb in facts[i + 1:]:
            score = _overlap(f"{sa} {ta}", f"{sb} {tb}")
            if score >= DUPLICATE_THRESHOLD:
                pairs.append((sa, sb, round(score, 2)))
    return sorted(pairs, key=lambda p: -p[2])


def always_on_budget(extra: list[tuple[str, int]] | None = None) -> str:
    """The always-on ledger, for the curator — one shared budget, not five caps.

    `extra` carries contributors this module cannot see on its own: today the project's
    memory file, which cli resolves per project. A ledger that silently omits a
    contributor is not a ledger — it under-reports the very total it is asked to police.
    """
    usage = always_on_usage() + list(extra or [])
    rows = [f"  {lbl:<16}{n:>8,} chars" for lbl, n in usage]
    total = sum(n for _l, n in usage)
    state = ("OVER — consolidate" if total > ALWAYS_ON_BUDGET
             else f"{ALWAYS_ON_BUDGET - total:,} chars free")
    return ("ALWAYS-ON LEDGER — one shared budget for everything sent every turn:\n"
            + "\n".join(rows)
            + f"\n  {'TOTAL':<16}{total:>8,} / {ALWAYS_ON_BUDGET:,}  ({state})\n"
              "Promoting into USER.md spends this shared budget. Nothing is silently "
              "cut, but the bigger it gets the less reliably luban follows any of it.")


def audit(extra: list[tuple[str, int]] | None = None) -> str:
    """The COMPLETE fact store plus duplicate candidates — the curator's raw material.

    recall() is capped at RECALL_MAX (8,000 chars), which on a real store lets /reflect
    see roughly a tenth of what it is being asked to curate; rationing the curator is why
    consolidation never happened. This is injected into the isolated /reflect turn only,
    so an ordinary turn never carries it.
    """
    facts, maintained = [], []
    if MEMORY_DIR.is_dir():
        for p in sorted(MEMORY_DIR.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            try:
                entry = f"[{p.stem}]\n{p.read_text(encoding='utf-8', errors='replace').strip()}"
            except OSError:
                continue
            (maintained if is_checkpoint(p.stem) else facts).append(entry)
    if not facts and not maintained:
        return always_on_budget(extra) + "\n\n(the fact store is empty)"
    body = "\n\n".join(facts)
    parts = [always_on_budget(extra),
             f"THE COMPLETE FACT STORE ({len(facts)} facts, {len(body):,} chars):\n\n{body}"]
    if maintained:
        # Shown, because they spend the same budget and the curator is being asked to
        # account for it — but ring-fenced, because every rule in REFLECT_PROMPT would
        # delete them: they are task-scoped and they duplicate the project's own files
        # BY DESIGN. That is the job, not a defect.
        parts.append("MAINTAINED BY LUBAN — continuity pointers, rewritten automatically "
                     "at /compact and at exit. Never merge, graduate, rewrite or "
                     "shorten one, and never treat two of them as duplicates: there is "
                     "one per project and they are meant to look alike. The ONE edit "
                     "you may make is to forget a pointer whose project has not been "
                     "touched in months — the 'last session' date is in the fact, and "
                     "if you are wrong the next /compact there writes it back.\n\n"
                     + "\n\n".join(maintained))
    dupes = duplicate_candidates()
    if dupes:
        listing = "\n".join(f"  - [{a}] vs [{b}]  (overlap {s})" for a, b, s in dupes[:20])
        parts.append("POSSIBLE DUPLICATES (lexical overlap — judge for yourself, these "
                     f"are only candidates):\n{listing}")
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
        paths.atomic_write_text(MEMORY_DIR / "MEMORY.md", "\n".join(lines) + "\n")
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
        paths.atomic_write_text(
            _fact_path(name), f"description: {description.strip()}\n\n{body.strip()}\n"
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


# --- continuity pointers ---------------------------------------------------------
# The one question a session has to answer before it can read anything: where am I on
# this project, and what is next. Everything that could answer it is either wrong for
# the job or unmaintained — the journal is a global timeline whose newest entry may
# belong to another project, the transcript has to be found before it can be read, and
# a hand-written fact goes stale the first time nobody updates it.
#
# So it is machine-maintained, and it is a POINTER: it lives in the always-on index as
# one line, and the detail it names costs nothing until someone opens it. One per
# project, because a single shared fact is overwritten by whichever project compacted
# last — which is the stale-pointer failure again, wearing the fix's clothes.
#
# Code owns the ADDRESS and the model owns the STATUS, and they carry SEPARATE dates.
# That split is the whole design: the address lands even when the model call that would
# supply a status never happens, and refreshing the address can never re-date a status
# the model did not actually write.
CHECKPOINT_PREFIX = "active-"

_STATUS_RX = re.compile(r"^status \((\d{4}-\d\d-\d\d)\): (.+)$", re.M)
_TRANSCRIPT_RX = re.compile(r"^transcript: ~/\.luban/sessions/(\S+)\.json$", re.M)


def checkpoint_slug(project: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (project or "").lower()).strip("-")[:56]
    return f"{CHECKPOINT_PREFIX}{slug or 'work'}"


def is_checkpoint(name: str) -> bool:
    return name.startswith(CHECKPOINT_PREFIX)


def checkpoint(project: str, status: str = "", session_id: str = "") -> str:
    """Write or refresh this project's continuity pointer.

    Called with a status by the model, and with none by luban at /compact and at exit —
    the second form refreshes the address and leaves the last real status standing under
    its own, older date, so a pointer that nobody has updated reads as exactly that.
    """
    name = checkpoint_slug(project)
    old = read_fact(name) or ""
    today = date.today().isoformat()
    status = " ".join(status.split())
    if status:
        recorded = today
    else:
        m = _STATUS_RX.search(old)
        status, recorded = (m.group(2), m.group(1)) if m else ("", "")
    if not session_id:
        m = _TRANSCRIPT_RX.search(old)
        session_id = m.group(1) if m else ""
    lines = [f"project: {project}", f"last session: {today}"]
    if session_id:
        lines.append(f"transcript: ~/.luban/sessions/{session_id}.json")
    lines.append(f"status ({recorded}): {status}" if status
                 else "status: not recorded — read the transcript.")
    lines.append("Maintained by luban at /compact and at exit. Never merge, graduate, "
                 "rewrite or forget it; it is refreshed automatically.")
    description = status[:160] if status else f"where {project} stands — status not recorded"
    return remember(name, description, "\n".join(lines))


RECALL_TOP_FACTS = 8      # best-scoring facts returned (wikilinks followed on top of this)
RECALL_TOP_JOURNAL = 12   # matching journal lines returned — NEWEST kept, not oldest
_EXACT_BONUS = 1000       # whole-query substring dominates any token-overlap score

# Deliberately small and closed. These carry no retrieval signal, and letting them
# match is what made "how does the user like their code written" behave like a
# wildcard. Pure stdlib — no stemmer, no embeddings (E26).
_STOPWORDS = frozenset("""
a an the this that these those and or not no but if then than so as of in on at to for
with about against from by into over under is are was were be been being am do does did
done have has had can could should would will shall may might must i me my mine we us
our you your he him his she her it its they them their what which who whom whose when
where why how all any both each few more most other some such only own same too very
just like get got make made use used there here
""".split())


def _normalize(token: str) -> str:
    """Strip punctuation and a trailing possessive/plural.

    Comparison is by substring, so normalising ONE side is enough to match both
    directions: "results" -> "result" hits a body containing "results", and a query
    "result" already hits it as-is.
    """
    t = token.strip(".,;:!?()[]{}<>\"'`")
    if t.endswith("'s") or t.endswith("’s"):
        t = t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t


def _content_tokens(query: str) -> list[str]:
    """Normalised, de-duplicated, stopword-free tokens — the ones that carry meaning."""
    seen: list[str] = []
    for raw in query.lower().split():
        # Check the RAW word against the stopword list first: normalising "does" to
        # "doe" would smuggle it past the filter, and "doe" is a substring of
        # "doesn't" — a false-positive source.
        bare = raw.strip(".,;:!?()[]{}<>\"'`")
        if not bare or bare in _STOPWORDS:
            continue
        t = _normalize(raw)
        if t and t not in _STOPWORDS and t not in seen:
            seen.append(t)
    return seen


def _recall_score(query: str, *fields: str) -> int:
    """How well this text answers the query. 0 = no match.

    Was an all-tokens-AND boolean, so a single ordinary absent word ("how", "their")
    zeroed an otherwise perfect hit and recall reported "(no matches)" for a fact
    sitting on disk — which then led the model to save a duplicate (E26). Now a
    RANKED OR: any content token counts, and more distinct tokens ranks higher.
    """
    hay = " ".join(fields).lower()
    q = query.lower().strip()
    if not q:
        return 1  # empty query = dump everything, unchanged
    if q in hay:
        return _EXACT_BONUS
    tokens = _content_tokens(q)
    if not tokens:
        return 0  # a query of pure stopwords must match NOTHING, not everything
    found = sum(1 for t in tokens if t in hay)
    if not found:
        return 0
    # Normalise by length. An unnormalised count rises monotonically with document
    # size, so the biggest generic files (the tracker, an active-work note) outranked
    # the fact actually asked for — the intended fact came first in only 2 of 8 probed
    # queries (E27). Score = coverage, with a mild length penalty as the tiebreak.
    coverage = found / len(tokens)
    length_penalty = 1.0 + (len(hay) / 20_000)
    return int(round(1000 * coverage / length_penalty))


def _recall_match(query: str, *fields: str) -> bool:
    """Boolean view of the score, kept for callers/tests that only ask yes-or-no."""
    return _recall_score(query, *fields) > 0


_WIKILINK = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)\]\]")


def _fact_text(slug: str) -> str | None:
    p = _fact_path(slug)
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    except OSError:
        return None


NO_MATCH = (
    "Nothing in memory matched those words. NOTE: this does NOT mean the fact does not "
    "exist — the memory index in your system prompt lists every fact there is. Look "
    "there and pass an exact slug to read one. Do not save a new fact just because a "
    "search missed; check the index first, and update the existing fact if there is one."
)


def recall(query: str) -> str:
    """Read a fact by slug (the normal path), or explore by keyword (the fallback).

    The index of every fact is already in the model's context, so this is fundamentally
    a FETCH, not a search — matching exists only for exploration. An empty result must
    never read as 'that fact does not exist', because that is what makes the model save
    a duplicate; see NO_MATCH.
    """
    # Exact slug: read it directly, no scoring, no competition from other facts.
    direct = _fact_text(query.strip())
    if direct is not None:
        out = f"[{query.strip()}]\n{direct.strip()}"
        for slug in _WIKILINK.findall(direct):
            linked = _fact_text(slug)
            if linked is not None:
                out += f"\n\n[{slug}] (linked from [{query.strip()}])\n{linked.strip()}"
        return out if len(out) <= RECALL_MAX else out[:RECALL_MAX] + "\n[recall truncated]"

    hits: list[str] = []
    matched: set[str] = set()

    # --- facts lane: score, rank, take the best few -------------------------------
    if MEMORY_DIR.is_dir():
        scored: list[tuple[int, str, str]] = []
        for p in sorted(MEMORY_DIR.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if p.stem in _DOCUMENTS and query.strip() != p.stem:
                continue  # a document only surfaces when asked for BY NAME
            score = _recall_score(query, p.stem, text)
            if score > 0:
                scored.append((score, p.stem, text))
        # Rank by score, then slug so equal scores are stable/deterministic.
        scored.sort(key=lambda s: (-s[0], s[1]))
        # Cap each fact's share of the budget. One oversized fact used to consume the
        # whole RECALL_MAX, truncating INSIDE hit #1 so the correct lower-ranked fact
        # was silently dropped (E27). A fetch by slug is never capped this way.
        share = max(1200, RECALL_MAX // max(1, min(len(scored), RECALL_TOP_FACTS)))
        for _score, stem, text in scored[:RECALL_TOP_FACTS]:
            body = text.strip()
            if len(body) > share:
                body = body[:share] + f"\n[trimmed — read it whole with recall('{stem}')]"
            hits.append(f"[{stem}]\n{body}")
            matched.add(stem)
        # E9: follow [[wikilinks]] one level so a "pointer" fact that references
        # another (e.g. active-work → [[project-x]]) pulls the linked fact in too.
        for stem in list(matched):
            body = _fact_text(stem) or ""
            for slug in _WIKILINK.findall(body):
                if slug in matched:
                    continue
                linked = _fact_text(slug)
                if linked is not None:
                    hits.append(f"[{slug}] (linked from [{stem}])\n{linked.strip()}")
                    matched.add(slug)

    # --- journal lane: its own cap, and keep the NEWEST ---------------------------
    journal_dir = MEMORY_DIR / "journal"
    if journal_dir.is_dir():
        lines: list[str] = []
        for p in sorted(journal_dir.glob("*.md")):  # chronological by filename
            try:
                content = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            lines.extend(
                f"{p.stem}: {ln.strip()}" for ln in content if _recall_match(query, ln)
            )
        if lines:
            # Tail slice = newest. The old code appended every match and then head-
            # truncated the whole output, so the NEWEST journal was what got cut.
            kept = lines[-RECALL_TOP_JOURNAL:]
            dropped = len(lines) - len(kept)
            label = "--- journal ---" + (
                f"  ({dropped} older match(es) not shown)" if dropped else ""
            )
            hits.append(label + "\n" + "\n".join(kept))

    out = "\n\n".join(hits) or NO_MATCH
    if len(out) > RECALL_MAX:
        out = out[:RECALL_MAX] + "\n[recall truncated]"
    return out


def journal_append(text: str, project: str | None = None) -> None:
    """Append one entry to today's journal, tagged with its project.

    The tag is applied HERE and nowhere else. It used to be part of the text at one
    caller and absent at the other, so half the timeline was labelled and half was not.
    """
    global _journal_writes
    tag = (_project if project is None else project).strip()
    try:
        journal_dir = MEMORY_DIR / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M")
        path = journal_dir / f"{date.today().isoformat()}.md"
        head = f"[{stamp}] " + (f"[{tag}] " if tag else "")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{head}{text.strip()}\n")
        _journal_writes += 1
    except Exception:
        pass  # journaling must never break the loop
