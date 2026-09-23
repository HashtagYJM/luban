from pathlib import Path
from luban import tools


def _ctx(root: Path):
    return tools.ToolContext(root, lambda p: True, lambda a, b, c: None, lambda c: None)


def test_glob(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("x")
    out = tools._glob({"pattern": "**/*.py"}, _ctx(tmp_path))
    assert "a.py" in out.content and "sub/b.py" in out.content


def test_grep(tmp_path):
    (tmp_path / "a.py").write_text("hello world\nfoo\n")
    (tmp_path / "b.py").write_text("nothing\n")
    out = tools._grep({"pattern": "hello"}, _ctx(tmp_path))
    assert "a.py" in out.content and "1" in out.content
    assert "b.py" not in out.content


def test_glob_no_escape(tmp_path):
    (tmp_path / "inside.py").write_text("x")
    (tmp_path.parent / "outside_secret.py").write_text("x")
    out = tools._glob({"pattern": "../*.py"}, _ctx(tmp_path))
    assert "outside_secret.py" not in out.content
    assert ".." not in out.content


def test_grep_says_when_the_credential_guard_skipped_files(tmp_path, monkeypatch):
    """The guard is right to skip every .py under ~/.luban. Reporting the search as an
    ordinary no-match was not: an agent reads exclusion as absence (E53)."""
    home = tmp_path / "home"
    (home / "skills" / "s").mkdir(parents=True)
    (home / "skills" / "s" / "SKILL.md").write_text("import nothing\n", encoding="utf-8")
    (home / "skills" / "s" / "check.py").write_text("import os\n", encoding="utf-8")
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    ctx = tools.ToolContext(tmp_path / "proj", lambda p: True, lambda a, b, c: None,
                            lambda c: None, allow_out_of_tree=True)
    (tmp_path / "proj").mkdir()
    out = tools._grep({"pattern": "import", "path": str(home / "skills" / "s")}, ctx)
    assert "SKILL.md" in out.content
    assert "check.py" not in out.content.split("(not searched")[0]
    assert "not searched: 1 Python file" in out.content
    silent = tools._grep({"pattern": "zzz", "path": str(home / "skills" / "s")}, ctx)
    assert silent.content.startswith("(no matches)") and "not searched: 1" in silent.content


# --- Defect 2: recursive grep must classify each candidate file by its RESOLVED
# path, not the lexical path rglob() hands back — otherwise a symlink inside the
# project is a way to read anything the resolver would otherwise refuse. ---

def test_grep_symlink_to_protected_home_py_is_not_read(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "client_local.py").write_text("SECRET-CREDS import token\n", encoding="utf-8")
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    proj = tmp_path / "proj"
    proj.mkdir()
    link = proj / "link.py"
    try:
        link.symlink_to(home / "client_local.py")
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    ctx = _ctx(proj)
    out = tools._grep({"pattern": "SECRET"}, ctx)
    assert "SECRET-CREDS" not in out.content
    assert out.content.startswith("(no matches)")
    assert "not searched: 1 Python file" in out.content


def test_grep_symlink_to_outside_file_skipped_without_opt_in(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET\n", encoding="utf-8")
    link = proj / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    ctx = _ctx(proj)
    out = tools._grep({"pattern": "OUTSIDE"}, ctx)
    assert "OUTSIDE-SECRET" not in out.content
    assert "not searched: 1 file(s) linked outside the project" in out.content


def test_grep_symlink_to_outside_file_searched_with_opt_in(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET\n", encoding="utf-8")
    link = proj / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    ctx = tools.ToolContext(
        proj, lambda p: True, lambda a, b, c: None, lambda c: None, allow_out_of_tree=True
    )
    out = tools._grep({"pattern": "OUTSIDE"}, ctx)
    assert "OUTSIDE-SECRET" in out.content
    assert "linked outside the project" not in out.content


def test_grep_symlink_within_project_still_searched(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "real.txt"
    target.write_text("IN-PROJECT-HIT\n", encoding="utf-8")
    link = proj / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    ctx = _ctx(proj)
    out = tools._grep({"pattern": "IN-PROJECT-HIT"}, ctx)
    # both the real file and the in-project symlink are legitimate hits
    assert out.content.count("IN-PROJECT-HIT") == 2
    assert "not searched" not in out.content


def test_grep_symlink_to_home_py_still_blocked_with_opt_in(tmp_path, monkeypatch):
    """The out-of-tree opt-in must never lift the home .py guard."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "client_local.py").write_text("SECRET-CREDS\n", encoding="utf-8")
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    proj = tmp_path / "proj"
    proj.mkdir()
    link = proj / "link.py"
    try:
        link.symlink_to(home / "client_local.py")
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    ctx = tools.ToolContext(
        proj, lambda p: True, lambda a, b, c: None, lambda c: None, allow_out_of_tree=True
    )
    out = tools._grep({"pattern": "SECRET"}, ctx)
    assert "SECRET-CREDS" not in out.content
    assert "not searched: 1 Python file" in out.content


# --- Defect 3: _glob must apply the same home-.py guard as grep/resolve_tool_path. ---

def test_glob_excludes_protected_home_py_when_root_contains_home(tmp_path, monkeypatch):
    home = tmp_path / ".luban"
    home.mkdir()
    (home / "client_local.py").write_text("x", encoding="utf-8")
    (home / "SOUL.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    ctx = _ctx(tmp_path)
    out = tools._glob({"pattern": "**/*"}, ctx)
    assert "client_local.py" not in out.content
    assert "SOUL.md" in out.content
