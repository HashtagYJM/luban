from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from luban import __version__, agent, config as config_mod, tools, ui
from luban import audit as audit_mod
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
    "Before this conversation is compacted: (1) save any durable facts you learned "
    "about the user or their practices using the remember tool — update existing "
    "facts, don't duplicate; (2) write a 2-4 line journal entry about what happened "
    "this session using the journal tool. Then reply with just: saved."
)
REFLECT_PROMPT = (
    "Housekeeping for your long-term memory. Review the memory index and recent "
    "journal in your context: use recall to inspect facts, promote durable items "
    "from the journal into facts with remember, and merge or forget stale, "
    "duplicate, or wrong facts. Then report briefly what you changed."
)
WARN_TOKENS = 60_000
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="luban", description="Terminal coding agent.")
    p.add_argument("--dir", default=".", help="Project root (default: cwd).")
    p.add_argument("--model", default=None)
    p.add_argument("--max-tokens", type=int, default=8192)
    p.add_argument("--auto", action="store_true", help="Skip confirmation prompts (auto-approves ALL file writes and shell commands — use with care).")
    p.add_argument("--no-stream", dest="stream", action="store_false")
    p.add_argument("--version", action="version", version=f"luban {__version__}")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--continue", "-c", dest="cont", action="store_true",
                   help="Resume the most recent session for this folder.")
    g.add_argument("--resume", "-r", action="store_true",
                   help="Pick a past session to resume.")
    p.add_argument("--all", action="store_true",
                   help="With --resume: list sessions from all folders.")
    p.set_defaults(stream=True)
    return p.parse_args(argv)


def build_tool_context(
    session: Session, project_root: Path, cfg: config_mod.Config | None = None
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

    return tools.ToolContext(
        project_root=Path(project_root),
        confirm=confirm,
        render_diff=ui.render_diff,
        render_command=ui.render_command,
        decide=decide,
        audit=audit_cb,
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
            "messages": session.messages,
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


def setup_custom_tools() -> list[str]:
    """Load user-owned tools_local.py and merge its tools into the toolbox."""
    specs = custom_tools_mod.load_custom_tools()
    return tools.register_custom(specs) if specs else []


def resolve_model(flag_model: str | None, cfg: config_mod.Config) -> str:
    return flag_model or cfg.model or client_mod.DEFAULT_MODEL


def build_agent_config(session: Session, cfg: config_mod.Config, project_root: Path) -> agent.AgentConfig:
    return agent.AgentConfig(
        session.model, session.max_tokens, session.stream, platform=cfg.platform,
        skills=skills_mod.list_skills(str(project_root)),
        memory=read_project_memory(project_root, cfg.memory_file),
        global_memory=memory_mod.bootstrap_block() if cfg.memory_enabled else "",
        tools=tools.active_tools(cfg.memory_enabled),
    )


def flush_memory(session: Session, client, ctx, cfg: config_mod.Config) -> None:
    """Best-effort: let the model bank durable facts before compaction destroys context."""
    if not cfg.memory_enabled or not session.messages:
        return
    ui.print_text("(memory flush…)\n")
    msgs = session.messages + [{"role": "user", "content": FLUSH_PROMPT}]
    config = agent.AgentConfig(
        session.model, session.max_tokens, stream=False, platform=cfg.platform,
        global_memory=memory_mod.bootstrap_block(), tools=tools.active_tools(True),
    )
    try:
        agent.run_turn(client, config, msgs, ctx, lambda t: None)
    except Exception as exc:
        ui.print_text(f"(memory flush skipped: {exc})\n")


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
    if not cfg.memory_enabled or not session.messages:
        return
    memory_mod.journal_append(
        f"[{Path(project_root).name}] '{session.title or 'untitled'}' — "
        f"{len(session.messages)} messages ({session.model})"
    )


def estimate_tokens(messages: list) -> int:
    return sum(len(str(m)) for m in messages) // 4


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
    session.messages = data["messages"]
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


def main(argv: list[str] | None = None) -> None:
    ns = parse_args(argv)
    project_root = Path(ns.dir).resolve()
    cfg = config_mod.load_config()
    session = Session(
        model=resolve_model(ns.model, cfg), max_tokens=ns.max_tokens,
        auto=ns.auto, stream=ns.stream, messages=[],
        project=str(project_root),
    )
    custom_names = setup_custom_tools()
    if cfg.memory_enabled:
        memory_mod.ensure_scaffold()
    client = client_mod.get_client()
    ctx = build_tool_context(session, project_root, cfg)
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
    ui.print_text(
        f"luban — project: {project_root}  model: {session.model}  "
        f"platform: {cfg.platform}\n"
    )
    if custom_names:
        ui.print_text(f"custom tools: {', '.join(custom_names)}\n")
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
        else:
            save_session(session)
            est = estimate_tokens(session.messages)
            if est > WARN_TOKENS:
                ui.print_text(
                    f"\nnote: conversation is large (~{est:,} tokens) — consider /compact\n"
                )
            ui.print_text("\n")
    exit_journal(session, cfg, project_root)
