"""Skill discovery and loading — user-authored markdown instruction files.

Standard library only. Skills live in two places: ~/.luban/skills/*.md
(global, follows the user) and <project>/.luban/skills/*.md (project-scoped,
committable to the project repo). Same name -> project wins. A skill file's
first line may be `description: <one-liner>`; otherwise the first non-empty
line doubles as the description (truncated).
"""
from __future__ import annotations

import sys
from pathlib import Path

GLOBAL_SKILLS_DIR = Path.home() / ".luban" / "skills"

_DESC_PREFIX = "description:"
_DESC_MAX = 80


def _project_dir(project_root: Path | str) -> Path:
    return Path(project_root) / ".luban" / "skills"


def _parse(text: str) -> tuple[str, str]:
    """Split a skill file's text into (description, body)."""
    lines = text.splitlines()
    if lines and lines[0].lower().startswith(_DESC_PREFIX):
        desc = lines[0][len(_DESC_PREFIX):].strip()
        body = "\n".join(lines[1:]).strip()
        return desc, body
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    return first[:_DESC_MAX], text.strip()


def _scan(directory: Path, scope: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.md")):
        try:
            desc, _ = _parse(path.read_text(encoding="utf-8"))
        except Exception:
            print(f"warning: skipping unreadable skill file {path.name}", file=sys.stderr)
            continue
        found[path.stem] = {"name": path.stem, "description": desc, "scope": scope}
    return found


def list_skills(project_root: Path | str) -> list[dict]:
    skills = _scan(GLOBAL_SKILLS_DIR, "global")
    skills.update(_scan(_project_dir(project_root), "project"))  # project wins
    return sorted(skills.values(), key=lambda s: s["name"])


def load_skill(name: str, project_root: Path | str) -> str | None:
    for directory in (_project_dir(project_root), GLOBAL_SKILLS_DIR):  # project first
        path = directory / f"{name}.md"
        if path.is_file():
            try:
                _, body = _parse(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            return body
    return None
