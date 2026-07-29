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


_INDEX_LINE = re.compile(r"^- \[([a-z0-9][a-z0-9-]*)\]")
_INDEX_TRIM_NOTE = "<!-- descriptions trimmed to fit; use recall for details -->"


def _slug_only_index(lines: list[str]) -> str:
    slugs = [f"- [{m.group(1)}]" for ln in lines if (m := _INDEX_LINE.match(ln))]
    header = lines[0] if lines else "# Long-term memory index"
    return "\n".join([header, _INDEX_TRIM_NOTE, *slugs])


def read_index() -> str:
    """The always-on catalog of facts. Degrades by dropping DESCRIPTIONS, never
    SLUGS.

    _rebuild_index sorts alphabetically and this used to head-truncate, so once the
    index passed its cap the late-alphabet facts silently fell off the list. The
    index is the only thing telling the model a fact EXISTS — a fact missing from it
    is one the model will never think to recall. A slug-only line is ~20 chars, so
    dropping descriptions keeps ~200 facts discoverable instead of ~50 (H2).
    """
    try:
        text = (MEMORY_DIR / "MEMORY.md").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) <= INDEX_MAX:
        return text
    compact = _slug_only_index(text.splitlines())
    if len(compact) <= INDEX_MAX:
        return compact
    # Extreme: even slug-only overflows. Now a fact really is falling off the
    # catalog — cap_warnings says so out loud.
    return compact[:INDEX_MAX] + "\n[memory index truncated]"


def _read_raw_index() -> str:
    try:
        return (MEMORY_DIR / "MEMORY.md").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def index_slugs_dropped() -> int:
    """How many fact slugs don't fit even in a slug-only index — i.e. facts the
    model will no longer know exist. 0 in every normal case."""
    try:
        text = (MEMORY_DIR / "MEMORY.md").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return 0
    lines = text.splitlines()
    total = sum(1 for ln in lines if _INDEX_LINE.match(ln))
    compact = _slug_only_index(lines)
    if len(compact) <= INDEX_MAX:
        return 0
    kept = sum(1 for ln in compact[:INDEX_MAX].splitlines() if _INDEX_LINE.match(ln))
    return max(0, total - kept)


JOURNAL_DAYS = 2


def _recent_journal_text() -> str:
    """The most recent JOURNAL_DAYS journal days that actually HAVE content.

    Was calendar-based (literally today and yesterday), so it went completely
    blank after any gap — work Friday, return Monday, and both "today" and
    "yesterday" are empty even though Friday's entries are right there on disk.
    Continuity died exactly when you'd been away and needed it most (H3).
    """
    try:
        files = sorted((MEMORY_DIR / "journal").glob("*.md"))  # names sort chronologically
    except OSError:
        return ""
    picked: list[str] = []
    for path in reversed(files):  # newest first
        if len(picked) >= JOURNAL_DAYS:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            picked.append(f"## {path.stem}\n{text}")
    return "\n".join(reversed(picked))  # back to chronological order


def read_recent_journal() -> str:
    """Recent journal days. Tail-biased truncation: when the slice is over budget
    the NEWEST entries survive and the OLDEST roll off (the opposite of
    _read_capped) — and losslessly, since the full day files stay on disk."""
    combined = _recent_journal_text()
    if len(combined) > JOURNAL_MAX:
        combined = "[journal truncated]\n" + combined[-JOURNAL_MAX:]
    return combined


