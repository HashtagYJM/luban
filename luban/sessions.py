"""Session persistence — one JSON file per session under ~/.luban/sessions/.

Standard library only. Writes go through paths.atomic_write_text, so a crash never
leaves a half-written session; the previous complete save survives.
"""
from __future__ import annotations

import json
import secrets
import sys
from datetime import datetime
from pathlib import Path

from luban import paths

SESSIONS_DIR = paths.luban_home() / "sessions"

# One implementation for the whole codebase — see paths.atomic_write_text.
_atomic_write_text = paths.atomic_write_text


class SessionNotFound(Exception):
    pass


class AmbiguousSession(Exception):
    """A reference matched more than one session — the caller should show them."""

    def __init__(self, matches: list[dict]) -> None:
        super().__init__(f"{len(matches)} sessions match")
        self.matches = matches


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
    _atomic_write_text(path, json.dumps(data, indent=1, ensure_ascii=False))
    return path


def archive(data: dict, sessions_dir: Path | None = None) -> Path:
    """The verbatim history at this moment, in its own file, before a fold or a stub
    rewrites the session's. `sessions/archive/` is not scanned by list_sessions (its glob
    is one level deep), so an archive is never mistaken for a thread. One file per
    rewrite: the first fold's archive holds what the second fold's summary elides.

    Never overwrites: a trim and a fold in the same second are two rewrites, and the
    second archive (holding the stub) used to replace the first (holding the result the
    stub points at). A suffix keeps both."""
    d = _dir(sessions_dir) / "archive"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = d / f"{data['id']}-{stamp}.json"
    n = 1
    while path.exists():
        n += 1
        path = d / f"{data['id']}-{stamp}-{n}.json"
    data = dict(data)
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write_text(path, json.dumps(data, indent=1, ensure_ascii=False))
    return path


def archives(session_id: str, sessions_dir: Path | None = None) -> list[Path]:
    """Every archive written for this session, oldest first. A fold and a /compact both
    keep the session's id, so a long thread's earlier history is spread across these."""
    d = _dir(sessions_dir) / "archive"
    if not d.exists():
        return []
    return sorted(d.glob(f"{session_id}-*.json"))


def load(session_id: str, sessions_dir: Path | None = None) -> dict:
    path = _dir(sessions_dir) / f"{session_id}.json"
    if not path.exists():
        raise SessionNotFound(session_id)
    return json.loads(path.read_text(encoding="utf-8"))


_HEADER_KEYS = ("id", "project", "created", "updated", "model", "title")
# Keys a session file may lack because it was written before they existed. Listed with
# their default, so an old file is read rather than skipped as unreadable.
_OPTIONAL_HEADER_KEYS = {"title_source": "first_line"}


def list_sessions(project: str | None, sessions_dir: Path | None = None) -> list[dict]:
    d = _dir(sessions_dir)
    if not d.exists():
        return []
    headers: list[dict] = []
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            header = {k: data[k] for k in _HEADER_KEYS}
            header.update({k: data.get(k) or v for k, v in _OPTIONAL_HEADER_KEYS.items()})
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


def resolve(ref: str, project: str | None = None,
            sessions_dir: Path | None = None, listed: list[dict] | None = None) -> dict:
    """Turn whatever the user typed into a session.

    Accepts a listing number, a full id, or any distinguishing fragment of an id
    or a title — because ids are timestamps-plus-hex and nobody wants to retype
    `2026-07-14-0930-a1b2` to switch threads. Sessions in `project` win over
    identically-matching ones elsewhere; a fragment that still matches several
    raises AmbiguousSession rather than guessing.
    """
    ref = ref.strip()
    if not ref:
        raise SessionNotFound(ref)
    # A number means a row of the list the user was just shown — which may be a
    # filtered one — never a row of some list they never saw.
    heads = listed if listed is not None else list_sessions(project, sessions_dir)
    if ref.isdigit():  # a number from the /sessions listing
        i = int(ref)
        if 1 <= i <= len(heads):
            return load(heads[i - 1]["id"], sessions_dir)
        raise SessionNotFound(ref)
    try:
        return load(ref, sessions_dir)  # a full id, from anywhere
    except SessionNotFound:
        pass
    low = ref.lower()
    matches = [
        h for h in list_sessions(None, sessions_dir)
        if low in h["id"].lower() or low in (h["title"] or "").lower()
    ]
    here = [h for h in matches if project and h["project"] == project]
    matches = here or matches
    if not matches:
        raise SessionNotFound(ref)
    if len(matches) > 1:
        raise AmbiguousSession(matches)
    return load(matches[0]["id"], sessions_dir)


