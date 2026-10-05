"""E58: glob took a ~/.luban pattern literally, searched a folder named "~" under the
project and answered "(no matches)" for a home file that exists; the agent then told the
user the file was missing. An absolute pattern raised instead of answering."""
from pathlib import Path

from luban import tools


def _ctx(root: Path, **kw):
    return tools.ToolContext(root, lambda p: True, lambda a, b, c: None, lambda c: None, **kw)


def _make_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "memory").mkdir(parents=True)
    (home / "OPINIONS.md").write_text("x", encoding="utf-8")
    (home / "memory" / "fact-a.md").write_text("x", encoding="utf-8")
    (home / "client_local.py").write_text("SECRET = 1\n", encoding="utf-8")
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    proj = tmp_path / "proj"
    proj.mkdir()
    return home, proj


def test_alias_pattern_finds_the_home_file(tmp_path, monkeypatch):
    _, proj = _make_home(tmp_path, monkeypatch)
    out = tools._glob({"pattern": "~/.luban/OPINIONS.md"}, _ctx(proj))
    assert not out.is_error and out.content.strip() == "~/.luban/OPINIONS.md"


def test_alias_recursive_pattern_lists_home_files_and_still_hides_home_python(tmp_path, monkeypatch):
    _, proj = _make_home(tmp_path, monkeypatch)
    out = tools._glob({"pattern": "~/.luban/**/*"}, _ctx(proj))
    assert "~/.luban/OPINIONS.md" in out.content
    assert "~/.luban/memory/fact-a.md" in out.content
    assert "client_local" not in out.content
    assert "not listed: 1 Python file" in out.content  # exclusion is not absence


def test_absolute_pattern_inside_the_project_works(tmp_path, monkeypatch):
    _, proj = _make_home(tmp_path, monkeypatch)
    (proj / "src").mkdir()
    (proj / "src" / "a.py").write_text("x")
    out = tools._glob({"pattern": str(proj.resolve() / "src" / "*.py")}, _ctx(proj))
    assert not out.is_error and out.content.strip() == "src/a.py"


def test_out_of_tree_pattern_is_refused_with_a_reason_not_no_matches(tmp_path, monkeypatch):
    _, proj = _make_home(tmp_path, monkeypatch)
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "x.md").write_text("x")
    out = tools._glob({"pattern": str(other / "*.md")}, _ctx(proj))
    assert out.is_error and "no matches" not in out.content


def test_out_of_tree_pattern_lists_when_the_config_allows_it(tmp_path, monkeypatch):
    _, proj = _make_home(tmp_path, monkeypatch)
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "x.md").write_text("x")
    out = tools._glob({"pattern": str(other / "*.md")}, _ctx(proj, allow_out_of_tree=True))
    assert not out.is_error and out.content.strip().endswith("x.md")


def test_a_missing_folder_is_an_error_not_an_empty_result(tmp_path, monkeypatch):
    _, proj = _make_home(tmp_path, monkeypatch)
    out = tools._glob({"pattern": "~/.luban/nope/*.md"}, _ctx(proj))
    assert out.is_error and "Path not found" in out.content


def test_relative_patterns_behave_as_before(tmp_path, monkeypatch):
    _, proj = _make_home(tmp_path, monkeypatch)
    (proj / "b.md").write_text("x")
    out = tools._glob({"pattern": "*.md"}, _ctx(proj))
    assert out.content.strip() == "b.md"
