from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from luban import __version__, agent, config as config_mod, paths, tools, ui
from luban import audit as audit_mod
from luban import changelog
from luban import client as client_mod
from luban import custom_tools as custom_tools_mod
from luban import memory as memory_mod
from luban import permissions as permissions_mod
from luban import sessions as sessions_mod
from luban import skills as skills_mod


COMPACT_PROMPT = (
    "Summarize this conversation comprehensively so a fresh session can continue "
    "the work: key decisions and rationale, files created or changed and why, the "
    "current state, and open items or next steps. Reply with only the summary."
)
FLUSH_PROMPT = (
    "Before this conversation is compacted, write a short journal entry with the "
    "journal tool: 2-4 lines on what happened, what was decided, and what's next. "
    "Then reply with just: saved."
)
REFLECT_PROMPT = (
    "Housekeeping for your long-term memory. Review the memory index and recent "
    "journal in your context: use recall to inspect facts, promote durable items "
    "from the journal into facts with remember, and merge or forget stale, "
    "duplicate, or wrong facts. Then report briefly what you changed."
)
# Kept for reference; the live threshold is config.warn_tokens (default 150k).
DEFAULT_WARN_TOKENS = 150_000
# First match wins; a `memory_file` key in config.toml overrides the chain.
MEMORY_FILES = ("LUBAN.md", "CLAUDE.md", "AGENTS.md")
MEMORY_MAX_CHARS = 8000


@dataclass
class Session:
    model: str
    max_tokens: int
    auto: bool
    stream: bool
    messages: list = field(default_factory=list)
    project: str = ""
    session_id: str = ""
    created: str = ""
    title: str = ""
    pending_context: list = field(default_factory=list)
    journaled: bool = False
    thinking: bool = True
    effort: str = "medium"
    thinking_verbose: bool = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="luban", description="Terminal coding agent.")
    p.add_argument("--dir", default=".", help="Project root (default: cwd).")
    p.add_argument("--model", default=None)
    p.add_argument("--max-tokens", type=int, default=8192)
    p.add_argument("--auto", action="store_true", help="Skip confirmation prompts (auto-approves ALL file writes and shell commands — use with care).")
    p.add_argument("--no-stream", dest="stream", action="store_false")
    p.add_argument("--version", action="version", version=f"luban {__version__}")
    p.add_argument("--set-home", metavar="PATH", default=None,
                   help="Persist LUBAN_HOME to PATH (e.g. a OneDrive folder to "
                        "sync memory across devices) and exit.")
    p.add_argument("--sync-config", action="store_true",
                   help="Append any new config keys (commented) to config.toml, "
                        "preserving your values, and exit.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--continue", "-c", dest="cont", action="store_true",
                   help="Resume the most recent session for this folder.")
    g.add_argument("--resume", "-r", action="store_true",
                   help="Pick a past session to resume.")
    p.add_argument("--all", action="store_true",
                   help="With --resume: list sessions from all folders.")
    p.set_defaults(stream=True)
    return p.parse_args(argv)


def _final_text(messages: list[dict]) -> str:
    """The last assistant message's text (for a sub-agent's return value)."""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if any(parts):
                return "\n".join(p for p in parts if p)
    return "(sub-agent produced no text)"


