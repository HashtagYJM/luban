"""User-owned custom tools loaded from tools_local.py.

Resolution: LUBAN_TOOLS_LOCAL env var -> ~/.luban/tools_local.py -> none.
User-owned locations ONLY, never the project directory: a cloned repo must
not be able to inject executable tools (same stance as permissions and
client_local). Any failure here degrades to "no custom tools" with a
one-line warning — it must never stop luban from starting.

The module contract (documented in README):

    TOOLS = [
        {
            "name": "query_sql",                # ^[a-zA-Z0-9_-]{1,64}$
            "description": "Run read-only SQL", # non-empty str
            "input_schema": {...},              # dict, Anthropic tool shape
            "handler": my_func,                 # callable(inp: dict, project_root: Path) -> str
            "read_only": True,                  # optional, default False
            "permission_target": "sql",         # optional input key for "<tool>:<pattern>" rules
        },
    ]
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

from luban import paths

DEFAULT_PATH = paths.luban_home() / "tools_local.py"  # call-time resolution

_NAME_RX = re.compile(r"^[a-zA-Z0-9_-]{1,64}\Z")
_REQUIRED = ("name", "description", "input_schema", "handler")


def _tools_path() -> Path | None:
    env = os.environ.get("LUBAN_TOOLS_LOCAL")
    if env:
        return Path(env)
    default = DEFAULT_PATH
    return default if default.is_file() else None


def _valid(entry: object, index: int) -> bool:
    def warn(why: str) -> None:
        print(f"warning: skipping custom tool #{index}: {why}", file=sys.stderr)

    if not isinstance(entry, dict):
        warn("not a dict")
        return False
    for key in _REQUIRED:
        if key not in entry:
            warn(f"missing {key!r}")
            return False
    if not isinstance(entry["name"], str) or not _NAME_RX.match(entry["name"]):
        warn(f"bad name {entry.get('name')!r} (want ^[a-zA-Z0-9_-]{{1,64}}$)")
        return False
    if not isinstance(entry["description"], str) or not entry["description"].strip():
        warn("description must be a non-empty string")
        return False
    if not isinstance(entry["input_schema"], dict):
        warn("input_schema must be a dict")
        return False
    if not callable(entry["handler"]):
        warn("handler must be callable")
        return False
    return True


def load_custom_tools() -> list[dict]:
    path = _tools_path()
    if path is None:
        return []
    if not path.is_file():
        print(f"warning: custom tools file not found: {path}", file=sys.stderr)
        return []
    try:
        spec = importlib.util.spec_from_file_location("luban_tools_local", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = module.TOOLS
        if not isinstance(raw, list):
            raise TypeError("TOOLS must be a list")
    except Exception as exc:
        print(f"warning: could not load custom tools from {path}: {exc}", file=sys.stderr)
        return []
    return [entry for i, entry in enumerate(raw) if _valid(entry, i)]
