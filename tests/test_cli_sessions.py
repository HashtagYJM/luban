from luban import cli, sessions


def _session(**over):
    kw = dict(model="claude-sonnet-5", max_tokens=100, auto=True, stream=False,
              project="/projA")
    kw.update(over)
    return cli.Session(**kw)


def test_save_session_noop_when_no_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    s = _session()
    cli.save_session(s)
    assert list(tmp_path.glob("*.json")) == []


def test_save_session_assigns_id_and_title(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    s = _session(messages=[{"role": "user", "content": "fix the bug in utils.py please"}])
    cli.save_session(s)
    assert s.session_id and s.created
    data = sessions.load(s.session_id, sessions_dir=tmp_path)
    assert data["title"] == "fix the bug in utils.py please"
    assert data["project"] == "/projA"
    assert data["model"] == "claude-sonnet-5"


def test_save_session_title_truncated_to_60(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    s = _session(messages=[{"role": "user", "content": "x" * 100}])
    cli.save_session(s)
    assert len(sessions.load(s.session_id, sessions_dir=tmp_path)["title"]) == 60


def test_save_session_rewrites_same_file_and_updates_model(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    s = _session(messages=[{"role": "user", "content": "hi"}])
    cli.save_session(s)
    first_id = s.session_id
    s.model = "claude-fable-5"
    s.messages.append({"role": "assistant", "content": [{"type": "text", "text": "hello"}]})
    cli.save_session(s)
    assert s.session_id == first_id
    assert len(list(tmp_path.glob("*.json"))) == 1
    data = sessions.load(first_id, sessions_dir=tmp_path)
    assert data["model"] == "claude-fable-5"
    assert len(data["messages"]) == 2


def test_save_session_warning_not_fatal_on_oserror(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)

    def boom(data, sessions_dir=None):
        raise OSError("disk full")

    monkeypatch.setattr(sessions, "save", boom)
    s = _session(messages=[{"role": "user", "content": "hi"}])
    cli.save_session(s)  # must not raise
    assert "could not save session" in capsys.readouterr().out


def test_clear_detaches_session():
    s = _session(messages=[{"role": "user", "content": "hi"}],
                 session_id="2026-07-03-1400-abcd", title="hi", created="2026-07-03T14:00:00")
    assert cli.handle_command("/clear", s) == "handled"
    assert s.messages == [] and s.session_id == "" and s.title == "" and s.created == ""
