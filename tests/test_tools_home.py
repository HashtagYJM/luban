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
    secret.write_text("SUPERSECRETCONTENTS", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": str(secret)}, ctx)
    assert out.is_error and "SUPERSECRETCONTENTS" not in out.content
    # the refusal names the real home so the model can retry with the right alias
    assert str(home) in out.content and "~/.luban" in out.content


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


@pytest.mark.parametrize("bad", [
    "client_local.py ", "client_local.py.", "TOOLS_LOCAL.PY ", "tools_local.py.",
])
def test_trailing_dot_space_py_blocked(env, bad):
    home, proj, ctx = env
    # read attempt
    out = tools.run_tool("read_file", {"path": str(home / bad)}, ctx)
    assert out.is_error
    # write attempt must not create anything
    out = tools.run_tool("write_file", {"path": str(home / bad), "content": "x"}, ctx)
    assert out.is_error


@pytest.mark.parametrize("bad", ["audit.jsonl ", "audit.jsonl.", "AUDIT.JSONL "])
def test_trailing_dot_space_audit_write_blocked(env, bad):
    home, proj, ctx = env
    out = tools.run_tool("write_file", {"path": str(home / bad), "content": "x"}, ctx)
    assert out.is_error


# --- Defect 1: home guards must apply whenever the root CONTAINS the home, not just
# when the home tier resolves the path (Tier 2's "inside project root" return used to
# skip the .py/audit.jsonl guards entirely in this layout). ---

def _root_contains_home_env(tmp_path, monkeypatch, root_is_home=False, sibling=False):
    if sibling:
        base = tmp_path / "workspace"
        base.mkdir()
        home = base / "dot-luban"
        proj = base / "otherproject"
        home.mkdir()
        proj.mkdir()
    elif root_is_home:
        home = tmp_path / "home" / ".luban"
        home.mkdir(parents=True)
        proj = home
    else:
        # project root is a PARENT of the home (e.g. the home lives inside a
        # workspace the agent is also pointed at as its project).
        proj = tmp_path / "workspace"
        home = proj / ".luban"
        home.mkdir(parents=True)
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    monkeypatch.setenv("HOME", str(tmp_path / "unused_os_home"))
    ctx = tools.ToolContext(
        project_root=proj,
        confirm=lambda p: True,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
    )
    return home, proj, ctx


@pytest.mark.parametrize("path_kind", ["relative", "absolute", "alias"])
def test_home_py_guard_when_root_is_parent_of_home(tmp_path, monkeypatch, path_kind):
    home, proj, ctx = _root_contains_home_env(tmp_path, monkeypatch)
    (home / "client_local.py").write_text("SECRET-CREDS", encoding="utf-8")
    if path_kind == "relative":
        p = str((home / "client_local.py").relative_to(proj))
    elif path_kind == "absolute":
        p = str(home / "client_local.py")
    else:
        p = "~/.luban/client_local.py"
    out = tools.run_tool("read_file", {"path": p}, ctx)
    assert out.is_error and "SECRET-CREDS" not in out.content


def test_home_audit_guard_when_root_is_parent_of_home(tmp_path, monkeypatch):
    home, proj, ctx = _root_contains_home_env(tmp_path, monkeypatch)
    (home / "audit.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    # reading stays allowed
    out = tools.run_tool("read_file", {"path": str(home / "audit.jsonl")}, ctx)
    assert not out.is_error
    out = tools.run_tool("write_file", {"path": str(home / "audit.jsonl"), "content": ""}, ctx)
    assert out.is_error and (home / "audit.jsonl").read_text(encoding="utf-8") == '{"a":1}\n'


def test_home_py_guard_when_root_equals_home(tmp_path, monkeypatch):
    home, proj, ctx = _root_contains_home_env(tmp_path, monkeypatch, root_is_home=True)
    (home / "tools_local.py").write_text("import evil", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": "tools_local.py"}, ctx)
    assert out.is_error and "import evil" not in out.content
    out = tools.run_tool("write_file", {"path": "tools_local.py", "content": "x"}, ctx)
    assert out.is_error


def test_home_guards_when_root_is_sibling_of_home(tmp_path, monkeypatch):
    # Sibling layout: the home is not reachable through Tier 2 at all, only through
    # the alias/absolute-home tier — guards must still hold there (today's behavior,
    # unaffected by the Defect 1 fix, kept here as a regression check).
    home, proj, ctx = _root_contains_home_env(tmp_path, monkeypatch, sibling=True)
    (home / "client_local.py").write_text("SECRET-CREDS", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": str(home / "client_local.py")}, ctx)
    assert out.is_error and "SECRET-CREDS" not in out.content
    # an ordinary project .py file outside the home stays usable
    (proj / "app.py").write_text("print('hi')", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": "app.py"}, ctx)
    assert not out.is_error and "print('hi')" in out.content


def test_ordinary_project_py_file_stays_usable_when_root_contains_home(tmp_path, monkeypatch):
    home, proj, ctx = _root_contains_home_env(tmp_path, monkeypatch)
    (proj / "app.py").write_text("print('ok')", encoding="utf-8")
    out = tools.run_tool("read_file", {"path": "app.py"}, ctx)
    assert not out.is_error and "print('ok')" in out.content


def test_symlink_in_project_to_protected_home_py_refused_by_read_file(tmp_path, monkeypatch):
    home, proj, ctx = _root_contains_home_env(tmp_path, monkeypatch)
    (home / "client_local.py").write_text("SECRET-CREDS", encoding="utf-8")
    link = proj / "link.py"
    try:
        link.symlink_to(home / "client_local.py")
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    out = tools.run_tool("read_file", {"path": "link.py"}, ctx)
    assert out.is_error and "SECRET-CREDS" not in out.content


def test_symlink_in_project_to_protected_home_py_refused_by_edit_file(tmp_path, monkeypatch):
    home, proj, ctx = _root_contains_home_env(tmp_path, monkeypatch)
    (home / "client_local.py").write_text("SECRET-CREDS", encoding="utf-8")
    link = proj / "link.py"
    try:
        link.symlink_to(home / "client_local.py")
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    out = tools.run_tool(
        "edit_file", {"path": "link.py", "old_string": "SECRET", "new_string": "x"}, ctx
    )
    assert out.is_error


def test_symlink_in_project_to_outside_file_refused_by_read_file_without_opt_in(
    tmp_path, monkeypatch
):
    home, proj, ctx = _root_contains_home_env(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("OUTSIDE-CONTENTS", encoding="utf-8")
    link = proj / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    out = tools.run_tool("read_file", {"path": "link.txt"}, ctx)
    assert out.is_error and "OUTSIDE-CONTENTS" not in out.content
