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