def build_tool_context(
    session: Session, project_root: Path, cfg: config_mod.Config | None = None,
    client=None,
) -> tools.ToolContext:
    def confirm(prompt: str) -> bool:
        if session.auto:
            return True
        decision = ui.ask_confirm(prompt)
        if decision == "all":
            session.auto = True
            return True
        return decision == "yes"

    decide = None
    audit_cb = None
    if cfg is not None:
        def decide(tool_name: str, tool_input: dict) -> permissions_mod.Decision:
            return permissions_mod.evaluate(
                tool_name, tool_input, cfg.allow, cfg.deny,
                read_only=tool_name in tools.READ_ONLY_TOOLS,
            )

        def audit_cb(entry: dict) -> None:
            audit_mod.log({"project": session.project, **entry})

    subagent = None
    if client is not None and cfg is not None and cfg.subagents:
        def subagent(task: str) -> str:
            # Nested agent: read-only tools only (no writes/run_command → no confirm
            # prompts and no unattended mutations), no memory, no further nesting.
            read_only = [t for t in tools.active_tools(False)
                         if t["name"] in tools.READ_ONLY_TOOLS]
            sub_cfg = agent.AgentConfig(
                session.model, session.max_tokens, stream=False, platform=cfg.platform,
                tools=read_only,
            )
            sub_ctx = tools.ToolContext(
                project_root=Path(project_root),
                confirm=lambda p: False,  # writes aren't offered; deny defensively
                render_diff=lambda p, o, n: None,
                render_command=lambda c: None,
                decide=decide, audit=audit_cb,
            )
            msgs = agent.run_turn(
                client, sub_cfg, [{"role": "user", "content": task}], sub_ctx, lambda t: None
            )
            return _final_text(msgs)

    return tools.ToolContext(
        project_root=Path(project_root),
        confirm=confirm,
        render_diff=ui.render_diff,
        render_command=ui.render_command,
        decide=decide,
        audit=audit_cb,
        allow_out_of_tree=cfg.allow_out_of_tree_file_edits if cfg is not None else False,
        subagent=subagent,
    )


def save_session(session: Session) -> None:
    if not session.messages:
        return
    if not session.session_id:
        session.session_id = sessions_mod.new_session_id()
        session.created = datetime.now().isoformat(timespec="seconds")
    if not session.title:
        first = next(
            (m["content"] for m in session.messages
             if m["role"] == "user" and isinstance(m["content"], str)),
            "",
        )
        session.title = first[:60]
    try:
        sessions_mod.save({
            "id": session.session_id,
            "project": session.project,
            "created": session.created,
            "model": session.model,
            "title": session.title,
            # never persist a history that ends in an unanswered tool_use (E14)
            "messages": agent.sanitize_history(session.messages),
        })
    except OSError as exc:
        ui.print_text(f"warning: could not save session ({exc})\n")


def compose_user_message(session: Session, line: str) -> str:
    if not session.pending_context:
        return line
    parts = session.pending_context + [line]
    session.pending_context.clear()
    return "\n\n".join(parts)


def read_project_memory(project_root: Path, memory_file: str = "") -> str:
    # An explicit memory_file (from config.toml) is authoritative: no chain fallback.
    names = (memory_file,) if memory_file else MEMORY_FILES
    for name in names:
        path = Path(project_root) / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if len(text) > MEMORY_MAX_CHARS:
            text = text[:MEMORY_MAX_CHARS] + "\n[memory file truncated]"
        return text
    return ""


