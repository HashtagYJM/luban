from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from luban import skills as skills_mod

MAX_OUTPUT = 20000  # chars; truncate large tool output to protect context
MAX_COMMAND_TIMEOUT = 600  # seconds; cap model-supplied run_command timeouts


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
    start = max(1, start)
    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines[start - 1:end], start))
    return ToolResult(_truncate(numbered))


def _glob(inp: dict, ctx: ToolContext) -> ToolResult:
    root = Path(ctx.project_root).resolve()
    matches = []
    for p in root.glob(inp["pattern"]):
        if not p.is_file():
            continue
        rp = p.resolve()
        if rp != root and root not in rp.parents:
            continue  # drop matches that escape the project root
        matches.append(str(rp.relative_to(root)))
    return ToolResult(_truncate("\n".join(sorted(matches)) or "(no matches)"))


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
        except (UnicodeDecodeError, OSError, ValueError):
            continue
    return ToolResult(_truncate("\n".join(hits) or "(no matches)"))


def _write_file(inp: dict, ctx: ToolContext) -> ToolResult:
    try:
        target = resolve_in_root(ctx.project_root, inp["path"])
    except (ValueError, KeyError) as exc:
        return ToolResult(f"Bad request: {exc}", is_error=True)
    old = target.read_text() if target.exists() else ""
    new = inp["content"]
    ctx.render_diff(inp["path"], old, new)
    if not ctx.confirm(f"Write {inp['path']}?"):
        return ToolResult("User declined the write.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new)
    return ToolResult(f"Wrote {inp['path']} ({len(new)} chars).")


def _edit_file(inp: dict, ctx: ToolContext) -> ToolResult:
    try:
        target = resolve_in_root(ctx.project_root, inp["path"])
        old = target.read_text()
    except (ValueError, KeyError) as exc:
        return ToolResult(f"Bad request: {exc}", is_error=True)
    except FileNotFoundError:
        return ToolResult(f"File not found: {inp['path']}", is_error=True)
    count = old.count(inp["old_string"])
    if count == 0:
        return ToolResult("old_string not found in file.", is_error=True)
    if count > 1:
        return ToolResult(
            f"old_string is not unique ({count} matches); add more context.",
            is_error=True,
        )
    new = old.replace(inp["old_string"], inp["new_string"])
    ctx.render_diff(inp["path"], old, new)
    if not ctx.confirm(f"Edit {inp['path']}?"):
        return ToolResult("User declined the edit.")
    target.write_text(new)
    return ToolResult(f"Edited {inp['path']}.")


def _kill_tree(proc: subprocess.Popen) -> None:  # type: ignore
    # shell=True spawns grandchildren; on Windows a plain kill leaves them
    # holding the console. taskkill /T takes the whole tree down.
    if sys.platform == "win32":
        subprocess.run(
            f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True
        )
    else:
        proc.kill()


def _run_command(inp: dict, ctx: ToolContext) -> ToolResult:
    command = inp["command"]
    timeout = min(int(inp.get("timeout", 120)), MAX_COMMAND_TIMEOUT)
    ctx.render_command(command)
    if not ctx.confirm(f"Run: {command}"):
        return ToolResult("User declined the command.")
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=str(ctx.project_root),
        stdin=subprocess.DEVNULL,  # interactive children EOF instead of hanging
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        partial = _truncate((out or "") + (err or ""))
        return ToolResult(
            f"Command timed out after {timeout}s (process tree killed).\n{partial}",
            is_error=True,
        )
    body = (out or "") + (err or "")
    return ToolResult(_truncate(f"{body}\n[exit code: {proc.returncode}]"))


def _load_skill(inp: dict, ctx: ToolContext) -> ToolResult:
    name = inp["name"]
    body = skills_mod.load_skill(name, ctx.project_root)
    if body is None:
        available = ", ".join(
            s["name"] for s in skills_mod.list_skills(ctx.project_root)
        ) or "(none)"
        return ToolResult(f"Unknown skill: {name}. Available: {available}", is_error=True)
    return ToolResult(_truncate(f"[skill: {name}]\n{body}"))


_DISPATCH = {
    "list_dir": _list_dir,
    "glob": _glob,
    "grep": _grep,
    "read_file": _read_file,
    "write_file": _write_file,
    "edit_file": _edit_file,
    "run_command": _run_command,
    "load_skill": _load_skill,
}

TOOLS = [
    {
        "name": "list_dir",
        "description": "List entries in a directory (relative to project root).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Dir path, default '.'"}},
        },
    },
    {
        "name": "glob",
        "description": "Find files by glob pattern across the project tree.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search file contents by regex; returns file:line: text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Dir/file to search, default '.'"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file live from disk. Optional start/end line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create/overwrite a file with full content. Shows a diff and asks to confirm.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace a unique old_string with new_string. Shows a diff and asks to confirm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command in the project root. Shows the command and asks to confirm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds, default 120"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "load_skill",
        "description": "Load a skill's full instructions by name. Available skills are listed in the system prompt.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
]


def run_tool(name: str, tool_input: dict, ctx: ToolContext) -> ToolResult:
    fn = _DISPATCH.get(name)
    if fn is None:
        return ToolResult(f"Unknown tool: {name}", is_error=True)
    try:
        return fn(tool_input, ctx)
    except Exception as exc:  # tools must never crash the loop
        return ToolResult(f"Tool error: {exc}", is_error=True)
