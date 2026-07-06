from luban import sessions, tools


def _ctx(project):
    def no_confirm(prompt):
        raise AssertionError("read-only tool must not ask to confirm")

    return tools.ToolContext(
        project_root=project,
        confirm=no_confirm,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
    )


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "s")
    proj = tmp_path / "proj"
    proj.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    sessions.save({"id": "a1", "project": str(proj), "created": "c",
                   "model": "m", "title": "mine", "messages": []})
    sessions.save({"id": "b2", "project": str(other), "created": "c",
                   "model": "m", "title": "theirs", "messages": []})
    return proj


def test_sessions_lists_current_project_only(tmp_path, monkeypatch):
    proj = _seed(tmp_path, monkeypatch)
    out = tools.run_tool("sessions", {}, _ctx(proj))
    assert not out.is_error
    assert "mine" in out.content and "a1" in out.content
    assert "theirs" not in out.content


def test_sessions_all_projects(tmp_path, monkeypatch):
    proj = _seed(tmp_path, monkeypatch)
    out = tools.run_tool("sessions", {"all": True}, _ctx(proj))
    assert "mine" in out.content and "theirs" in out.content
    assert "[other]" in out.content  # project tag shown in all mode


def test_sessions_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "absent")
    proj = tmp_path / "proj"
    proj.mkdir()
    out = tools.run_tool("sessions", {}, _ctx(proj))
    assert not out.is_error and out.content == "(no saved sessions)"


def test_sessions_is_read_only_and_offered():
    assert "sessions" in tools.READ_ONLY_TOOLS
    assert any(t["name"] == "sessions" for t in tools.active_tools(memory_enabled=False))
