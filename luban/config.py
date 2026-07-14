"""User-editable config at ~/.luban/config.toml.

Read with the stdlib `tomllib` (no dependency). On first run the file is
created with the auto-detected platform; users can edit it afterwards. Built
to grow — more keys (model, auto, stream) can be added later.
"""
from __future__ import annotations

import platform as _platform
import re
import sys
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
    # The ceiling on ONE turn's whole output: thinking + text + the tool_use input.
    # The old fixed 8192 was set before thinking existed — raising `effort` grows the
    # thinking allocation but the ceiling never moved with it, so reasoning starved the
    # tool call and writes were cut off mid-flight (E24). Streaming makes a large
    # ceiling safe; a non-streamed request holds the connection open with nothing
    # flowing, so it gets clamped (see NO_STREAM_MAX_TOKENS).
    max_tokens: int = 32_000
    thinking_verbose: bool = False  # stream the reasoning text; default silent
    auto_continue: bool = False  # reopen the folder's last session on a plain start
    # When to nudge "consider /compact". The old 60k default was set for a much
    # smaller context window and cried wolf constantly on a 1M-token model.
    warn_tokens: int = 150_000
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
        "# Ceiling on ONE turn's whole output: thinking + text + the tool call itself.\n"
        "# Raising `effort` grows the thinking allocation but NOT this ceiling, so if it\n"
        "# is too low, reasoning starves the tool call and a write gets cut off. Raise it\n"
        "# if you run high/xhigh effort or ask for large writes. Non-streamed runs\n"
        "# (--no-stream) are clamped lower — an idle connection times out.\n"
        "# max_tokens = 32000\n"
        "\n"
        "# Reopen this folder's last session automatically on a plain `luban` start\n"
        "# (instead of just reminding you it exists). Default off:\n"
        "# auto_continue = false\n"
        "\n"
        "# Nudge you to /compact once the conversation passes this many tokens.\n"
        "# Raise it if you have a large context window and don't want the reminder:\n"
        "# warn_tokens = 150000\n"
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


# Config keys a fresh install documents. Used by sync_config to append any that a
# pre-existing config.toml predates — so shipped-but-gated features stay
# discoverable instead of silently missing from a stale file (E19).
_MIGRATABLE = [
    ("model", '# model = "your-model-id"\n'),
    ("memory_file", '# memory_file = "CLAUDE.md"\n'),
    ("memory_enabled", "# memory_enabled = true\n"),
    ("thinking", "# thinking = true\n"),
    ("effort", '# effort = "medium"   # low | medium | high | xhigh | max\n'),
    ("thinking_verbose", "# thinking_verbose = false   # stream the reasoning text\n"),
    ("max_tokens", "# max_tokens = 32000   # ceiling on ONE turn: thinking + text + tool call\n"),
    ("auto_continue", "# auto_continue = false   # reopen the last session on plain start\n"),
    ("warn_tokens", "# warn_tokens = 150000   # when to nudge you to /compact\n"),
    ("allow_out_of_tree_file_edits", "# allow_out_of_tree_file_edits = false\n"),
    ("web_search", "# web_search = false\n"),
    ("web_search_tool_type", '# web_search_tool_type = "web_search_20250305"\n'),
    ("subagents", "# subagents = false\n"),
]


_TOP_LEVEL_KEYS = {"platform", *(k for k, _ in _MIGRATABLE)}
# A real table header. A commented-out `# [permissions]` is not one.
_TABLE_HEADER = re.compile(r"^\s*\[")


def _top_level_end(lines: list[str]) -> int:
    """Index of the first real table header — the end of the top-level region.

    Everything after a `[table]` header belongs to that table until the next one.
    Top-level keys MUST be written above it or TOML silently reparents them.
    """
    for i, line in enumerate(lines):
        if _TABLE_HEADER.match(line):
            return i
    return len(lines)


def missing_keys(path: Path = CONFIG_PATH) -> list[str]:
    """Config keys the current luban knows about that aren't in the file yet.

    Only the TOP-LEVEL region counts: a `effort = "xhigh"` sitting under
    `[permissions]` is not a top-level effort setting, it's `permissions.effort`,
    which nothing reads. Scanning the whole file (as this used to) reported such a
    key as present, so --sync-config saw nothing to do and the user was stuck.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines(keepends=True)
    head = "".join(lines[: _top_level_end(lines)])
    return [k for k, _ in _MIGRATABLE
            if not re.search(rf"^\s*#?\s*{re.escape(k)}\s*=", head, re.MULTILINE)]


def misplaced_keys(path: Path = CONFIG_PATH) -> list[tuple[str, str]]:
    """Top-level settings that TOML has swallowed into a table — (key, table).

    This is what a bare `effort = "xhigh"` appended below `[permissions]` becomes:
    a live, syntactically valid, completely ignored `permissions.effort`.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, OSError):
        return []
    found: list[tuple[str, str]] = []
    for table, body in data.items():
        if not isinstance(body, dict):
            continue
        found.extend((key, table) for key in body if key in _TOP_LEVEL_KEYS)
    return found


