from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from luban import hooks as hooks_mod
from luban import memory as memory_mod
from luban import paths
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
    allow_out_of_tree: bool = False  # config gate for editing files outside the project
    subagent: Callable[[str], str] | None = None  # run a nested read-only sub-agent
    # The tools this context may call AT ALL. None means the full dispatch. Withholding a
    # tool by leaving it out of the schema list is a request, not a control: the model
    # still has the whole prior conversation, including turns where it WAS offered, and a
    # proxied or rogue backend need not read the schema at all. run_tool is the one choke
    # point every call passes through, so the capability lives here.
    only: frozenset[str] | None = None
    # Lifecycle hooks available on THIS context. A subagent and the pre-compact flush
    # turn build their own contexts and get none: a nested read-only run must not fire a
    # write-check, and the flush turn must not spend the compact budget on hooks.
    hooks: list = field(default_factory=list)
    notify: Callable[[str], None] | None = None  # say something to the human mid-turn


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


LUBAN_HOME = paths.luban_home()  # single resolved home (tests monkeypatch this)


def resolve_tool_path(
    root: Path, path: str, writing: bool = False, allow_out_of_tree: bool = False
) -> Path:
    """Resolve a tool-supplied path in tiers.

    1. Relative → jailed to the project root.
    2. Absolute under the project root → allowed.
    3. Absolute under the user's own ~/.luban area → allowed, but with two
       guardrails: Python files there (client_local.py holds credentials,
       tools_local.py executes at startup) are off-limits, and the audit log is
       never writable.
    4. Absolute anywhere else (out of tree) → refused by default; permitted only
       when allow_out_of_tree is set (config), in which case the caller's normal
       diff-and-confirm flow applies — the same safety `run_command` already has.
    """
    home = LUBAN_HOME.resolve()
    root_resolved = Path(root).resolve()
    # The documented ~/.luban alias must point at the (possibly relocated) luban
    # home — NOT the OS home. Path.expanduser() sends ~ to the OS home, so on a
    # box where LUBAN_HOME is relocated (e.g. a OneDrive folder) the model's
    # natural "~/.luban/…" paths would resolve outside the jail and be rejected,
    # leaving luban unable to edit its own memory/tracker/config (E10). Map the
    # alias to LUBAN_HOME first; everything else uses normal ~ expansion.
    norm = str(path).replace("\\", "/")
    if norm == "~/.luban" or norm.startswith("~/.luban/"):
        rest = norm[len("~/.luban"):].lstrip("/")
        expanded = home / rest if rest else home
    else:
        expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        return resolve_in_root(root, path)
    target = expanded.resolve()
    # Tier 2: absolute path inside the project root is fine (no ~/.luban guards).
    if target == root_resolved or root_resolved in target.parents:
        return target
    # Tier 4 (checked before the ~/.luban guards so a sibling project's data.py
    # stays editable): out-of-tree paths are gated by config, then fall through to
    # the caller's confirm flow. The .py/audit guards below are ~/.luban-only.
    if not (target == home or home in target.parents):
        if allow_out_of_tree:
            return target
        # Say WHERE the alias actually points. On a relocated home the model's guess
        # ("C:/Users/me/.luban/…", or a shell '~' it expanded itself) lands on the OS
        # home and is refused — and the old message ("escapes the project root and
        # ~/.luban") gave it nothing to correct with, so it would try the same wrong
        # path again. Naming the real home makes the next attempt right.
        raise ValueError(
            f"Path is outside both the project root and luban's home: {path}\n"
            f"  project root: {root_resolved}\n"
            f"  luban home:   {home}\n"
            "  Use the '~/.luban/...' alias with the FILE TOOLS to reach luban's home "
            "— it maps to the path above. Do NOT expand '~' yourself, and do not reach "
            "it through run_command: a shell '~' resolves to the OS home, not here."
        )
    # Windows strips trailing dots/spaces from the final component at open time,
    # so "client_local.py " and "tools_local.py." reach the real .py file while
    # pathlib keeps the tail lexically. Normalize before classifying. Case-folded:
    # NTFS/macOS resolve TOOLS_LOCAL.PY to tools_local.py. Only ".py" is blocked
    # because nothing adds ~/.luban to sys.path.
    stem_name = target.name.rstrip(" .").lower()
    if stem_name.endswith(".py"):
        raise ValueError(f"Python files under ~/.luban are off-limits to file tools: {path}")
    if writing and stem_name == "audit.jsonl":
        raise ValueError(f"The audit log is not writable via file tools: {path}")
    return target


