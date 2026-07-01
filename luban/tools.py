from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MAX_OUTPUT = 20000  # chars; truncate large tool output to protect context


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


@dataclass
class ToolContext:
    project_root: Path
    confirm: Callable[[str], bool]
    render_diff: Callable[[str, str, str], None]
    render_command: Callable[[str], None]


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n... [truncated {len(text) - MAX_OUTPUT} chars]"


def resolve_in_root(root: Path, path: str) -> Path:
    root = Path(root).resolve()
    target = (root / path).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"Path escapes project root: {path}")
    return target


def _list_dir(inp: dict, ctx: ToolContext) -> ToolResult:
    try:
        target = resolve_in_root(ctx.project_root, inp.get("path", "."))
        if not target.is_dir():
            return ToolResult(f"Not a directory: {inp.get('path', '.')}", is_error=True)
        names = sorted(
            p.name + ("/" if p.is_dir() else "") for p in target.iterdir()
        )
        return ToolResult(_truncate("\n".join(names) or "(empty)"))
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)


def _read_file(inp: dict, ctx: ToolContext) -> ToolResult:
    try:
        target = resolve_in_root(ctx.project_root, inp["path"])
        text = target.read_text()
    except (ValueError, KeyError) as exc:
        return ToolResult(f"Bad request: {exc}", is_error=True)
    except FileNotFoundError:
        return ToolResult(f"File not found: {inp['path']}", is_error=True)
    lines = text.splitlines()
    try:
        start = int(inp.get("start", 1))
        end = int(inp.get("end", len(lines)))
    except (ValueError, TypeError):
        return ToolResult("start/end must be integers.", is_error=True)
    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines[start - 1:end], start))
    return ToolResult(_truncate(numbered))


def _glob(inp: dict, ctx: ToolContext) -> ToolResult:
    root = Path(ctx.project_root).resolve()
    matches = sorted(
        str(p.relative_to(root)) for p in root.glob(inp["pattern"]) if p.is_file()
    )
    return ToolResult(_truncate("\n".join(matches) or "(no matches)"))


def _grep(inp: dict, ctx: ToolContext) -> ToolResult:
    root = Path(ctx.project_root).resolve()
    try:
        rx = re.compile(inp["pattern"])
    except re.error as exc:
        return ToolResult(f"Bad regex: {exc}", is_error=True)
    base = resolve_in_root(root, inp.get("path", "."))
    files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
    hits = []
    for f in files:
        try:
            for n, line in enumerate(f.read_text().splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(root)}:{n}: {line.strip()}")
        except (UnicodeDecodeError, OSError):
            continue
    return ToolResult(_truncate("\n".join(hits) or "(no matches)"))
