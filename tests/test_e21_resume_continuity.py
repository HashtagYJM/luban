"""E21 — resume/session continuity. Restoring state must come from the session
TRANSCRIPT (project-scoped, deterministic), not from narrating the journal."""
import pytest

from luban import cli, memory, sessions as sessions_mod, tools


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_mod, "SESSIONS_DIR", tmp_path / "sessions")
    return tmp_path


def _save(project, title, sid=None, msgs=None):
    sessions_mod.save({
        "id": sid or sessions_mod.new_session_id(), "project": project,
        "created": "2026-07-13T09:00:00", "model": "m", "title": title,
        "messages": msgs or [{"role": "user", "content": "the task"},
                             {"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
    })


def _session(project):
    return cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       messages=[], project=project, title="")


def _capture(monkeypatch):
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    return out


# ---- /resume is first-class and project-scoped ----

def test_slash_resume_restores_this_projects_session(store, monkeypatch):
    proj = str(store / "GLIO")
    _save(proj, "glio work", sid="sid-glio")
    out = _capture(monkeypatch)
    s = _session(proj)
    cli.handle_command("/resume", s)
    assert s.session_id == "sid-glio"
    assert s.messages  # transcript restored, not inferred
    assert "glio work" in "".join(out)


def test_slash_resume_never_picks_another_projects_session(store, monkeypatch):
    """The E21 failure: the journal's newest entry pointed at another project, so
    luban resumed the wrong thread. Project-scoped lookup makes that impossible."""
    _save(str(store / "FM-Monitor"), "fm monitor work", sid="sid-fm")
    proj = str(store / "GLIO")  # current project has NO session
    out = _capture(monkeypatch)
    s = _session(proj)
    cli.handle_command("/resume", s)
    assert s.session_id == ""  # nothing restored
    assert "no other saved session for this project" in "".join(out)
    assert "fm monitor" not in "".join(out).lower()  # the other project is never reached for


def test_slash_resume_saves_the_current_thread_first(store, monkeypatch):
    proj = str(store / "GLIO")
    _save(proj, "older", sid="sid-old")
    _capture(monkeypatch)
    s = _session(proj)
    s.messages = [{"role": "user", "content": "work in progress"}]
    cli.handle_command("/resume", s)
    ids = {h["id"] for h in sessions_mod.list_sessions(proj)}
    assert "sid-old" in ids and len(ids) >= 2  # current thread was persisted, not lost


# ---- restore leads with the project (mismatch caught immediately) ----

def test_restore_leads_with_project_name(store, monkeypatch):
    out = _capture(monkeypatch)
    s = _session(str(store / "GLIO"))
    cli.restore_session(s, {"id": "x", "project": str(store / "GLIO"), "model": "m",
                            "title": "t", "messages": [{"role": "user", "content": "hi"}]})
    assert "resumed [GLIO]" in "".join(out)


def test_cross_project_restore_warns_loudly(store, monkeypatch):
    out = _capture(monkeypatch)
    s = _session(str(store / "GLIO"))
    cli.restore_session(s, {"id": "x", "project": str(store / "FM-Monitor"), "model": "m",
                            "title": "t", "messages": [{"role": "user", "content": "hi"}]})
    text = "".join(out)
    assert "resumed [FM-Monitor]" in text
    assert "DIFFERENT project" in text and "WARNING" in text


# ---- the root cause: transcript is the state store, not the journal ----

def test_hygiene_routes_continuity_to_the_transcript():
    h = memory._HYGIENE
    assert "CONTINUITY" in h
    assert "sessions/<id>.json" in h
    assert "not a state store" in h  # the journal
    assert "shell '~'" in h  # the relocated-home trap


def test_sessions_tool_points_at_transcripts_not_journal():
    spec = next(t for t in tools.TOOLS if t["name"] == "sessions")
    assert "not the journal" in spec["description"]
    assert "never a shell" in spec["description"]