def _list_dir(inp: dict, ctx: ToolContext) -> ToolResult:
    try:
        target = resolve_tool_path(
            ctx.project_root, inp.get("path", "."),
            allow_out_of_tree=ctx.allow_out_of_tree,
        )
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
        target = resolve_tool_path(
            ctx.project_root, inp["path"], allow_out_of_tree=ctx.allow_out_of_tree
        )
        text = target.read_text(encoding="utf-8", errors="replace")
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
    try:
        # Same resolver as read_file/list_dir so the ~/.luban alias, the project
        # jail, and out-of-tree gating all behave identically (E18).
        base = resolve_tool_path(
            root, inp.get("path", "."), allow_out_of_tree=ctx.allow_out_of_tree
        )
    except (ValueError, KeyError) as exc:
        return ToolResult(str(exc), is_error=True)
    if not base.exists():
        # A silent "(no matches)" for an unsearchable path reads as a genuine
        # empty result and misleads the agent — error like read_file does (E4a).
        return ToolResult(f"Path not found: {inp.get('path', '.')}", is_error=True)
    home = LUBAN_HOME.resolve()
    files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
    hits = []
    for f in files:
        # Never expose the contents of ~/.luban Python (client_local.py holds
        # credentials) even though grep can now reach the home area.
        if (f == home or home in f.parents) and f.name.rstrip(" .").lower().endswith(".py"):
            continue
        try:
            for n, line in enumerate(
                f.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if rx.search(line):
                    try:
                        disp = f.relative_to(root)
                    except ValueError:
                        disp = f  # under ~/.luban or out-of-tree: show the full path
                    hits.append(f"{disp}:{n}: {line.strip()}")
        except (UnicodeDecodeError, OSError, ValueError):
            continue
    return ToolResult(_truncate("\n".join(hits) or "(no matches)"))


# One implementation for the whole codebase — see paths.atomic_write_text for why the
# order matters. Aliased rather than re-exported so existing call sites read unchanged.
_atomic_write_text = paths.atomic_write_text


def _write_file(inp: dict, ctx: ToolContext) -> ToolResult:
    try:
        target = resolve_tool_path(
            ctx.project_root, inp["path"], writing=True,
            allow_out_of_tree=ctx.allow_out_of_tree,
        )
    except (ValueError, KeyError) as exc:
        return ToolResult(f"Bad request: {exc}", is_error=True)
    old = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    new = inp["content"]
    ctx.render_diff(inp["path"], old, new)
    if not ctx.confirm(f"Write {inp['path']}?"):
        return ToolResult("User declined the write.")
    try:
        _atomic_write_text(target, new)
    except (OSError, ValueError, UnicodeError) as exc:
        return ToolResult(f"Could not write {inp['path']}: {exc}", is_error=True)
    return ToolResult(f"Wrote {inp['path']} ({len(new)} chars).")


def _edit_file(inp: dict, ctx: ToolContext) -> ToolResult:
    try:
        target = resolve_tool_path(
            ctx.project_root, inp["path"], writing=True,
            allow_out_of_tree=ctx.allow_out_of_tree,
        )
        old = target.read_text(encoding="utf-8", errors="replace")
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
    try:
        _atomic_write_text(target, new)
    except (OSError, ValueError, UnicodeError) as exc:
        return ToolResult(f"Could not edit {inp['path']}: {exc}", is_error=True)
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


def _spawn(command: str, cwd, merge_stderr: bool = False) -> subprocess.Popen:
    """The one place a child process is started. Foreground runs, background jobs and
    lifecycle hooks all come through here, so the UTF-8 decoding, the DEVNULL stdin and
    the process-group setup that makes _kill_tree work cannot drift apart."""
    return subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,  # interactive children EOF instead of hanging
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True,
        encoding="utf-8",  # decode child output as UTF-8 (children run in UTF-8 mode
        errors="replace",  # via PYTHONUTF8); never charmap-crash reading their output
        start_new_session=(sys.platform != "win32"),  # POSIX: own process group so we can kill the whole tree
    )


MAX_BACKGROUND_JOBS = 8  # a runaway loop must not fork-bomb the machine luban runs on