# ------------------------------------------------------------------ session notes ----
# One luban session leaves a note for another; the target takes it once at its next turn.
# The journal was the channel people used, and it is the wrong one: it is re-sent on every
# call of every session in the project for days, a busy day evicts the line before anyone
# acts on it, and it cannot name a recipient. A note is addressed, read once, and paid once.

def _notes_path(session_id: str, sessions_dir: Path | None) -> Path:
    # .jsonl, so list_sessions' "*.json" never mistakes it for a session.
    return _dir(sessions_dir) / f"{session_id}.notes.jsonl"


def send_note(target: str, text: str, sender: str, project: str,
              sessions_dir: Path | None = None) -> None:
    """Append one note for `target`. Raises SessionNotFound for an id with no saved file:
    a note nobody will ever open is a silent loss, not a delivery."""
    if not target or not (_dir(sessions_dir) / f"{target}.json").exists():
        raise SessionNotFound(target)
    note = {"from": sender, "project": project, "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "text": text}
    with _notes_path(target, sessions_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(note, ensure_ascii=False) + "\n")


def pending_notes(session_id: str, sessions_dir: Path | None = None) -> int:
    try:
        return sum(1 for line in _notes_path(session_id, sessions_dir)
                   .read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def take_notes(session_id: str, sessions_dir: Path | None = None) -> list[dict]:
    """Every pending note for this session, removed from disk as it is read.

    Moved aside before reading, so a note appended meanwhile lands in a fresh file and
    waits for the next turn instead of being deleted unread.
    """
    if not session_id:
        return []
    path = _notes_path(session_id, sessions_dir)
    taking = path.with_suffix(".taking")
    try:
        path.replace(taking)
    except OSError:
        return []
    notes = []
    for line in taking.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            notes.append(json.loads(line))
        except ValueError:
            continue
    taking.unlink(missing_ok=True)
    return notes


# ------------------------------------------------------------------------ tidy ----

def _age_days(header: dict) -> float:
    try:
        return (datetime.now() - datetime.fromisoformat(header["updated"])).total_seconds() / 86400
    except Exception:
        return 0.0


def recent(heads: list[dict], days: int, keep: set[str] = frozenset()) -> list[dict]:
    """The sessions touched within `days`, plus any whose id is in `keep` (the live ones)."""
    return [h for h in heads if h["id"] in keep or _age_days(h) <= days]


def tidy(days: int, skip: set[str] = frozenset(),
         sessions_dir: Path | None = None) -> tuple[int, int, int]:
    """Move sessions untouched for `days`, with their notes and fold archives, into
    `attic/YYYY-MM/` (the month they were last touched). Nothing is deleted: the raw
    record stays readable; it just leaves every list. Returns (sessions, archives, bytes).
    """
    d = _dir(sessions_dir)
    moved = archived = size = 0
    for h in list_sessions(None, sessions_dir):
        if h["id"] in skip or _age_days(h) <= days:
            continue
        month = (h.get("updated") or "0000-00")[:7]
        dest = d / "attic" / month
        dest.mkdir(parents=True, exist_ok=True)
        files = [d / f"{h['id']}.json", d / f"{h['id']}.notes.jsonl",
                 *archives(h["id"], sessions_dir)]
        for f in files:
            if not f.exists():
                continue
            size += f.stat().st_size
            f.replace(dest / f.name)
            if f.parent.name == "archive":
                archived += 1
        moved += 1
    return moved, archived, size
