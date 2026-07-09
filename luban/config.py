"""User-editable config at ~/.luban/config.toml.

Read with the stdlib `tomllib` (no dependency). On first run the file is
created with the auto-detected platform; users can edit it afterwards. Built
to grow — more keys (model, auto, stream) can be added later.
"""
from __future__ import annotations

import platform as _platform
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from luban import paths

CONFIG_DIR = paths.luban_home()
CONFIG_PATH = CONFIG_DIR / "config.toml"

_VALID_PLATFORMS = {"windows", "mac", "linux"}


@dataclass
class Config:
    platform: str
    model: str = ""  # default model id; "" = built-in default (--model flag wins)
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    memory_file: str = ""  # "" = try LUBAN.md, CLAUDE.md, AGENTS.md in order
    memory_enabled: bool = True  # long-term memory (SOUL.md, remember/recall, journal)
    thinking: bool = True  # request adaptive extended thinking (on for capable models)
    effort: str = "medium"  # low | medium | high | xhigh | max
    thinking_verbose: bool = False  # stream the reasoning text; default silent
    allow_out_of_tree_file_edits: bool = False  # let file tools touch paths outside the project
    web_search: bool = False  # offer the model the API's server-side web search tool
    web_search_tool_type: str = "web_search_20250305"  # server-tool type version string
    subagents: bool = False  # offer the spawn_subagent tool (nested read-only agent)


def detect_platform() -> str:
    """Map platform.system() to luban's short platform names."""
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        _platform.system(), _platform.system().lower()
    )


def _default_text(plat: str) -> str:
    return (
        "# ~/.luban/config.toml — luban settings (edit me)\n"
        "# To move this whole ~/.luban folder (e.g. to a OneDrive folder synced\n"
        "# across devices), set the LUBAN_HOME environment variable — not a key\n"
        "# here (this file lives inside the folder it would relocate). Quickest:\n"
        "#   luban --set-home <path>\n"
        f'platform = "{plat}"   # windows | mac | linux\n'
        "\n"
        "# Default model (the --model flag wins). Uncomment to override the built-in:\n"
        '# model = "your-model-id"\n'
        "\n"
        "# Project memory file (default: first of LUBAN.md, CLAUDE.md, AGENTS.md):\n"
        '# memory_file = "CLAUDE.md"\n'
        "\n"
        "# Long-term memory (SOUL.md, remember/recall tools, journal). Default on:\n"
        "# memory_enabled = true\n"
        "\n"
        "# Extended thinking. On by default for capable models; effort is one of\n"
        "# low | medium | high | xhigh | max (medium balances speed and depth; use\n"
        "# xhigh for the hardest coding/agentic work). Thinking is SILENT by default;\n"
        "# thinking_verbose = true streams the reasoning as grey text. Change any of\n"
        "# these per-session with /thinking, /effort, /verbose, or set defaults here:\n"
        "# thinking = true\n"
        '# effort = "medium"\n'
        "# thinking_verbose = false\n"
        "\n"
        "# Let the file tools read/write paths OUTSIDE this project (e.g. a sibling\n"
        "# repo), via the same diff-and-confirm as run_command. Default off for\n"
        "# corporate safety; under --auto these edits auto-approve like any other:\n"
        "# allow_out_of_tree_file_edits = false\n"
        "\n"
        "# Offer the API's server-side web search tool (if your client/model supports\n"
        "# it). Default off. Set the tool-type to match your backend — newer models\n"
        "# use web_search_20260209; the default basic variant is broadly available:\n"
        "# web_search = false\n"
        '# web_search_tool_type = "web_search_20250305"\n'
        "\n"
        "# Offer the spawn_subagent tool: the model can run a fresh read-only sub-agent\n"
        "# on a focused subtask. Default off (each sub-run costs extra model calls):\n"
        "# subagents = false\n"
        "\n"
        "# Optional permission rules (deny > allow > ask; deny works even in --auto):\n"
        "# [permissions]\n"
        '# allow = ["run_command:python *"]\n'
        '# deny  = ["run_command:del *"]\n'
    )


def write_default(path: Path = CONFIG_PATH) -> str:
    """Create the config file with the detected platform. Returns the platform."""
    plat = detect_platform()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_default_text(plat), encoding="utf-8")
    return plat


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load config, auto-creating it on first run. Never raises on a bad file."""
    if not path.exists():
        return Config(platform=write_default(path))
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, OSError):
        data = {}
    plat = data.get("platform") or detect_platform()
    if plat not in _VALID_PLATFORMS:
        plat = detect_platform()
    model = data.get("model")
    if not isinstance(model, str):
        model = ""
    perms = data.get("permissions")
    if not isinstance(perms, dict):
        perms = {}

    def _rules(key: str) -> list[str]:
        raw = perms.get(key)
        if not isinstance(raw, list):
            return []
        return [r for r in raw if isinstance(r, str)]

    allow = _rules("allow")
    deny = _rules("deny")
    memory_file = data.get("memory_file")
    if not isinstance(memory_file, str):
        memory_file = ""
    memory_enabled = data.get("memory_enabled")
    if not isinstance(memory_enabled, bool):
        memory_enabled = True
    thinking = data.get("thinking")
    if not isinstance(thinking, bool):
        thinking = True
    effort = data.get("effort")
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        effort = "medium"
    thinking_verbose = data.get("thinking_verbose")
    if not isinstance(thinking_verbose, bool):
        thinking_verbose = False
    allow_out_of_tree = data.get("allow_out_of_tree_file_edits")
    if not isinstance(allow_out_of_tree, bool):
        allow_out_of_tree = False
    web_search = data.get("web_search")
    if not isinstance(web_search, bool):
        web_search = False
    web_search_type = data.get("web_search_tool_type")
    if not isinstance(web_search_type, str) or not web_search_type:
        web_search_type = "web_search_20250305"
    subagents = data.get("subagents")
    if not isinstance(subagents, bool):
        subagents = False
    return Config(
        platform=plat,
        model=model,
        allow=allow,
        deny=deny,
        memory_file=memory_file,
        memory_enabled=memory_enabled,
        thinking=thinking,
        effort=effort,
        thinking_verbose=thinking_verbose,
        allow_out_of_tree_file_edits=allow_out_of_tree,
        web_search=web_search,
        web_search_tool_type=web_search_type,
        subagents=subagents,
    )
