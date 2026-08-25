"""Lifecycle hooks — the one way luban can say "this ALWAYS happens" and have it bind.

Everything else that says *always* in luban is prose: USER.md, the project memory file, a
skill. The model reads it and complies most of the time, which is exactly the failure E36
records three times over — a skill that never self-loads, a verification that gets skipped,
a plan that drifts out of attention. A hook is run by luban itself, on an event, whether or
not the model would have chosen to.

It stays cheap because it is EVENT-driven: nothing is declared, nothing runs, nothing is
spent. That is what separates it from the rejected always-on skill load (E22), which paid
its cost every turn whether the skill was relevant or not.

Deliberately NOT here: a hook cannot block, deny, or redirect a turn. Upstream harnesses
allow that (exit code 2, `decision: block`) and it is where their footguns live; a blocking
completion gate is also the parked `/goal` continuation loop under another name. luban's
`stop` hook therefore gives the completion REMINDER, not the gate.
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from luban import paths

EVENTS = ("session_start", "user_prompt_submit", "post_tool_use", "stop")

# Only tool events carry a name to filter on. `match` anywhere else would sit in the
# config looking effective and never apply to anything.
MATCHABLE = ("post_tool_use",)

# Events whose injection is REPLACED rather than repeated — see strip_previous.
REPLACING = ("user_prompt_submit", "stop")

# Half the tool-output cap. A tool result is read once; hook output rides in the prompt
# for the rest of the session, so it is held to a tighter bound. Lands at the same
# 10,000 characters Claude Code documents for its own hook output.
MAX_HOOK_OUTPUT = 10_000

DEFAULT_TIMEOUT = 60   # a hook blocks the thing it fires on; a slow one is a stalled prompt
MAX_TIMEOUT = 600

OPEN = "[hook: {event}]"
CLOSE = "[/hook: {event}]"


@dataclass(frozen=True)
class Hook:
    event: str
    run: str
    match: str = ""          # tool name, post_tool_use only
    project: str = ""        # glob on the project name/path; empty fires everywhere
    inject: bool = True      # False = pure side effect, nothing enters context
    timeout: int = DEFAULT_TIMEOUT


def parse(entries: list) -> tuple[list[Hook], list[str]]:
    """Config tables -> hooks, plus warnings for the ones that were dropped.

    A malformed hook is dropped and REPORTED. Silently ignoring it would leave the user
    believing a guarantee is in force when nothing is running — the failure this whole
    module exists to remove.
    """
    out: list[Hook] = []
    warnings: list[str] = []
    for i, e in enumerate(entries or []):
        where = f"[[hooks]] entry {i + 1}"
        if not isinstance(e, dict):
            warnings.append(f"{where}: not a table — ignored.")
            continue
        event = str(e.get("event", "")).strip()
        run = str(e.get("run", "")).strip()
        if event not in EVENTS:
            warnings.append(
                f"{where}: unknown event {event!r} — ignored. "
                f"Valid events: {', '.join(EVENTS)}."
            )
            continue
        if not run:
            warnings.append(f"{where}: no `run` command — ignored.")
            continue
        match = str(e.get("match", "")).strip()
        if match and event not in MATCHABLE:
            warnings.append(
                f"{where}: `match` only applies to {', '.join(MATCHABLE)}, not "
                f"{event!r} — ignored (it would never filter anything)."
            )
            continue
        try:
            timeout = int(e.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            warnings.append(f"{where}: `timeout` is not a number — ignored.")
            continue
        out.append(Hook(
            event=event, run=run, match=match,
            project=str(e.get("project", "")).strip(),
            inject=bool(e.get("inject", True)),
            timeout=max(1, min(timeout, MAX_TIMEOUT)),
        ))
    return out, warnings


def in_project(hook, project_root) -> bool:
    """Does this hook apply to the project luban is running in?

    A glob, matched against the project's directory NAME and against its full path, so
    both `project = "glio-*"` and `project = "*/work/luban"` mean what they look like.
    Empty fires everywhere — the behaviour every existing config already has.

    The filter lives in the USER'S OWN config, and deliberately not in a config file
    discovered inside the project. luban's rule for anything executable is that it comes
    from a user-owned location only (see custom_tools.py: a cloned repo must not be able
    to inject executable tools). Reading `[[hooks]]` out of a project directory would let
    any repository run commands the moment luban started in it — a strictly worse bug
    than the targeting it would buy (E37).
    """
    if not hook.project:
        return True
    path = Path(project_root)
    name = path.name
    text = path.as_posix()
    return (fnmatch.fnmatch(name, hook.project)
            or fnmatch.fnmatch(text, hook.project)
            or fnmatch.fnmatch(text, f"*/{hook.project.lstrip('/')}"))


def for_event(hooks: list, event: str, tool_name: str = "", project_root=".") -> list:
    """The hooks that fire for this event. An empty `match` fires for every tool."""
    return [
        h for h in hooks
        if h.event == event and (not h.match or h.match == tool_name)
        and in_project(h, project_root)
    ]


def wrap(event: str, text: str) -> str:
    return f"{OPEN.format(event=event)}\n{text}\n{CLOSE.format(event=event)}"


def _block_re(event: str) -> re.Pattern:
    return re.compile(
        re.escape(OPEN.format(event=event)) + r".*?" + re.escape(CLOSE.format(event=event))
        + r"\n?",
        re.DOTALL,
    )


def strip_previous(messages: list, event: str) -> None:
    """Remove this event's earlier injections from the conversation, in place.

    Injected text lands in a user message and stays there, so a hook that recites the
    plan every turn leaves one copy per turn: paid on every call thereafter, and the
    stale copies contradict the live one. Only plain-text user messages are touched —
    a list-content message is a tool result, and editing one strands its tool_use id.
    """
    pattern = _block_re(event)
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        stripped = pattern.sub("", content)
        if stripped != content:
            msg["content"] = stripped.strip()


def _truncate(text: str) -> str:
    if len(text) <= MAX_HOOK_OUTPUT:
        return text
    return (
        text[:MAX_HOOK_OUTPUT]
        + f"\n[... truncated at {MAX_HOOK_OUTPUT} characters — a hook's output is "
        "re-sent on every later call, so it is capped tighter than a tool result.]"
    )


def hooks_dir() -> Path:
    """Where hook scripts live. User-owned, like everything else luban executes."""
    return paths.luban_home() / "hooks"


_HOOKS_TOKEN = re.compile(r"(?<![\w./\\])hooks[/\\]([^\s\"\']+)")


def resolve_run(run: str) -> str:
    """Point a bare `hooks/<script>` at the user's hooks dir.

    Without this, every hook has to be a one-liner inlined into config.toml or an
    absolute path repeated in full, because the command runs with the PROJECT as its
    working directory — so `hooks/guard.py` in the config meant a script inside whatever
    repository luban happened to be started in (E37).

    Rewritten only when the file actually exists in the user's hooks dir, so a project
    that has a `hooks/` directory of its own keeps working exactly as it did.
    """
    def swap(m: re.Match) -> str:
        target = hooks_dir() / m.group(1)
        try:
            if not target.is_file():
                return m.group(0)
        except OSError:
            return m.group(0)
        text = str(target)
        return f'"{text}"' if " " in text else text

    return _HOOKS_TOKEN.sub(swap, run)


def payload(event: str, project_root, tool_name: str = "",
            tool_input: dict | None = None) -> dict:
    """What the hook is told about the thing it fired on.

    Without a payload a hook can only be a fixed command: it knows an event happened and
    nothing else, so the content-aware case — check the file that was just written —
    could not be written at all (E37). Shaped after Claude Code's own hook input so a
    script written for one is close to running under the other.

    The tool RESULT is deliberately absent: it can be a whole file, and a hook's own
    output is already held to a tighter cap than a tool result for the same reason.
    """
    root = Path(project_root)
    data = {"event": event, "project": root.name, "project_dir": str(root),
            "hooks_dir": str(hooks_dir())}
    if tool_name:
        data["tool_name"] = tool_name
        data["tool_input"] = tool_input if isinstance(tool_input, dict) else {}
    return data


def _environment(data: dict) -> dict:
    """The same payload for a hook that would rather not parse JSON.

    A one-line shell hook is the common case and `%LUBAN_TOOL_INPUT%` is reachable from
    one; a `python -c "import json,sys; ..."` prelude to read stdin is not.
    """
    env = {"LUBAN_EVENT": data["event"], "LUBAN_PROJECT": data["project"],
           "LUBAN_PROJECT_DIR": data["project_dir"],
           "LUBAN_HOOKS_DIR": data["hooks_dir"]}
    if "tool_name" in data:
        env["LUBAN_TOOL_NAME"] = data["tool_name"]
        env["LUBAN_TOOL_INPUT"] = json.dumps(data["tool_input"], ensure_ascii=False)
        # The single most-wanted field, lifted out so a guard on the file just written
        # is a one-liner rather than a JSON parse.
        target = data["tool_input"].get("path") or data["tool_input"].get("file_path")
        if isinstance(target, str):
            env["LUBAN_TOOL_PATH"] = target
    return env


def _run_one(hook: Hook, project_root, notify, data: dict | None = None) -> tuple[str, bool]:
    """(output, ok). Never raises: a broken hook must not kill the session."""
    # Imported here, not at module scope: tools.py fires post_tool_use hooks, so a
    # top-level import in both directions would be a cycle. The spawn itself is shared
    # rather than repeated — the UTF-8 decoding and process-group setup are the same
    # problem for a hook as for any other child, and two copies drift.
    from luban import tools as tools_mod

    data = data or payload(hook.event, project_root)
    try:
        # stderr merged: a hook's output is one diagnostic stream, not data to parse.
        proc = tools_mod._spawn(resolve_run(hook.run), project_root, merge_stderr=True,
                                stdin_pipe=True, env=_environment(data))
    except Exception as exc:  # a command that cannot even start
        if notify is not None:
            notify(f"hook {hook.event} could not start: {exc}")
        return f"[hook {hook.event} could not start: {exc}]", False
    try:
        body, _ = proc.communicate(input=json.dumps(data, ensure_ascii=False),
                                   timeout=hook.timeout)
    except subprocess.TimeoutExpired:
        tools_mod._kill_tree(proc)
        try:
            body, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            body = ""
        msg = f"hook {hook.event} timed out after {hook.timeout}s (process tree killed)"
        if notify is not None:
            notify(msg)
        return f"{body or ''}\n[{msg}]", False
    body = body or ""
    if proc.returncode != 0:
        # The output is still worth having — a failing check is exactly the output you
        # want. But nobody may believe it succeeded.
        msg = f"hook {hook.event} failed with exit code {proc.returncode}"
        if notify is not None:
            notify(f"{msg}: {hook.run}")
        return f"{body}\n[{msg}]", False
    return body, True


def run_hooks(hooks: list, event: str, project_root, tool_name: str = "",
              decide=None, audit=None, notify=None, tool_input: dict | None = None) -> str:
    """Fire every hook for this event; return the text to inject ("" for none).

    `decide` is the permission layer: declaring a hook in your own config is the consent
    to run it, so there is no prompt — a hook that asks on every turn is a hook you
    delete — but a deny rule still refuses, because deny can only ever subtract.
    """
    firing = for_event(hooks, event, tool_name, project_root)
    if not firing:
        return ""
    data = payload(event, project_root, tool_name, tool_input)
    parts = []
    for hook in firing:
        decision = decide(hook.run) if decide is not None else None
        if decision is not None and getattr(decision, "action", "") == "deny":
            reason = getattr(decision, "reason", "denied by rule")
            if notify is not None:
                notify(f"hook {event} blocked: {reason}")
            _audit(audit, event, hook, "deny_rule", None)
            continue
        body, ok = _run_one(hook, project_root, notify, data)
        _audit(audit, event, hook, "", ok)
        if hook.inject and body.strip():
            parts.append(body.strip())
    if not parts:
        return ""
    return wrap(event, _truncate("\n\n".join(parts)))


def _audit(audit, event: str, hook: Hook, decision: str, ok) -> None:
    if audit is None:
        return
    try:
        audit({
            "tool": f"hook:{event}",
            "target": hook.run,
            "decision": decision,
            "is_error": ok is False,
        })
    except Exception:
        pass  # auditing is a side channel — it must never raise into the loop