def always_on_usage(project_root: Path, cfg: config_mod.Config) -> list[tuple[str, int, int]]:
    """(label, chars, cap) for everything injected into EVERY turn's system prompt —
    the memory files plus this project's memory file. Used to tell the HUMAN when
    content is being dropped (the truncation marker only ever reached the model)."""
    usage = list(memory_mod.always_on_usage()) if cfg.memory_enabled else []
    names = (cfg.memory_file,) if cfg.memory_file else MEMORY_FILES
    for name in names:
        path = Path(project_root) / name
        try:
            size = len(path.read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            continue
        usage.append((name, size, MEMORY_MAX_CHARS))
        break  # first match only, same as read_project_memory
    return usage


def setup_custom_tools() -> list[str]:
    """Load user-owned tools_local.py and merge its tools into the toolbox."""
    specs = custom_tools_mod.load_custom_tools()
    return tools.register_custom(specs) if specs else []


def resolve_model(flag_model: str | None, cfg: config_mod.Config) -> str:
    return flag_model or cfg.model or client_mod.DEFAULT_MODEL


def detect_upgrade() -> tuple[str | None, str]:
    """Compare the installed version against the last one luban ran as.

    Returns (previous_version_or_None, current). None means first run ever (or no
    recorded version) — callers suppress the banner then. The `.last-version`
    dotfile lives at the luban-home ROOT (not under memory/) so upgrade detection
    works even with memory disabled; a legacy dotfile under memory/ is honored
    once for a seamless transition. Never raises.
    """
    home = paths.luban_home()
    state = home / ".last-version"
    legacy = memory_mod.MEMORY_DIR / ".last-version"
    prev: str | None = None
    try:
        prev = state.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        prev = None
    if prev is None:  # migrate from the old memory/ location if present
        try:
            prev = legacy.read_text(encoding="utf-8", errors="replace").strip() or None
        except OSError:
            prev = None
    try:
        home.mkdir(parents=True, exist_ok=True)
        state.write_text(__version__, encoding="utf-8")
    except OSError:
        pass
    return prev, __version__


def upgrade_banner(prev: str, section: str) -> str:
    """Deterministic 'what's new' text from the bundled changelog (no model call,
    not gated on memory — every colleague sees it)."""
    header = f"[luban upgraded {prev} → {__version__}] What's new:"
    if section:
        return f"{header}\n{section}"
    return f"{header}\n  (see the release notes for details)"


def reconcile_directive(prev: str, section: str) -> str:
    """Injected into the next turn so luban reconciles the enhancement tracker
    against the release — using local changelog data, no network. `section` is the
    CUMULATIVE span of every release since the user's last-seen version (E17), so a
    multi-version jump doesn't miss intermediate fixes."""
    notes = section or "(no bundled notes for these versions)"
    return (
        f"luban was just upgraded from {prev} to {__version__}. Here is EVERY "
        f"release's notes across that span:\n{notes}\n\n"
        "Reconcile the Open items in ~/.luban/memory/enhancements.md against these "
        "changes, and verify — don't just text-match:\n"
        "- For each Open row, empirically check whether the fix is actually present: "
        "prefer running the relevant tool/probe; for a prevention-class fix that "
        "can't be triggered in-session, inspect the installed package code to confirm "
        "the guard exists.\n"
        "- Move a row to Resolved only with evidence; note the version it was fixed "
        "in and a one-line note of how you verified it. If a prevention fix can't be "
        "re-triggered, mark it Resolved with 're-open on recurrence'.\n"
        "- Don't infer status from stale artifacts (e.g. an old config.toml).\n"
        "Then briefly tell me what you moved and how you checked."
    )


def home_notice() -> str:
    """One line when $LUBAN_HOME has relocated the home dir, plus a warning if a
    legacy ~/.luban still holds data that is now being ignored. Never raises.

    Enforces the single-source-of-truth: you can see which home is active, and
    you're told if a second copy exists so you never silently run against two.
    """
    if not os.environ.get("LUBAN_HOME"):
        return ""
    active = paths.luban_home()
    default = (Path.home() / ".luban").resolve()
    if active == default:
        return ""
    lines = [f"[luban home: {active}]"]
    try:
        soul = default / "SOUL.md"
        has_data = (default / "memory").is_dir() or (
            soul.is_file()
            and soul.read_text(encoding="utf-8", errors="replace").strip() != ""
        )
    except OSError:
        has_data = False
    if has_data:
        lines.append(
            f"note: a legacy {default} with data exists and is being ignored — "
            "move or delete it to avoid confusion."
        )
    return "\n".join(lines)


def set_home(path: str) -> None:
    """Persist LUBAN_HOME for future runs and create the target directory.

    On Windows this runs `setx` (a new terminal is needed to pick it up); on
    POSIX it prints the export line to add to your shell profile (editing
    profiles automatically is too fragile to do for you).
    """
    target = Path(path).expanduser().resolve()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        ui.print_text(f"could not create {target}: {exc}\n")
        return
    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["setx", "LUBAN_HOME", str(target)], check=True, capture_output=True
            )
            ui.print_text(
                f"✓ LUBAN_HOME set to {target}.\n"
                "Open a NEW terminal for it to take effect. Point every device at "
                "the same synced folder to share memory across them.\n"
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            ui.print_text(f"could not set LUBAN_HOME: {exc}\n")
    else:
        ui.print_text(
            f"✓ created {target}. Add this to your shell profile to make it stick:\n"
            f'  export LUBAN_HOME="{target}"\n'
        )


def build_agent_config(session: Session, cfg: config_mod.Config, project_root: Path) -> agent.AgentConfig:
    tool_list = tools.active_tools(cfg.memory_enabled)
    if cfg.subagents:
        tool_list = [*tool_list, tools.SUBAGENT_TOOL]
    return agent.AgentConfig(
        session.model, session.max_tokens, session.stream, platform=cfg.platform,
        skills=skills_mod.list_skills(str(project_root)),
        memory=read_project_memory(project_root, cfg.memory_file),
        global_memory=memory_mod.bootstrap_block() if cfg.memory_enabled else "",
        tools=tool_list,
        web_search=cfg.web_search,
        web_search_tool_type=cfg.web_search_tool_type,
        thinking=session.thinking,
        effort=session.effort,
        thinking_verbose=session.thinking_verbose,
    )


def flush_memory(session: Session, client, ctx, cfg: config_mod.Config) -> None:
    """Best-effort: capture a journal entry before compaction destroys context.

    Structural guarantee: the flush turn is offered ONLY the journal tool, so it
    cannot write facts (remember/forget) — session narrative belongs in the
    journal and the compact summary, never in the permanent fact store. Runs at
    most once per session; exit_journal is the fallback when it never runs.
    """
    if not cfg.memory_enabled or not session.messages or session.journaled:
        return
    ui.print_text("(memory flush…)\n")
    journal_only = [t for t in tools.active_tools(True) if t["name"] == "journal"]
    msgs = session.messages + [{"role": "user", "content": FLUSH_PROMPT}]
    config = agent.AgentConfig(
        session.model, session.max_tokens, stream=False, platform=cfg.platform,
        global_memory=memory_mod.bootstrap_block(), tools=journal_only,
    )
    before = memory_mod._journal_writes
    try:
        agent.run_turn(client, config, msgs, ctx, lambda t: None)
    except Exception as exc:
        ui.print_text(f"(memory flush skipped: {exc})\n")
        return
    if memory_mod._journal_writes > before:
        session.journaled = True  # a journal entry was actually written this segment


def reflect_session(session: Session, client, ctx, cfg: config_mod.Config) -> None:
    """Isolated consolidation turn; the live conversation is never touched."""
    if not cfg.memory_enabled:
        ui.print_text("memory is disabled (memory_enabled = false in config.toml).\n")
        return
    config = agent.AgentConfig(
        session.model, session.max_tokens, session.stream, platform=cfg.platform,
        global_memory=memory_mod.bootstrap_block(), tools=tools.active_tools(True),
    )
    ui.print_text("\nluban> ")
    try:
        agent.run_turn(client, config, [{"role": "user", "content": REFLECT_PROMPT}],
                       ctx, ui.print_text, ui.print_thinking)
    except Exception as exc:
        ui.print_text(f"reflect failed ({exc}) — memory unchanged.\n")
    ui.print_text("\n")


def exit_journal(session: Session, cfg: config_mod.Config, project_root: Path) -> None:
    if not cfg.memory_enabled or not session.messages or session.journaled:
        return
    memory_mod.journal_append(
        f"[{Path(project_root).name}] '{session.title or 'untitled'}' — "
        f"{len(session.messages)} messages ({session.model})"
    )


def _message_text(msg: dict) -> str:
    """The text a message actually contributes to the prompt."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text", "")))
        elif kind == "thinking":
            parts.append(str(block.get("thinking", "")))
        elif kind == "tool_use":
            # the tool name + its JSON arguments are real tokens on the wire
            parts.append(str(block.get("name", "")))
            parts.append(str(block.get("input", "")))
        elif kind == "tool_result":
            inner = block.get("content")
            parts.append(inner if isinstance(inner, str) else str(inner))
        else:
            parts.append(str(block.get("text") or ""))
    return "\n".join(p for p in parts if p)


def estimate_tokens(messages: list) -> int:
    """Rough token estimate (~4 chars/token) over the message TEXT.

    The old version stringified the whole message dict, so it counted Python
    punctuation and the 'role'/'content'/'type' keys as if they were prompt
    tokens — inflating the count and tripping the /compact nudge far too early.
    """
    return sum(len(_message_text(m)) for m in messages) // 4


def compact_session(session: Session, client, ctx=None, cfg=None) -> None:
    if not session.messages:
        ui.print_text("nothing to compact.\n")
        return
    if ctx is not None and cfg is not None:
        flush_memory(session, client, ctx, cfg)
    save_session(session)  # preserve the full transcript on disk first
    old_id = session.session_id
    old_title = session.title
    msgs = session.messages + [{"role": "user", "content": COMPACT_PROMPT}]
    try:
        msg = client_mod.create_turn(
            client, model=session.model, max_tokens=session.max_tokens,
            system=agent.SYSTEM_PROMPT, messages=msgs, tools=[],
        )
        summary = "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception as exc:
        ui.print_text(f"compact failed ({exc}) — session unchanged.\n")
        return
    if not summary:
        ui.print_text("compact failed (empty summary) — session unchanged.\n")
        return
    ui.print_text(f"\n{summary}\n\n")
    session.messages = [
        {"role": "user",
         "content": f"[conversation summary — compacted from {old_id}]\n{summary}"},
        {"role": "assistant",
         "content": [{"type": "text", "text": "Understood — continuing from the summary."}]},
    ]
    session.session_id = ""
    session.created = ""
    session.title = f"compacted: {old_title}"[:60] if old_title else ""
    session.journaled = False  # the post-compaction segment can journal again
    save_session(session)  # mint the new file now so the seed survives a crash
    ui.print_text(f"✓ compacted — new session started (previous saved as {old_id})\n")


def _print_last_exchange(messages: list) -> None:
    last_user = next(
        (m["content"] for m in reversed(messages)
         if m["role"] == "user" and isinstance(m["content"], str)),
        None,
    )
    last_texts: list[str] = []
    for m in reversed(messages):
        if m["role"] == "assistant":
            last_texts = [b["text"] for b in m["content"]
                          if isinstance(b, dict) and b.get("type") == "text"]
            break
    if last_user:
        ui.print_text(f"\n(you) {last_user}\n")
    if last_texts:
        ui.print_text(f"(luban) {' '.join(last_texts)}\n")


def restore_session(session: Session, data: dict) -> None:
    # Repair any already-saved history that ends in an unanswered tool_use, so a
    # session closed mid-tool-call resumes cleanly instead of 400-crashing (E14).
    session.messages = agent.sanitize_history(data["messages"])
    session.model = data.get("model", session.model)
    session.session_id = data["id"]
    session.created = data.get("created", "")
    session.title = data.get("title", "")
    ui.print_text(
        f'resumed {data["id"]} · "{session.title}" · {session.model} '
        f"· {len(session.messages)} messages\n"
    )
    if data.get("project") and data["project"] != session.project:
        ui.print_text(f"note: this conversation referenced another folder: {data['project']}\n")
    _print_last_exchange(session.messages)


def pick_session(project: str, all_projects: bool, input_fn=input) -> dict | None:
    heads = sessions_mod.list_sessions(None if all_projects else project)
    if not heads:
        ui.print_text("no saved sessions found.\n")
        return None
    for i, h in enumerate(heads, 1):
        prefix = f"[{Path(h['project']).name}] " if all_projects else ""
        ui.print_text(
            f'{i:3}. {prefix}{h["updated"]}  {h["model"]}  "{h["title"]}"'
            f"  ({h['message_count']} msgs)\n"
        )
    try:
        raw = input_fn("resume which? (number, Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw.isdigit() or not (1 <= int(raw) <= len(heads)):
        return None
    try:
        return sessions_mod.load(heads[int(raw) - 1]["id"])
    except Exception:
        ui.print_text("could not read that session file — starting fresh.\n")
        return None


def handle_command(line: str, session: Session, client=None, ctx=None, cfg=None) -> str:
    if not line.startswith("/"):
        return "not_command"
    parts = line.split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "/exit":
        return "exit"
    if cmd == "/auto":
        session.auto = True
        return "handled"
    if cmd == "/thinking":
        if arg in ("on", "off"):
            session.thinking = arg == "on"
        elif arg:
            ui.print_text("usage: /thinking [on|off]\n")
            return "handled"
        ui.print_text(f"thinking: {'on' if session.thinking else 'off'}\n")
        return "handled"
    if cmd == "/effort":
        levels = ("low", "medium", "high", "xhigh", "max")
        if arg in levels:
            session.effort = arg
        elif arg:
            ui.print_text(f"usage: /effort [{' | '.join(levels)}]\n")
            return "handled"
        ui.print_text(f"effort: {session.effort}\n")
        return "handled"
    if cmd == "/verbose":
        if arg in ("on", "off"):
            session.thinking_verbose = arg == "on"
        elif arg:
            ui.print_text("usage: /verbose [on|off]  (show the grey thinking text)\n")
            return "handled"
        ui.print_text(f"thinking text: {'shown' if session.thinking_verbose else 'hidden'}\n")
        return "handled"
    if cmd == "/config":
        # Effective settings (session overrides where they exist) so every
        # capability is discoverable even if it's absent from a stale config (E19).
        rows = [
            ("model", session.model), ("auto", session.auto),
            ("thinking", session.thinking), ("effort", session.effort),
            ("thinking_verbose", session.thinking_verbose),
        ]
        if cfg is not None:
            rows += [
                ("memory_enabled", cfg.memory_enabled),
                ("auto_continue", cfg.auto_continue),
                ("warn_tokens", f"{cfg.warn_tokens:,}"),
                ("web_search", cfg.web_search),
                ("web_search_tool_type", cfg.web_search_tool_type),
                ("subagents", cfg.subagents),
                ("allow_out_of_tree_file_edits", cfg.allow_out_of_tree_file_edits),
                ("memory_file", cfg.memory_file or "(auto: LUBAN.md/CLAUDE.md/AGENTS.md)"),
            ]
        ui.print_text("effective settings:\n")
        for k, v in rows:
            ui.print_text(f"  {k} = {v}\n")
        if cfg is not None and ctx is not None:
            usage = always_on_usage(Path(ctx.project_root), cfg)
            if usage:
                ui.print_text("always-on context (sent every turn):\n")
                for label, size, cap in usage:
                    over = "  ⚠ OVER CAP — tail dropped" if size > cap else ""
                    ui.print_text(f"  {label}: {size:,}/{cap:,} chars{over}\n")
                for warning in memory_mod.cap_warnings(usage):
                    ui.print_text(warning + "\n")
        miss = config_mod.missing_keys() if cfg is not None else []
        if miss:
            ui.print_text(f"({len(miss)} new setting(s) not in your config.toml — "
                          "run `luban --sync-config` to add them)\n")
        return "handled"
    if cmd == "/clear":
        session.messages.clear()
        session.session_id = ""
        session.title = ""
        session.created = ""
        return "handled"
    if cmd == "/model":
        available = client_mod.list_models(client) if client is not None else None
        if not arg:
            if available:
                ui.print_text("available models:\n")
                for m in available:
                    marker = "  (current)" if m == session.model else ""
                    ui.print_text(f"  {m}{marker}\n")
            else:
                ui.print_text(f"current model: {session.model}\n")
            return "handled"
        wanted = arg.strip()
        if available and wanted not in available:
            ui.print_text(f"unknown model: {wanted}\navailable models:\n")
            for m in available:
                ui.print_text(f"  {m}\n")
            return "handled"
        session.model = wanted
        ui.print_text(f"✓ model → {wanted}\n")
        return "handled"
    if cmd == "/sessions":
        heads = sessions_mod.list_sessions(session.project or None)
        if not heads:
            ui.print_text("no saved sessions found.\n")
            return "handled"
        for h in heads:
            ui.print_text(
                f'  {h["id"]}  {h["updated"]}  {h["model"]}  "{h["title"]}"'
                f"  ({h['message_count']} msgs)\n"
            )
        return "handled"
    if cmd == "/skills":
        catalog = skills_mod.list_skills(session.project or ".")
        if not catalog:
            ui.print_text(
                "no skills found (put <name>.md files or <name>/SKILL.md folders "
                "in ~/.luban/skills/ or <project>/.luban/skills/).\n"
            )
            return "handled"
        for s in catalog:
            marker = " [project]" if s["scope"] == "project" else ""
            ui.print_text(f"  {s['name']}: {s['description']}{marker}\n")
        return "handled"
    if cmd == "/skill":
        name = arg.strip()
        if not name:
            ui.print_text("usage: /skill <name>\n")
            return "handled"
        body = skills_mod.load_skill(name, session.project or ".")
        if body is None:
            names = ", ".join(
                s["name"] for s in skills_mod.list_skills(session.project or ".")
            ) or "(none)"
            ui.print_text(f"unknown skill: {name}\navailable: {names}\n")
            return "handled"
        session.pending_context.append(f"[skill: {name}]\n{body}")
        ui.print_text(f"✓ skill queued: {name} (applies to your next message)\n")
        return "handled"
    if cmd == "/compact":
        if client is None:
            ui.print_text("compact needs a client.\n")
            return "handled"
        compact_session(session, client, ctx, cfg)
        return "handled"
    if cmd == "/reflect":
        if client is None or ctx is None or cfg is None:
            ui.print_text("reflect needs a client.\n")
            return "handled"
        reflect_session(session, client, ctx, cfg)
        return "handled"
    return "handled"  # unknown /command: swallow rather than send to model


def configure_utf8_io() -> None:
    """Force UTF-8 across luban's WHOLE process tree, regardless of the OS locale.

    The cp1252 family (E7/E8/E10/E12/E20) all trace to one root: on Windows the
    default locale is cp1252, and Python honors it for std streams, file I/O, AND
    the processes luban spawns. This is the single process-level lever:
      - luban's own std streams are reconfigured to UTF-8 here;
      - PYTHONUTF8=1 / PYTHONIOENCODING=utf-8 are set in the environment so EVERY
        child process luban later spawns (a run_command Python probe, a pipeline,
        anything — not just run_command) starts in UTF-8 mode and won't charmap-
        crash printing arrows/emoji (E20).
    luban's own file I/O pins encoding at each site (policy-tested), and run_command
    also decodes child pipes as UTF-8, so no surface is left on the locale default.
    Best-effort: a stream without reconfigure() is skipped, never fatal.
    """
    # Children read these at their own interpreter start and inherit os.environ.
    os.environ["PYTHONUTF8"] = "1"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> None:
    configure_utf8_io()
    ns = parse_args(argv)
    if ns.set_home is not None:
        set_home(ns.set_home)
        return
    if ns.sync_config:
        added = config_mod.sync_config()
        if added:
            ui.print_text(
                f"added {len(added)} setting(s) to {config_mod.CONFIG_PATH} "
                f"(commented): {', '.join(added)}\n"
            )
        else:
            ui.print_text("config.toml is already up to date.\n")
        return
    project_root = Path(ns.dir).resolve()
    notice = home_notice()
    if notice:
        ui.print_text(notice + "\n")
    cfg = config_mod.load_config()
    session = Session(
        model=resolve_model(ns.model, cfg), max_tokens=ns.max_tokens,
        auto=ns.auto, stream=ns.stream, messages=[],
        project=str(project_root),
        thinking=cfg.thinking, effort=cfg.effort,
        thinking_verbose=cfg.thinking_verbose,
    )
    custom_names = setup_custom_tools()
    prev, cur = detect_upgrade()  # runs regardless of memory (dotfile at home root)
    upgraded = bool(prev and prev != cur)
    # Cumulative span since the last-seen version — a multi-version jump surfaces
    # every intermediate release's notes, not just the newest (E17).
    section = changelog.sections_between(prev, cur) if upgraded else ""
    if upgraded:
        ui.print_text(upgrade_banner(prev, section) + "\n")  # everyone sees this
        miss = config_mod.missing_keys()
        if miss:
            ui.print_text(
                f"tip: {len(miss)} new setting(s) available — run `luban --sync-config` "
                "to add them to your config.toml, or /config to see what's in effect.\n"
            )
    if cfg.memory_enabled:
        memory_mod.ensure_scaffold()  # guarantees enhancements.md exists to reconcile
        if upgraded:
            session.pending_context.append(reconcile_directive(prev, section))
    client = client_mod.get_client()
    ctx = build_tool_context(session, project_root, cfg, client=client)
    if ns.cont:
        data = sessions_mod.latest(str(project_root))
        if data is None:
            ui.print_text("no previous session here — starting fresh.\n")
        else:
            restore_session(session, data)
    elif ns.resume:
        data = pick_session(str(project_root), ns.all)
        if data is not None:
            restore_session(session, data)
    else:
        # Plain start (no -c/-r): a prior session for this folder — e.g. one you
        # compacted then exited — is on disk but wouldn't auto-load, so it looks
        # gone. Surface it. auto_continue reopens it instead of just reminding.
        recent = sessions_mod.latest(str(project_root))
        if recent is not None:
            if cfg.auto_continue:
                restore_session(session, recent)
            else:
                n = len(recent.get("messages", []))
                title = recent.get("title") or "(untitled)"
                when = recent.get("updated", "")
                ui.print_text(
                    f"↩ a recent session is saved here: \"{title}\" ({n} messages"
                    + (f", updated {when}" if when else "")
                    + ") — resume with `luban -c`, or set auto_continue = true to "
                    "reopen it automatically.\n"
                )
    ui.print_text(
        f"luban — project: {project_root}  model: {session.model}  "
        f"platform: {cfg.platform}\n"
    )
    if custom_names:
        ui.print_text(f"custom tools: {', '.join(custom_names)}\n")
    # Tell the HUMAN when an always-on file is over its cap and being cut (C1) —
    # otherwise luban silently never sees the tail and looks like it's ignoring you.
    for warning in memory_mod.cap_warnings(always_on_usage(project_root, cfg)):
        ui.print_text(warning + "\n")
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        status = handle_command(line, session, client, ctx, cfg)
        if status == "exit":
            break
        if status == "handled":
            continue
        session.messages.append(
            {"role": "user", "content": compose_user_message(session, line)}
        )
        agent_config = build_agent_config(session, cfg, project_root)
        ui.print_text("\nluban> ")
        try:
            session.messages = agent.run_turn(
                client, agent_config, session.messages, ctx, ui.print_text, ui.print_thinking
            )
        except KeyboardInterrupt:
            session.messages.pop()  # drop the unanswered user turn so history stays valid
            ui.print_text("\n[interrupted]\n")
        except Exception as exc:  # a bad turn must not kill the session (E14)
            if session.messages and session.messages[-1].get("role") == "user":
                session.messages.pop()  # drop the turn that failed; keep history valid
            ui.print_text(f"\n[turn failed: {exc}] — history preserved, try again.\n")
        else:
            save_session(session)
            est = estimate_tokens(session.messages)
            if est > cfg.warn_tokens:
                ui.print_text(
                    f"\nnote: conversation is large (~{est:,} tokens) — consider /compact\n"
                )
            ui.print_text("\n")
    exit_journal(session, cfg, project_root)
