import pytest

from luban import cli, sessions


def _saved(tmp_path, sid="2026-07-02-0900-aaaa", project="/projA", model="claude-fable-5",
           title="fix utils", updated="2026-07-02T09:00:00"):
    sessions.save(
        {"id": sid, "project": project, "created": "2026-07-02T09:00:00",
         "model": model, "title": title,
         "messages": [
             {"role": "user", "content": "fix utils"},
             {"role": "assistant", "content": [{"type": "text", "text": "done, tests pass"}]},
         ]},
        sessions_dir=tmp_path,
    )
    import json
    p = tmp_path / f"{sid}.json"
    d = json.loads(p.read_text()); d["updated"] = updated; p.write_text(json.dumps(d))


def _session(project="/projA"):
    return cli.Session(model="claude-sonnet-5", max_tokens=100, auto=True,
                       stream=False, project=project)


def test_parse_args_flags():
    # -r now takes an optional ref, so "present" is `is not None`, not truthiness:
    # a bare -r is "" (falsy) and still means "show me the picker".
    ns = cli.parse_args(["--continue"])
    assert ns.cont and ns.resume is None
    ns = cli.parse_args(["-r", "--all"])
    assert ns.resume == "" and ns.all


def test_continue_and_resume_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.parse_args(["--continue", "--resume"])


def test_restore_session_sets_state_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    _saved(tmp_path)
    s = _session()
    cli.restore_session(s, sessions.load("2026-07-02-0900-aaaa", sessions_dir=tmp_path))
    assert s.session_id == "2026-07-02-0900-aaaa"
    assert s.model == "claude-fable-5"
    assert len(s.messages) == 2
    out = capsys.readouterr().out
    # banner now leads with the PROJECT so a wrong-thread resume is obvious (E21);
    # the session id is still shown, just no longer first.
    assert "resumed [projA]" in out
    assert "2026-07-02-0900-aaaa" in out
    assert "fix utils" in out          # banner title + last exchange
    assert "done, tests pass" in out   # last assistant text


def test_restore_warns_on_other_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    _saved(tmp_path, project="/elsewhere")
    s = _session(project="/projA")
    cli.restore_session(s, sessions.load("2026-07-02-0900-aaaa", sessions_dir=tmp_path))
    out = capsys.readouterr().out
    assert "DIFFERENT project" in out and "WARNING" in out  # loud, not a soft note (E21)
    assert "/elsewhere" in out and "/projA" in out


def test_pick_session_by_number(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    _saved(tmp_path, sid="2026-07-01-0900-bbbb", title="older", updated="2026-07-01T09:00:00")
    _saved(tmp_path, sid="2026-07-02-0900-aaaa", title="newer", updated="2026-07-02T09:00:00")
    got = cli.pick_session("/projA", all_projects=False, input_fn=lambda _: "2")
    assert got["id"] == "2026-07-01-0900-bbbb"  # row 2 = older (newest first)
    assert "newer" in capsys.readouterr().out


def test_pick_session_cancel_on_enter(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    _saved(tmp_path)
    assert cli.pick_session("/projA", all_projects=False, input_fn=lambda _: "") is None


def test_pick_session_scopes_to_project_unless_all(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    _saved(tmp_path, sid="2026-07-01-0900-bbbb", project="/other", title="foreign")
    assert cli.pick_session("/projA", all_projects=False, input_fn=lambda _: "") is None
    got = cli.pick_session("/projA", all_projects=True, input_fn=lambda _: "1")
    assert got["id"] == "2026-07-01-0900-bbbb"
    assert "[other]" in capsys.readouterr().out  # project name shown in --all rows


def test_pick_session_none_when_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    assert cli.pick_session("/projA", all_projects=False, input_fn=lambda _: "1") is None
    assert "no saved sessions" in capsys.readouterr().out


def test_pick_session_cancel_on_interrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    _saved(tmp_path)

    def raise_interrupt(_):
        raise KeyboardInterrupt

    assert cli.pick_session("/projA", all_projects=False, input_fn=raise_interrupt) is None