@dataclass
class _Job:
    handle: str
    command: str
    proc: subprocess.Popen
    buffer: list          # appended by the drainer thread
    read_to: int = 0      # chars already handed to the model — reads are incremental


_JOBS: dict[str, _Job] = {}
_JOB_SEQ = 0


def _drain(job: _Job) -> None:
    # A child that fills the pipe buffer blocks forever if nobody reads it. Draining on
    # a thread is what makes "spawn now, read later" safe — it is the deadlock that the
    # hand-built `start /min cmd /c "... > log"` workaround exists to dodge.
    try:
        for line in job.proc.stdout:
            job.buffer.append(line)
    except Exception:
        pass
    finally:
        try:
            job.proc.stdout.close()
        except Exception:
            pass


def _start_background(command: str, ctx: ToolContext) -> ToolResult:
    global _JOB_SEQ
    live = [j for j in _JOBS.values() if j.proc.poll() is None]
    if len(live) >= MAX_BACKGROUND_JOBS:
        return ToolResult(
            f"Too many background jobs already running ({len(live)}). Read one to "
            "completion or kill it with read_output(kill=true) first.",
            is_error=True,
        )
    try:
        proc = _spawn(command, ctx.project_root, merge_stderr=True)
    except Exception as exc:
        return ToolResult(f"Could not start: {exc}", is_error=True)
    _JOB_SEQ += 1
    handle = f"bg{_JOB_SEQ}"
    job = _Job(handle=handle, command=command, proc=proc, buffer=[])
    _JOBS[handle] = job
    threading.Thread(target=_drain, args=(job,), daemon=True).start()
    return ToolResult(
        f"Started in the background as {handle}. It keeps running across turns; read it "
        f"with read_output(handle=\"{handle}\"). Nothing is captured for you elsewhere."
    )


def _read_output(inp: dict, ctx: ToolContext) -> ToolResult:
    handle = str(inp.get("handle", "")).strip()
    job = _JOBS.get(handle)
    if job is None:
        known = ", ".join(sorted(_JOBS)) or "(none)"
        return ToolResult(f"Unknown handle: {handle!r}. Started jobs: {known}",
                          is_error=True)
    if inp.get("kill") is True:
        if job.proc.poll() is None:
            _kill_tree(job.proc)
        # Fall through: whatever it produced before the kill is still worth reporting.
    text = "".join(job.buffer)
    fresh = text[job.read_to:]
    job.read_to = len(text)
    code = job.proc.poll()
    if code is None:
        status = "still running"
    else:
        status = f"finished, exit code {code}"
        # Keep the record so a later read can still report the exit code, but the
        # buffer has been fully handed over.
    body = fresh or "(no new output)"
    return ToolResult(_truncate(f"[{handle}: {status}]\n{body}"))


def kill_all_jobs() -> list[str]:
    """Kill every still-running background job. Returns the handles that were killed.

    Called at session exit: a job outliving the session that started it is an orphaned
    process tree nobody can see, which is the failure background execution is meant to
    remove rather than relocate.
    """
    killed = []
    for handle, job in list(_JOBS.items()):
        if job.proc.poll() is None:
            _kill_tree(job.proc)
            killed.append(handle)
    _JOBS.clear()
    return killed


def _run_command(inp: dict, ctx: ToolContext) -> ToolResult:
    command = inp["command"]
    timeout = min(int(inp.get("timeout", 120)), MAX_COMMAND_TIMEOUT)
    ctx.render_command(command)
    if not ctx.confirm(f"Run: {command}"):
        return ToolResult("User declined the command.")
    if inp.get("background") is True:
        return _start_background(command, ctx)
    proc = _spawn(command, ctx.project_root)
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


def _checkpoint(inp: dict, ctx: ToolContext) -> ToolResult:
    status = " ".join(inp.get("status", "").split())
    if not status:
        return ToolResult("Empty checkpoint status.", is_error=True)
    project = Path(ctx.project_root).name
    ctx.render_command(f"checkpoint[{project}] = {status}")
    if not ctx.confirm("Update the continuity pointer?"):
        return ToolResult("User declined the checkpoint.")
    memory_mod.checkpoint(project, status)
    return ToolResult(
        f"Checkpoint saved — [{memory_mod.checkpoint_slug(project)}] now reads: {status}"
    )


