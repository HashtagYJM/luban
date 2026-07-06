from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from luban import memory as memory_mod
from luban import permissions as permissions_mod
from luban import sessions as sessions_mod
from luban import skills as skills_mod

MAX_OUTPUT = 20000  # chars; truncate large tool output to protect context
MAX_COMMAND_TIMEOUT = 600  # seconds; cap model-supplied run_command timeouts
READ_ONLY_TOOLS = {"list_dir", "glob", "grep", "read_file", "load_skill", "recall", "sessions"}


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
    decide: Callable[[str, dict], object] | None = None
    audit: Callable[[dict], None] | None = None


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


LUBAN_HOME = Path.home() / ".luban"  # call-time resolution (tests monkeypatch this)


def resolve_tool_path(root: Path, path: str, writing: bool = False) -> Path:
    """Resolve a tool-supplied path.

    Relative paths stay jailed to the project root. Absolute (or ~) paths are
    allowed only under the user's own ~/.luban area so the agent can maintain
    its memory, skills, and config — with two guardrails: Python files there
    (client_local.py holds credentials, tools_local.py executes at startup)
    are off-limits entirely, and the audit log is never writable.
    """
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        return resolve_in_root(root, path)
    home = LUBAN_HOME.resolve()
    target = expanded.resolve()
    if not (target == home or home in target.parents):
        raise ValueError(f"Absolute paths must stay under ~/.luban: {path}")
    # Case-insensitive: NTFS/macOS resolve TOOLS_LOCAL.PY to tools_local.py.
    # Only ".py" is blocked because nothing adds ~/.luban to sys.path.
    if target.suffix.lower() == ".py":
        raise ValueError(f"Python files under ~/.luban are off-limits to file tools: {path}")
    if writing and target.name.lower() == "audit.jsonl":
        raise ValueError(f"The audit log is not writable via file tools: {path}")
    return target


def _list_dir(inp: dict, ctx: ToolContext) -> ToolResult:
    try:
        target = resolve_tool_path(ctx.project_root, inp.get("path", "."))
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
        target = resolve_tool_path(ctx.project_root, inp["path"])
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
        target = resolve_tool_path(ctx.project_root, inp["path"], writing=True)
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
        target = resolve_tool_path(ctx.project_root, inp["path"], writing=True)
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
    # shell=True spawns grandchildren; killing only the shell orphans them.
    # Windows: taskkill /T takes the tree down. POSIX: the child was started
    # in its own session (start_new_session), so kill its process group.
    if sys.platform == "win32":
        subprocess.run(
            f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True
        )
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()  # group already gone or unreachable — kill the child directly


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
        start_new_session=(sys.platform != "win32"),  # POSIX: own process group so we can kill the whole tree
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


def _sessions(inp: dict, ctx: ToolContext) -> ToolResult:
    all_projects = inp.get("all") is True
    heads = sessions_mod.list_sessions(None if all_projects else str(ctx.project_root))
    if not heads:
        return ToolResult("(no saved sessions)")
    lines = []
    for h in heads:
        prefix = f"[{Path(h['project']).name}] " if all_projects else ""
        lines.append(
            f'{prefix}{h["id"]}  {h["updated"]}  {h["model"]}  '
            f'"{h["title"]}"  ({h["message_count"]} msgs)'
        )
    return ToolResult(_truncate("\n".join(lines)))


def _remember(inp: dict, ctx: ToolContext) -> ToolResult:
    name = inp.get("name", "")
    description = inp.get("description", "")
    body = inp.get("body", "")
    if not memory_mod.valid_slug(name):
        return ToolResult(
            f"Invalid memory name: {name!r} (kebab-case: a-z, 0-9, dashes, max 64).",
            is_error=True,
        )
    old = memory_mod.read_fact(name) or ""
    new = f"description: {description.strip()}\n\n{body.strip()}\n"
    ctx.render_diff(f"~/.luban/memory/{name}.md", old, new)
    if not ctx.confirm(f"Remember '{name}'?"):
        return ToolResult("User declined the memory write.")
    msg = memory_mod.remember(name, description, body)
    return ToolResult(msg, is_error=msg.startswith(("Invalid", "Could not")))


def _forget(inp: dict, ctx: ToolContext) -> ToolResult:
    name = inp.get("name", "")
    old = memory_mod.read_fact(name)
    if old is None:
        return ToolResult(f"No memory named '{name}'.", is_error=True)
    ctx.render_diff(f"~/.luban/memory/{name}.md", old, "")
    if not ctx.confirm(f"Forget '{name}'?"):
        return ToolResult("User declined the memory delete.")
    msg = memory_mod.forget(name)
    return ToolResult(msg, is_error=msg.startswith(("Invalid", "No memory", "Could not")))


def _recall(inp: dict, ctx: ToolContext) -> ToolResult:
    return ToolResult(memory_mod.recall(inp.get("query", "")))


def _journal(inp: dict, ctx: ToolContext) -> ToolResult:
    text = inp.get("text", "").strip()
    if not text:
        return ToolResult("Empty journal entry.", is_error=True)
    ctx.render_command(f"journal += {text}")
    if not ctx.confirm("Append to journal?"):
        return ToolResult("User declined the journal entry.")
    memory_mod.journal_append(text)
    return ToolResult("Journal updated.")


