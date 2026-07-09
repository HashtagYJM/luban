"""Read the bundled CHANGELOG.md so luban can show 'what's new' and reconcile its
enhancement tracker against a new release entirely offline (no network — the notes
ship inside the wheel). All functions are non-raising: a missing/unreadable
changelog degrades to empty output, never a crash."""
from __future__ import annotations

import importlib.resources
import re

_VER_HEAD = re.compile(r"^## v(\d+(?:\.\d+)*)\b")


def read_changelog() -> str:
    try:
        return (
            importlib.resources.files("luban")
            .joinpath("CHANGELOG.md")
            .read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ModuleNotFoundError):
        return ""


def section_for(version: str, text: str | None = None) -> str:
    """Return the changelog body under the `## v<version>` heading (without the
    heading line), trimmed. Empty string if there's no matching section."""
    text = read_changelog() if text is None else text
    if not text or not version:
        return ""
    marker = f"## v{version}"
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("## "):
            if capturing:  # reached the next version's heading — stop
                break
            capturing = line.strip() == marker or line.startswith(marker + " ")
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _parse_ver(s: str) -> tuple:
    try:
        return tuple(int(x) for x in s.split("."))
    except ValueError:
        return ()


def sections_between(prev: str | None, cur: str, text: str | None = None) -> str:
    """All changelog sections for versions in the range (prev, cur] — the whole
    cumulative span since the user's last-seen version. A multi-version upgrade
    (e.g. 0.5.7 -> 0.5.12) must surface EVERY intermediate release's notes, not
    just the newest, or the tracker reconcile misses fixes (E17). prev None (or
    unparseable) -> just the cur section."""
    text = read_changelog() if text is None else text
    if not text:
        return ""
    prev_t = _parse_ver(prev) if prev else ()
    cur_t = _parse_ver(cur) if cur else ()
    lines = text.splitlines()
    idxs = [i for i, ln in enumerate(lines) if _VER_HEAD.match(ln)]
    out: list[str] = []
    for j, i in enumerate(idxs):
        v = _parse_ver(_VER_HEAD.match(lines[i]).group(1))
        if prev_t and not v > prev_t:
            continue
        if cur_t and not v <= cur_t:
            continue
        end = idxs[j + 1] if j + 1 < len(idxs) else len(lines)
        out.append("\n".join(lines[i:end]).strip())
    return "\n\n".join(out).strip()