def _journal(inp: dict, ctx: ToolContext) -> ToolResult:
    text = inp.get("text", "").strip()
    if not text:
        return ToolResult("Empty journal entry.", is_error=True)
    ctx.render_command(f"journal += {text}")
    if not ctx.confirm("Append to journal?"):
        return ToolResult("User declined the journal entry.")
    memory_mod.journal_append(text, project=Path(ctx.project_root).name)
    limit = memory_mod.journal_entry_limit()
    if len(text) <= limit:
        return ToolResult("Journal updated.")
    # Written, never refused — the content is already worth more than the rule. What was
    # missing was a per-entry signal: the cap bounds the journal's total, so a bloated
    # entry stays legal and quietly costs every older day.
    return ToolResult(
        f"Journal updated — but this entry is {len(text):,} characters against a "
        f"~{limit:,} guide. The journal is a TIMELINE, sent whole on every turn and "
        f"holding roughly {memory_mod.JOURNAL_ENTRIES_PER_WINDOW} entries in total, so an "
        f"entry this size evicts earlier days outright. Keep it to what happened and why. "
        f"Edit plans, code and tracebacks belong in a file under docs/ — and the full "
        f"detail is already in the session transcript, which is searchable.")


def _spawn_subagent(inp: dict, ctx: ToolContext) -> ToolResult:
    if ctx.subagent is None:
        return ToolResult(
            "Subagents are not enabled (set subagents = true in ~/.luban/config.toml).",
            is_error=True,
        )
    task = inp.get("task")
    if not isinstance(task, str) or not task.strip():
        return ToolResult("Bad request: 'task' must be a non-empty string.", is_error=True)
    try:
        return ToolResult(_truncate(ctx.subagent(task)))
    except Exception as exc:  # a sub-run failure must not kill the parent turn
        return ToolResult(f"Subagent failed: {exc}", is_error=True)


# Offered only when config.subagents is on (build_agent_config appends it); the
# handler is always registered so run_tool can dispatch it when offered.
SUBAGENT_TOOL = {
    "name": "spawn_subagent",
    "description": (
        "Run a fresh read-only sub-agent on a focused, self-contained sub-task and "
        "get back its final answer. Use it to research or investigate in parallel "
        "with your own work, or to isolate a big read-heavy subtask from your "
        "context. The sub-agent can read/search/recall but cannot write files or run "
        "commands. Give it a complete, standalone task description."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"task": {"type": "string", "description": "Self-contained task for the sub-agent."}},
        "required": ["task"],
    },
}


