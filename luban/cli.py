from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from luban import agent, config as config_mod, tools, ui
from luban import client as client_mod
from luban import sessions as sessions_mod


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="luban", description="Terminal coding agent.")
    p.add_argument("--dir", default=".", help="Project root (default: cwd).")
    p.add_argument("--model", default=client_mod.DEFAULT_MODEL)
    p.add_argument("--max-tokens", type=int, default=8192)
    p.add_argument("--auto", action="store_true", help="Skip confirmation prompts (auto-approves ALL file writes and shell commands — use with care).")
    p.add_argument("--no-stream", dest="stream", action="store_false")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--continue", "-c", dest="cont", action="store_true",
                   help="Resume the most recent session for this folder.")
    g.add_argument("--resume", "-r", action="store_true",
                   help="Pick a past session to resume.")
    p.add_argument("--all", action="store_true",
                   help="With --resume: list sessions from all folders.")
    p.set_defaults(stream=True)
    return p.parse_args(argv)


def build_tool_context(session: Session, project_root: Path) -> tools.ToolContext:
    def confirm(prompt: str) -> bool:
        if session.auto:
            return True
        decision = ui.ask_confirm(prompt)
        if decision == "all":
            session.auto = True
            return True
        return decision == "yes"

    return tools.ToolContext(
        project_root=Path(project_root),
        confirm=confirm,
        render_diff=ui.render_diff,
        render_command=ui.render_command,
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
    raw = input_fn("resume which? (number, Enter to cancel): ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(heads)):
        return None
    try:
        return sessions_mod.load(heads[int(raw) - 1]["id"])
    except Exception:
        ui.print_text("could not read that session file — starting fresh.\n")
        return None


def handle_command(line: str, session: Session) -> str:
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
    if cmd == "/model" and arg:
        session.model = arg.strip()
        return "handled"
    return "handled"  # unknown /command: swallow rather than send to model


def main(argv: list[str] | None = None) -> None:
    ns = parse_args(argv)
    project_root = Path(ns.dir).resolve()
    session = Session(
        model=ns.model, max_tokens=ns.max_tokens,
        auto=ns.auto, stream=ns.stream, messages=[],
        project=str(project_root),
    )
    cfg = config_mod.load_config()
    client = client_mod.get_client()
    ctx = build_tool_context(session, project_root)
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
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        status = handle_command(line, session)
        if status == "exit":
            break
        if status == "handled":
            continue
        session.messages.append({"role": "user", "content": line})
        agent_config = agent.AgentConfig(
            session.model, session.max_tokens, session.stream, platform=cfg.platform
        )
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
            ui.print_text("\n")
