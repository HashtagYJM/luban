"""Which luban processes are running right now, and what each is doing.

One marker file per process in the OS temp dir — never in the luban home, which may be
a synced folder: a marker rewritten at every turn would put a sync round-trip in the
path of every prompt, and a marker synced from another machine would claim a session
is open here when it is not. A saved transcript cannot answer "is this open?": its
timestamp looks the same for a long model call, a prompt nobody has answered, and a
terminal closed an hour ago. The process says so itself, and a dead pid says "closed".

States: `working` (a turn is running), `input` (waiting at the prompt), `approval`
(waiting on a confirm). A marker with no live pid is stale and is dropped on read.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from luban import paths

LIVE_DIR = Path(tempfile.gettempdir()) / "luban-live"

_state: dict = {}  # this process's last marker, so `since` survives a same-state rewrite


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Never os.kill on Windows: any signal but CTRL_* is TerminateProcess.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _own_path() -> Path:
    return LIVE_DIR / f"{os.getpid()}.json"


def publish(session_id: str, project: str, state: str, title: str = "") -> None:
    """Record this process's state. Best-effort: a marker must never break a turn."""
    now = datetime.now().isoformat(timespec="seconds")
    since = _state.get("since", now) if _state.get("state") == state else now
    marker = {"pid": os.getpid(), "session": session_id, "project": project,
              "state": state, "since": since, "title": title}
    _state.clear()
    _state.update(marker)
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        paths.atomic_write_text(_own_path(), json.dumps(marker, ensure_ascii=False))
    except Exception:
        pass


def clear() -> None:
    _state.clear()
    try:
        _own_path().unlink(missing_ok=True)
    except Exception:
        pass


def live(include_self: bool = True) -> dict[str, dict]:
    """{session id: marker} for every running luban; stale markers are removed."""
    out: dict[str, dict] = {}
    try:
        files = list(LIVE_DIR.glob("*.json"))
    except OSError:
        return out
    for path in files:
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
            pid = int(marker.get("pid", 0))
        except Exception:
            continue
        if not _pid_alive(pid):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        if not include_self and pid == os.getpid():
            continue
        if marker.get("session"):
            out[marker["session"]] = marker
    return out


def holder(session_id: str) -> dict | None:
    """The OTHER running process that has this session open, if any."""
    if not session_id:
        return None
    return live(include_self=False).get(session_id)


def state_label(marker: dict | None) -> str:
    if not marker:
        return ""
    state = marker.get("state", "")
    if state == "working":
        return "● working"
    if state == "approval":
        return "✋ waiting for approval"
    if state == "input":
        return "✋ waiting for input"
    return state
