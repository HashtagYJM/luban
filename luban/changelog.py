"""Read the bundled CHANGELOG.md so luban can show 'what's new' and reconcile its
enhancement tracker against a new release entirely offline (no network — the notes
ship inside the wheel). All functions are non-raising: a missing/unreadable
changelog degrades to empty output, never a crash."""
from __future__ import annotations

import importlib.resources


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