def config_warnings(path: Path = CONFIG_PATH) -> list[str]:
    """Told to the HUMAN at startup: settings that are in the file but ignored."""
    bad = misplaced_keys(path)
    if not bad:
        return []
    listing = ", ".join(f"{k} (under [{t}])" for k, t in sorted(bad))
    return [
        f"warning: {len(bad)} setting(s) in your config.toml are being IGNORED — "
        f"{listing}. A [table] header captures every key below it, so these became "
        f"table entries, not settings. Run `luban --sync-config` to move them back "
        f"to the top of the file."
    ]


_SYNC_BANNER = re.compile(r"^#\s*---\s*settings added by luban --sync-config")


def _repair_misplaced(lines: list[str]) -> tuple[list[str], list[str]]:
    """Lift swallowed top-level keys back above the first table header."""
    start = _top_level_end(lines)
    if start >= len(lines):
        return lines, []
    keep: list[str] = []
    lifted: list[str] = []
    names: list[str] = []
    for line in lines[start:]:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in _TOP_LEVEL_KEYS:
            lifted.append(line if line.endswith("\n") else line + "\n")
            names.append(m.group(1))
        elif not _SYNC_BANNER.match(line):
            keep.append(line)  # our own banner would be left orphaned; drop it
    if not lifted:
        return lines, []
    banner = "# --- moved back to the top level by luban --sync-config ---\n"
    # (they were under a [table] header, where nothing reads them)
    body = lines[:start]
    while body and not body[-1].strip():  # collapse the gap we're inserting into
        body.pop()
    return body + ["\n", banner, *lifted, "\n"] + keep, names


def sync_config(path: Path = CONFIG_PATH) -> list[str]:
    """Bring a pre-existing config.toml up to date. Returns the keys touched.

    Two jobs, both purely mechanical — no value is ever changed:
      1. REPAIR: lift any top-level setting that a [table] header has swallowed
         back above that header, where it's actually read.
      2. ADD: append the keys this luban knows about that the file predates, as
         COMMENTED lines — inserted in the TOP-LEVEL region, never at EOF, which
         is what caused (1) in the first place.
    """
    from luban import __version__

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return []
    lines, repaired = _repair_misplaced(lines)
    # Recompute against the REPAIRED text, so a key we just lifted isn't also
    # re-added as a commented default below it.
    head = "".join(lines[: _top_level_end(lines)])
    missing = [k for k, _ in _MIGRATABLE
               if not re.search(rf"^\s*#?\s*{re.escape(k)}\s*=", head, re.MULTILINE)]
    added: list[str] = []
    if missing:
        blocks = dict(_MIGRATABLE)
        block = ([f"\n# --- settings added by luban --sync-config (v{__version__}) ---\n"]
                 + [blocks[k] for k in missing])
        at = _top_level_end(lines)  # above the first table, not at EOF
        lines = lines[:at] + block + (["\n"] if at < len(lines) else []) + lines[at:]
        added = missing
    if not repaired and not added:
        return []
    try:
        path.write_text("".join(lines), encoding="utf-8")
    except OSError:
        return []
    return sorted(set(repaired) | set(added))


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load config, auto-creating it on first run. Never raises on a bad file."""
    if not path.exists():
        return Config(platform=write_default(path))
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        # Don't die — but don't pretend either. Silently returning defaults for a
        # file the user is actively editing is how a setting goes missing for weeks.
        print(f"warning: config.toml could not be read ({exc}) — using defaults "
              f"for EVERY setting. Fix the file at {path}.", file=sys.stderr)
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
    max_tokens = data.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        max_tokens = 32_000
    thinking_verbose = data.get("thinking_verbose")
    if not isinstance(thinking_verbose, bool):
        thinking_verbose = False
    auto_continue = data.get("auto_continue")
    if not isinstance(auto_continue, bool):
        auto_continue = False
    warn_tokens = data.get("warn_tokens")
    if not isinstance(warn_tokens, int) or isinstance(warn_tokens, bool) or warn_tokens <= 0:
        warn_tokens = 150_000
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
        max_tokens=max_tokens,
        thinking_verbose=thinking_verbose,
        auto_continue=auto_continue,
        warn_tokens=warn_tokens,
        allow_out_of_tree_file_edits=allow_out_of_tree,
        web_search=web_search,
        web_search_tool_type=web_search_type,
        subagents=subagents,
    )
