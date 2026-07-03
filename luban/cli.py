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