_SCAFFOLD_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _raw_len(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return 0


def always_on_usage() -> list[tuple[str, int, int, bool]]:
    """(label, actual_chars, cap, warnable) for each memory file injected every turn.

    `warnable` marks the HEAD-biased, genuinely-lossy files. The journal is
    tail-biased and rolls off losslessly (full day files stay on disk), and the
    index now sheds descriptions rather than facts — warning about either would be
    noise, and the head-biased wording would be flat wrong for them (H1).
    """
    return [
        ("SOUL.md", _raw_len(SOUL_PATH), SOUL_MAX, True),
        ("USER.md", _raw_len(USER_PATH), USER_MAX, True),
        ("memory index", _raw_len(MEMORY_DIR / "MEMORY.md"), INDEX_MAX, False),
        ("journal", len(_recent_journal_text()), JOURNAL_MAX, False),
    ]


def cap_warnings(usage: list[tuple[str, int, int, bool]]) -> list[str]:
    """Human-facing warnings for always-on content that is genuinely being LOST.

    The `[label truncated]` marker only ever reached the MODEL — the human was never
    told, so an over-cap USER.md looked like luban ignoring their instructions when
    it had simply never seen them. Say it out loud — but only where it's true: this
    wording ("the last N chars are dropped") describes head-biased truncation, and
    must never be applied to the tail-biased journal (H1).
    """
    out = [
        f"warning: {label} is {size:,} chars but the cap is {cap:,} — the last "
        f"{size - cap:,} chars are NOT being sent to the model. Trim it, or move "
        "task-specific detail into a skill."
        for label, size, cap, warnable in usage
        if warnable and size > cap
    ]
    dropped = index_slugs_dropped()
    if dropped:
        out.append(
            f"warning: {dropped:,} fact(s) no longer fit in the memory index — luban "
            "won't know they exist (they're still on disk). Use forget to prune."
        )
    return out


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
    """Tell the MODEL when the store has outgrown its always-on budget.

    Shedding descriptions to fit (H2) keeps every fact discoverable, but doing only that
    hides the problem forever — rationing used as a substitute for curation. Anthropic's
    own memory design errors and tells the model to rewrite the index; this is the same
    idea: the cap is a forcing function for consolidation, not a silent quota.
    """
    dropped = index_slugs_dropped()
    trimmed = len(_read_raw_index()) > INDEX_MAX
    if not (dropped or trimmed):
        return ""
    detail = (f"{dropped} fact(s) no longer fit at all" if dropped
              else "descriptions are being trimmed to fit")
    return (f"NOTE: the long-term memory index is over its always-on budget — {detail}. "
            "A bloated store degrades how well you follow it. Suggest the user run "
            "/reflect to consolidate: merge duplicates, delete what the transcripts and "
            "journal already hold, and graduate standing preferences into USER.md.")


def bootstrap_volatile() -> str:
    """Global memory luban itself rewrites during a session: the fact index and the
    journal. Kept LAST in the prompt so a `remember`/`journal` write can't invalidate
    the cached prefix above it."""
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
            if p.name == "MEMORY.md":
                continue
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


def always_on_budget() -> str:
    """USER.md / SOUL.md usage against their caps — the curator must see this before it
    promotes anything INTO them.

    Graduation moves knowledge from the fact store (which degrades gracefully: over
    budget it sheds descriptions but keeps every fact) into an always-on file that does
    NOT degrade gracefully — over budget, the tail is simply cut. Promoting without
    showing the budget is how a fix for one accumulation problem creates another.
    """
    rows = []
    for label, path, cap in (("USER.md", USER_PATH, USER_MAX),
                             ("SOUL.md", SOUL_PATH, SOUL_MAX)):
        try:
            n = len(path.read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            n = 0
        state = "OVER BUDGET — the tail is being cut" if n > cap else f"{cap - n:,} free"
        rows.append(f"  {label}  {n:,} / {cap:,} chars  ({state})")
    return (
        "ALWAYS-ON FILES — sent on EVERY turn, so every line costs forever:\n"
        + "\n".join(rows)
        + "\nThese do NOT degrade gracefully: past the cap the end of the file is simply "
          "dropped. Anything you graduate here spends this budget permanently."
    )


def audit() -> str:
    """The COMPLETE fact store plus duplicate candidates — the curator's raw material.

    recall() is capped at RECALL_MAX (8,000 chars), which on a real store lets /reflect
    see roughly a tenth of what it is being asked to curate; rationing the curator is why
    consolidation never happened. This is injected into the isolated /reflect turn only,
    so an ordinary turn never carries it.
    """
    facts = []
    if MEMORY_DIR.is_dir():
        for p in sorted(MEMORY_DIR.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            try:
                facts.append(f"[{p.stem}]\n{p.read_text(encoding='utf-8', errors='replace').strip()}")
            except OSError:
                continue
    if not facts:
        return always_on_budget() + "\n\n(the fact store is empty)"
    body = "\n\n".join(facts)
    parts = [always_on_budget(),
             f"THE COMPLETE FACT STORE ({len(facts)} facts, {len(body):,} chars):\n\n{body}"]
    dupes = duplicate_candidates()
    if dupes:
        listing = "\n".join(f"  - [{a}] vs [{b}]  (overlap {s})" for a, b, s in dupes[:20])
        parts.append("POSSIBLE DUPLICATES (lexical overlap — judge for yourself, these "
                     f"are only candidates):\n{listing}")
    over = index_slugs_dropped()
    if over:
        parts.append(f"WARNING: the always-on index is over budget — {over} fact(s) no "
                     "longer fit. Consolidation is overdue.")
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
    return sum(1 for t in tokens if t in hay)


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
            score = _recall_score(query, p.stem, text)
            if score > 0:
                scored.append((score, p.stem, text))
        # Rank by score, then slug so equal scores are stable/deterministic.
        scored.sort(key=lambda s: (-s[0], s[1]))
        for _score, stem, text in scored[:RECALL_TOP_FACTS]:
            hits.append(f"[{stem}]\n{text.strip()}")
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
