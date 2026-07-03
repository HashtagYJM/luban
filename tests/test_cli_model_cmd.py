from conftest import FakeClient

from luban import cli, sessions


def _session(**over):
    kw = dict(model="claude-sonnet-5", max_tokens=100, auto=True, stream=False,
              project="/projA")
    kw.update(over)
    return cli.Session(**kw)


def test_model_no_arg_lists_and_marks_current(capsys):
    fc = FakeClient([], model_ids=["claude-sonnet-5", "claude-fable-5"])
    s = _session()
    assert cli.handle_command("/model", s, fc) == "handled"
    out = capsys.readouterr().out
    assert "claude-fable-5" in out
    assert "claude-sonnet-5  (current)" in out
    assert s.model == "claude-sonnet-5"  # unchanged


def test_model_no_arg_fallback_without_list(capsys):
    fc = FakeClient([], model_ids=None)
    s = _session()
    cli.handle_command("/model", s, fc)
    assert "current model: claude-sonnet-5" in capsys.readouterr().out


def test_model_switch_valid_confirms(capsys):
    fc = FakeClient([], model_ids=["claude-sonnet-5", "claude-fable-5"])
    s = _session()
    cli.handle_command("/model claude-fable-5", s, fc)
    assert s.model == "claude-fable-5"
    assert "model → claude-fable-5" in capsys.readouterr().out


def test_model_switch_unknown_rejected_lists_available(capsys):
    fc = FakeClient([], model_ids=["claude-sonnet-5", "claude-fable-5"])
    s = _session()
    cli.handle_command("/model fable 5", s, fc)
    assert s.model == "claude-sonnet-5"  # unchanged
    out = capsys.readouterr().out
    assert "unknown model: fable 5" in out
    assert "claude-fable-5" in out


def test_model_switch_optimistic_without_list(capsys):
    fc = FakeClient([], model_ids=None)
    s = _session()
    cli.handle_command("/model anything-goes", s, fc)
    assert s.model == "anything-goes"


def test_model_switch_no_client_optimistic():
    s = _session()
    cli.handle_command("/model claude-fable-5", s)  # old 2-arg call still works
    assert s.model == "claude-fable-5"


def test_sessions_command_lists_project_sessions(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    sessions.save(
        {"id": "2026-07-02-0900-aaaa", "project": "/projA",
         "created": "2026-07-02T09:00:00", "model": "claude-sonnet-5",
         "title": "fix utils", "messages": [{"role": "user", "content": "fix utils"}]},
        sessions_dir=tmp_path,
    )
    s = _session()
    assert cli.handle_command("/sessions", s) == "handled"
    out = capsys.readouterr().out
    assert "fix utils" in out and "2026-07-02-0900-aaaa" in out


def test_sessions_command_empty(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    cli.handle_command("/sessions", _session())
    assert "no saved sessions" in capsys.readouterr().out
