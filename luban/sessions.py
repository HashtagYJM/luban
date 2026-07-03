"""Session persistence — one JSON file per session under ~/.luban/sessions/.

Standard library only. Writes are atomic (tmp file + os.replace) so a crash
never leaves a half-written session; the previous complete save survives.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

SESSIONS_DIR = Path.home() / ".luban" / "sessions"


class SessionNotFound(Exception):
    pass


def _dir(sessions_dir: Path | None) -> Path:
    # Resolve the default at call time so tests can monkeypatch SESSIONS_DIR.
    return sessions_dir if sessions_dir is not None else SESSIONS_DIR


def new_session_id() -> str:
    return f"{datetime.now().strftime('%Y-%m-%d-%H%M')}-{secrets.token_hex(2)}"


def save(data: dict, sessions_dir: Path | None = None) -> Path:
    d = _dir(sessions_dir)
    d.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    path = d / f"{data['id']}.json"
    tmp = d / f"{data['id']}.tmp"
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(session_id: str, sessions_dir: Path | None = None) -> dict:
    path = _dir(sessions_dir) / f"{session_id}.json"
    if not path.exists():
        raise SessionNotFound(session_id)
    return json.loads(path.read_text(encoding="utf-8"))


_HEADER_KEYS = ("id", "project", "created", "updated", "model", "title")


def list_sessions(project: str | None, sessions_dir: Path | None = None) -> list[dict]:
    d = _dir(sessions_dir)
    if not d.exists():
        return []
    headers: list[dict] = []
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            header = {k: data[k] for k in _HEADER_KEYS}
            header["message_count"] = len(data["messages"])
        except Exception:
            print(f"warning: skipping unreadable session file {path.name}", file=sys.stderr)
            continue
        if project is None or header["project"] == project:
            headers.append(header)
    headers.sort(key=lambda h: h["updated"], reverse=True)
    return headers


def latest(project: str, sessions_dir: Path | None = None) -> dict | None:
    heads = list_sessions(project, sessions_dir)
    if not heads:
        return None
    return load(heads[0]["id"], sessions_dir)