_DISPATCH = {
    "list_dir": _list_dir,
    "glob": _glob,
    "grep": _grep,
    "read_file": _read_file,
    "write_file": _write_file,
    "edit_file": _edit_file,
    "run_command": _run_command,
    "load_skill": _load_skill,
    "sessions": _sessions,
    "remember": _remember,
    "forget": _forget,
    "recall": _recall,
    "journal": _journal,
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
    {
        "name": "sessions",
        "description": "List saved conversation sessions for this project "
        "(newest first). Set all=true to include every project. Full transcripts "
        "are JSON files under ~/.luban/sessions/, readable with read_file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "description": "include all projects, default false"}
            },
        },
    },
    {
        "name": "remember",
        "description": "Save or update a durable long-term memory fact about the user, "
        "their practices, or standing decisions (persists across sessions and projects). "
        "Update existing facts rather than creating near-duplicates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case slug, e.g. 'prefers-plotly'"},
                "description": {"type": "string", "description": "one-line summary for the memory index"},
                "body": {"type": "string", "description": "the full fact"},
            },
            "required": ["name", "description", "body"],
        },
    },
    {
        "name": "recall",
        "description": "Search long-term memory (facts and journal) for details behind "
        "the memory index shown in the system prompt.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "forget",
        "description": "Delete a stale or wrong long-term memory fact by name.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "journal",
        "description": "Append a short note to today's journal: what happened, "
        "decisions made, progress. Keep entries to a few lines.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]

MEMORY_TOOL_NAMES = {"remember", "forget", "recall", "journal"}


def active_tools(memory_enabled: bool = True) -> list[dict]:
    """Tool schemas to offer the model; memory tools hidden when disabled."""
    if memory_enabled:
        return TOOLS
    return [t for t in TOOLS if t["name"] not in MEMORY_TOOL_NAMES]


_CUSTOM_NAMES: set[str] = set()
_PREVIEW_MAX = 200  # chars of input preview rendered before confirming


def _wrap_custom(spec: dict) -> Callable[[dict, ToolContext], ToolResult]:
    handler = spec["handler"]
    name = spec["name"]
    read_only = spec.get("read_only") is True

    def call(inp: dict, ctx: ToolContext) -> ToolResult:
        if not read_only:
            preview = ", ".join(f"{k}={v!r}" for k, v in sorted(inp.items()))
            ctx.render_command(f"{name}({preview[:_PREVIEW_MAX]})")
            if not ctx.confirm(f"Run {name}?"):
                return ToolResult(f"User declined {name}.")
        # Handler exceptions deliberately propagate: run_tool's catch turns
        # them into the standard "Tool error:" is_error result.
        return ToolResult(_truncate(str(handler(inp, ctx.project_root))))

    return call


def register_custom(specs: list[dict]) -> list[str]:
    """Merge validated custom tool specs (see custom_tools.py) into the dispatch."""
    registered = []
    for spec in specs:
        name = spec["name"]
        if name in _DISPATCH:
            print(f"warning: custom tool {name!r} collides with an existing tool; skipped",
                  file=sys.stderr)
            continue
        _DISPATCH[name] = _wrap_custom(spec)
        TOOLS.append({
            "name": name,
            "description": spec["description"],
            "input_schema": spec["input_schema"],
        })
        if spec.get("read_only") is True:
            READ_ONLY_TOOLS.add(name)
        target = spec.get("permission_target")
        if isinstance(target, str) and target:
            permissions_mod._TARGET_KEY[name] = target
        _CUSTOM_NAMES.add(name)
        registered.append(name)
    return registered


def reset_custom() -> None:
    """Remove every registered custom tool (test isolation hook)."""
    for name in _CUSTOM_NAMES:
        _DISPATCH.pop(name, None)
        READ_ONLY_TOOLS.discard(name)
        permissions_mod._TARGET_KEY.pop(name, None)
    TOOLS[:] = [t for t in TOOLS if t["name"] not in _CUSTOM_NAMES]
    _CUSTOM_NAMES.clear()


def _audit_call(ctx: ToolContext, name: str, tool_input: dict, decision: str, out: ToolResult) -> None:
    if ctx.audit is None:
        return
    try:
        ctx.audit({
            "tool": name,
            "target": permissions_mod.target_of(name, tool_input),
            "decision": decision,
            "is_error": out.is_error,
        })
    except Exception:
        pass  # auditing must never break the loop


def run_tool(name: str, tool_input: dict, ctx: ToolContext) -> ToolResult:
    fn = _DISPATCH.get(name)
    if fn is None:
        return ToolResult(f"Unknown tool: {name}", is_error=True)
    decision = ctx.decide(name, tool_input) if ctx.decide is not None else None
    if decision is not None and decision.action == "deny":
        out = ToolResult(f"Blocked: {decision.reason}", is_error=True)
        _audit_call(ctx, name, tool_input, "deny_rule", out)
        return out
    call_ctx = ctx
    if decision is not None and decision.action == "allow" and name not in READ_ONLY_TOOLS:
        # Rule-approved: skip the ask, but handlers still render the diff/command.
        call_ctx = replace(ctx, confirm=lambda prompt: True)
    try:
        out = fn(tool_input, call_ctx)
    except Exception as exc:  # tools must never crash the loop
        out = ToolResult(f"Tool error: {exc}", is_error=True)
    _audit_call(ctx, name, tool_input, decision.action if decision is not None else "", out)
    return out
