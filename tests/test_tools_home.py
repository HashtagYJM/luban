import pytest

from luban import tools


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".luban"
    home.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # so "~" expands into the fixture
    ctx = tools.ToolContext(
        project_root=proj,
        confirm=lambda p: True,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
    )
    return home, proj, ctx


def test_read_file_in_luban_home(env):
    home, proj, ctx = env
    (home / "SOUL.md").write_text("soul body", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": str(home / "SOUL.md")}, ctx)
    assert not out.is_error and "soul body" in out.content


def test_tilde_path_resolves_to_luban_home(env):
    home, proj, ctx = env
    (home / "SOUL.md").write_text("tilde works", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": "~/.luban/SOUL.md"}, ctx)
    assert not out.is_error and "tilde works" in out.content


def test_write_and_edit_file_in_luban_home(env):
    home, proj, ctx = env
    target = home / "memory" / "enhancements.md"
    out = tools.run_tool("write_file", {"path": str(target), "content": "## Open\n"}, ctx)
    assert not out.is_error and target.read_text(encoding="utf-8") == "## Open\n"
    out = tools.run_tool(
        "edit_file",
        {"path": str(target), "old_string": "Open", "new_string": "Resolved"},
        ctx,
    )
    assert not out.is_error and "Resolved" in target.read_text(encoding="utf-8")


def test_list_dir_in_luban_home(env):
    home, proj, ctx = env
    (home / "skills").mkdir()
    out = tools.run_tool("list_dir", {"path": str(home)}, ctx)
    assert not out.is_error and "skills/" in out.content


def test_python_files_blocked_read_and_write(env):
    home, proj, ctx = env
    (home / "client_local.py").write_text("SECRET-CREDS", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": str(home / "client_local.py")}, ctx)
    assert out.is_error and "SECRET-CREDS" not in out.content
    out = tools.run_tool(
        "write_file", {"path": str(home / "tools_local.py"), "content": "x"}, ctx
    )
    assert out.is_error
    assert not (home / "tools_local.py").exists()


def test_audit_log_read_only(env):
    home, proj, ctx = env
    (home / "audit.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    assert not tools.run_tool("read_file", {"path": str(home / "audit.jsonl")}, ctx).is_error
    out = tools.run_tool("write_file", {"path": str(home / "audit.jsonl"), "content": ""}, ctx)
    assert out.is_error and (home / "audit.jsonl").read_text(encoding="utf-8") == '{"a":1}\n'


def test_absolute_outside_home_rejected(env, tmp_path):
    home, proj, ctx = env
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": str(secret)}, ctx)
    assert out.is_error and "no" not in out.content


def test_prefix_sibling_rejected(env):
    home, proj, ctx = env
    evil = home.parent / ".lubanevil"
    evil.mkdir()
    (evil / "x.txt").write_text("no", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": str(evil / "x.txt")}, ctx)
    assert out.is_error


def test_symlink_escape_rejected(env, tmp_path):
    home, proj, ctx = env
    outside = tmp_path / "outside.txt"
    outside.write_text("no", encoding="utf-8")
    link = home / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    out = tools.run_tool("read_file", {"path": str(link)}, ctx)
    assert out.is_error


def test_relative_paths_still_project_jailed(env):
    home, proj, ctx = env
    (proj / "a.txt").write_text("proj", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": "a.txt"}, ctx)
    assert not out.is_error and "proj" in out.content
    out = tools.run_tool("read_file", {"path": "../secret"}, ctx)
    assert out.is_error


def test_python_files_blocked_case_insensitive(env):
    home, proj, ctx = env
    out = tools.run_tool(
        "write_file", {"path": str(home / "TOOLS_LOCAL.PY"), "content": "EVIL"}, ctx
    )
    assert out.is_error
    assert not (home / "TOOLS_LOCAL.PY").exists()
    out = tools.run_tool(
        "read_file", {"path": str(home / "Client_Local.Py")}, ctx
    )
    assert out.is_error


def test_audit_log_write_blocked_case_insensitive(env):
    home, proj, ctx = env
    out = tools.run_tool(
        "write_file", {"path": str(home / "AUDIT.JSONL"), "content": "x"}, ctx
    )
    assert out.is_error
    assert not (home / "AUDIT.JSONL").exists()


def test_write_into_luban_home_still_confirms(env):
    home, proj, ctx = env
    from dataclasses import replace
    declined = replace(ctx, confirm=lambda p: False)
    out = tools.run_tool(
        "write_file", {"path": str(home / "note.md"), "content": "x"}, declined
    )
    assert "declined" in out.content and not (home / "note.md").exists()
