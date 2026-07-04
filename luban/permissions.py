"""Permission rules — declarative allow/deny evaluated before any confirmation.

Rules live in ~/.luban/config.toml under [permissions] (user-owned on purpose:
a cloned project repo must never be able to grant itself permissions):

    [permissions]
    allow = ["run_command:python *", "run_command:git status*"]
    deny  = ["run_command:del *", "write_file:*.env"]

A rule is "<tool>" (matches every call of that tool) or "<tool>:<pattern>"
(fnmatch pattern tested against the call's target — the command for
run_command, the path for file tools). Precedence: deny > allow > default
(read-only tools default to allow, mutating tools to ask). Deny applies even
in --auto mode. Malformed rules simply never match; they cannot crash.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

# Which input key a rule pattern is matched against, per tool.
_TARGET_KEY = {
    "run_command": "command",
    "write_file": "path",
    "edit_file": "path",
    "read_file": "path",
    "list_dir": "path",
    "grep": "path",
    "glob": "pattern",
    "load_skill": "name",
    "remember": "name",
    "forget": "name",
    "recall": "query",
    "journal": "text",
}


@dataclass
class Decision:
    action: str  # "deny" | "allow" | "ask"
    reason: str = ""


def target_of(tool_name: str, tool_input: dict) -> str:
    return str(tool_input.get(_TARGET_KEY.get(tool_name, ""), ""))


def _matches(rule: str, tool_name: str, target: str) -> bool:
    tool, sep, pattern = rule.partition(":")
    if tool.strip() != tool_name:
        return False
    if not sep:
        return True  # bare "<tool>" rule matches every call of that tool
    return fnmatch(target, pattern.strip())


def evaluate(
    tool_name: str,
    tool_input: dict,
    allow: list[str],
    deny: list[str],
    read_only: bool,
) -> Decision:
    target = target_of(tool_name, tool_input)
    for rule in deny:
        if _matches(rule, tool_name, target):
            return Decision("deny", f"blocked by deny rule: {rule}")
    for rule in allow:
        if _matches(rule, tool_name, target):
            return Decision("allow", f"allowed by rule: {rule}")
    return Decision("allow" if read_only else "ask")
