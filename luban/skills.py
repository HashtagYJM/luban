"""Skill discovery and loading — user-authored markdown instruction files.

Standard library only. Skills live in two places: ~/.luban/skills/ (global,
follows the user) and <project>/.luban/skills/ (project-scoped, committable).
Two layouts per directory, mixable:

  <name>.md            flat file; first line may be `description: <one-liner>`
  <name>/SKILL.md      folder skill (Claude Code convention); optional YAML
                       frontmatter block whose `description:` line feeds the
                       catalog; supporting files may sit beside SKILL.md

Same name: project beats global; within one directory a flat file beats a
folder. Frontmatter parsing is deliberately minimal (single-line description,
no YAML dependency).
"""
from __future__ import annotations

import sys
from pathlib import Path

GLOBAL_SKILLS_DIR = Path.home() / ".luban" / "skills"

_DESC_PREFIX = "description:"
_DESC_MAX = 80
_FRONT_DESC_MAX = 240


def _project_dir(project_root: Path | str) -> Path:
    return Path(project_root) / ".luban" / "skills"


def _parse_frontmatter(lines: list[str]) -> tuple[str, str] | None:
    """Parse a leading `---` YAML block; None when there is no closed block."""
    if not lines or lines[0].strip() != "---":
        return None
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            desc = ""
            for ln in lines[1:end]:
                if ln.strip().lower().startswith(_DESC_PREFIX):
                    desc = ln.split(":", 1)[1].strip().strip("\"'")
                    break
            body = "\n".join(lines[end + 1:]).strip()
            if not desc:
                desc = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")[:_DESC_MAX]
            return desc[:_FRONT_DESC_MAX], body
    return None  # unterminated block: treat the file as plain markdown


def _parse(text: str) -> tuple[str, str]:
    """Split a skill file's text into (description, body)."""
    lines = text.splitlines()
    front = _parse_frontmatter(lines)
    if front is not None:
        return front
    if lines and lines[0].lower().startswith(_DESC_PREFIX):
        desc = lines[0][len(_DESC_PREFIX):].strip()
        body = "\n".join(lines[1:]).strip()
        return desc, body
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    return first[:_DESC_MAX], text.strip()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _scan(directory: Path, scope: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not directory.is_dir():
        return found
    # Folders first so a flat <name>.md deterministically wins a name clash.
    candidates = sorted(directory.glob("*/SKILL.md")) + sorted(directory.glob("*.md"))
    for path in candidates:
        name = path.parent.name if path.parent != directory else path.stem
        try:
            desc, _ = _parse(_read(path))
        except Exception:
            print(f"warning: skipping unreadable skill file {path}", file=sys.stderr)
            continue
        found[name] = {"name": name, "description": desc, "scope": scope}
    return found


def list_skills(project_root: Path | str) -> list[dict]:
    skills = _scan(GLOBAL_SKILLS_DIR, "global")
    skills.update(_scan(_project_dir(project_root), "project"))  # project wins
    return sorted(skills.values(), key=lambda s: s["name"])


def load_skill(name: str, project_root: Path | str) -> str | None:
    if not name or "/" in name or "\\" in name or ".." in name:
        return None  # never let a tool-supplied name walk the filesystem
    for directory in (_project_dir(project_root), GLOBAL_SKILLS_DIR):  # project first
        for path in (directory / f"{name}.md", directory / name / "SKILL.md"):
            if not path.is_file():
                continue
            try:
                _, body = _parse(_read(path))
            except Exception:
                return None
            if path.name == "SKILL.md":
                # Global skill folders sit outside the project-root jail, so
                # point the model at run_command (not read_file) for assets.
                return (
                    f"(Skill folder: {path.parent} — supporting files referenced "
                    f"by this skill live there; read them with run_command.)\n\n{body}"
                )
            return body
    return None