_DISPATCH = {
    "spawn_subagent": _spawn_subagent,
    "list_dir": _list_dir,
    "glob": _glob,
    "grep": _grep,
    "read_file": _read_file,
    "write_file": _write_file,
    "edit_file": _edit_file,
    "run_command": _run_command,
    "read_output": _read_output,
    "load_skill": _load_skill,
    "sessions": _sessions,
    "remember": _remember,
    "forget": _forget,
    "recall": _recall,
    "journal": _journal,
    "checkpoint": _checkpoint,
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
        "description": "Run a shell command in the project root. Shows the command and "
        "asks to confirm. Set background=true for anything long-running (a build, a test "
        "suite, a server): it returns a handle immediately instead of blocking the turn, "
        "and you read it with read_output. Do NOT hand-roll a detached command with output "
        "redirected to a log file — that is what background=true replaces.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds, default 120. Ignored when background=true."},
                "background": {"type": "boolean", "description": "Run without waiting; returns a handle for read_output."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_output",
        "description": "Read new output from a background command started with "
        "run_command(background=true), and whether it is still running or has exited. "
        "Each call returns only what has arrived SINCE the last read. Set kill=true to "
        "terminate it (the last output is still returned).",
        "input_schema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "The handle run_command returned, e.g. bg1"},
                "kill": {"type": "boolean", "description": "Terminate the job's whole process tree."},
            },
            "required": ["handle"],
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
        "are JSON files under ~/.luban/sessions/, readable with read_file — use "
        "them (not the journal) to recover what a past session was actually doing. "
        "Read them via the ~/.luban alias, never a shell '~'.",
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
        "description": "Read the full text of a long-term memory fact. The memory index "
        "in your system prompt already lists EVERY fact that exists — normally you should "
        "pick the one you want from that index and pass its exact slug here.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {
                "type": "string",
                "description": "An exact slug from the memory index (e.g. "
                "'prefers-plotly') to read that fact — this is the normal use. Only "
                "if no index entry looks right, pass a few distinctive words to "
                "search bodies and the journal.",
            }},
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
        "name": "checkpoint",
        "description": "Record where THIS project now stands and what the next step is, "
        "in one sentence. It overwrites the project's continuity pointer, which is the "
        "line you will read in the memory index at the start of the next session — so "
        "name the concrete next action and the file to open, not what you have been "
        "doing. Use it when a milestone actually lands or the next step changes; luban "
        "also refreshes it at /compact and at exit.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {
                "type": "string",
                "description": "One sentence: state, then next step and where it lives.",
            }},
            "required": ["status"],
        },
    },
    {
        "name": "journal",
        "description": "Append a short note to today's journal: what happened and what "
        "was decided, as a POINTER — name what moved and the file that holds the detail. "
        f"Hard guide: keep it under {memory_mod.journal_entry_limit():,} characters — "
        "the journal is a timeline sent whole on every turn, and one long entry evicts "
        "earlier days. Plans, code and tracebacks belong in a file. Write out in full "
        "only what has no other home: a reversal, or something that behaved unlike its "
        "documentation.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]

MEMORY_TOOL_NAMES = {"remember", "forget", "recall", "journal", "checkpoint"}


def active_tools(memory_enabled: bool = True) -> list[dict]:
    """Tool schemas to offer the model; memory tools hidden when disabled."""
    if memory_enabled:
        return TOOLS
    return [t for t in TOOLS if t["name"] not in MEMORY_TOOL_NAMES]


_CUSTOM_NAMES: set[str] = set()
# name -> guidance: per-tool usage hints (when/how/cross-tool) that a TOOLS entry can
# contribute to the system prompt. The API `tools` param carries each tool's
# `description`, but there is no channel there for orchestration guidance across a
# suite of tools — this is it (E25).
_CUSTOM_GUIDANCE: dict[str, str] = {}
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
        guidance = spec.get("guidance")
        if isinstance(guidance, str) and guidance.strip():
            _CUSTOM_GUIDANCE[name] = guidance.strip()
        _CUSTOM_NAMES.add(name)
        registered.append(name)
    return registered


def custom_guidance() -> list[tuple[str, str]]:
    """(tool_name, guidance) for every custom tool that supplied usage guidance,
    in registration order. Fed into the system prompt so a growing tool suite can
    carry orchestration hints, not just per-tool descriptions (E25)."""
    return list(_CUSTOM_GUIDANCE.items())  # dict preserves registration order


def reset_custom() -> None:
    """Remove every registered custom tool (test isolation hook)."""
    for name in _CUSTOM_NAMES:
        _DISPATCH.pop(name, None)
        READ_ONLY_TOOLS.discard(name)
        permissions_mod._TARGET_KEY.pop(name, None)
    TOOLS[:] = [t for t in TOOLS if t["name"] not in _CUSTOM_NAMES]
    _CUSTOM_GUIDANCE.clear()
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
    if ctx.only is not None and name not in ctx.only:
        # Not a permission decision — the tool was never on offer here. Reported to the
        # model and the audit trail both, because no tool call is silently dropped.
        out = ToolResult(f"Blocked: {name} is not available on this turn.", is_error=True)
        _audit_call(ctx, name, tool_input, "not_offered", out)
        return out
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
    return _fire_post_tool_use(name, ctx, out)


def _fire_post_tool_use(name: str, ctx: ToolContext, out: ToolResult) -> ToolResult:
    """Run any post_tool_use hook and hang its output off THIS tool's result.

    Fired here rather than in the turn loop because run_tool is the choke point every
    call already passes through — so a context that carries no hooks (a subagent, the
    flush turn) cannot fire one, and a tool added later gets the behaviour for free.
    """
    if not ctx.hooks:
        return out
    try:
        injected = hooks_mod.run_hooks(
            ctx.hooks, "post_tool_use", ctx.project_root, tool_name=name,
            decide=_hook_decider(ctx), audit=ctx.audit, notify=ctx.notify,
        )
    except Exception:
        return out  # a broken hook must never turn a good tool call into a failure
    if not injected:
        return out
    return ToolResult(f"{out.content}\n\n{injected}", is_error=out.is_error)


def _hook_decider(ctx: ToolContext):
    """Deny rules still apply to a hook, even though declaring one is the consent to
    run it. Deny can only ever subtract, so honouring it cannot surprise anyone."""
    if ctx.decide is None:
        return None
    return lambda command: ctx.decide("run_command", {"command": command})
