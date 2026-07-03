from luban import cli, skills


def _session(project):
    return cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       project=str(project))


def _mk(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


def test_skills_command_lists(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "proj" / ".luban" / "skills", "conv", "description: conventions\n\nB.")
    assert cli.handle_command("/skills", _session(tmp_path / "proj")) == "handled"
    out = capsys.readouterr().out
    assert "conv: conventions" in out and "[project]" in out


def test_skills_command_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "absent")
    cli.handle_command("/skills", _session(tmp_path / "proj"))
    assert "no skills found" in capsys.readouterr().out


def test_skill_command_queues_body(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "style", "description: s\n\nUse ruff.")
    s = _session(tmp_path / "proj")
    cli.handle_command("/skill style", s)
    assert s.pending_context == ["[skill: style]\nUse ruff."]
    assert "queued" in capsys.readouterr().out


def test_skill_command_unknown_lists_available(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "style", "description: s\n\nB.")
    s = _session(tmp_path / "proj")
    cli.handle_command("/skill nope", s)
    assert s.pending_context == []
    out = capsys.readouterr().out
    assert "unknown skill: nope" in out and "style" in out


def test_skill_command_no_arg_usage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "absent")
    cli.handle_command("/skill", _session(tmp_path / "proj"))
    assert "usage: /skill" in capsys.readouterr().out


def test_compose_prepends_and_drains():
    s = _session("/p")
    s.pending_context = ["[skill: a]\nA body"]
    got = cli.compose_user_message(s, "do the thing")
    assert got == "[skill: a]\nA body\n\ndo the thing"
    assert s.pending_context == []


def test_compose_passthrough_without_pending():
    s = _session("/p")
    assert cli.compose_user_message(s, "hi") == "hi"
