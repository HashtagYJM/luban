from pathlib import Path
import pytest
from luban import tools


def _ctx(root: Path):
    return tools.ToolContext(
        project_root=root,
        confirm=lambda prompt: True,
        render_diff=lambda path, old, new: None,
        render_command=lambda cmd: None,
    )


def test_resolve_in_root_ok(tmp_path):
    assert tools.resolve_in_root(tmp_path, "a/b.py") == (tmp_path / "a/b.py")


def test_resolve_in_root_escape(tmp_path):
    with pytest.raises(ValueError):
        tools.resolve_in_root(tmp_path, "../secret")


def test_list_dir(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    out = tools._list_dir({"path": "."}, _ctx(tmp_path))
    assert not out.is_error
    assert "a.py" in out.content and "sub" in out.content


def test_read_file(tmp_path):
    (tmp_path / "f.py").write_text("line1\nline2\n")
    out = tools._read_file({"path": "f.py"}, _ctx(tmp_path))
    assert "line1" in out.content and "line2" in out.content


def test_read_file_missing(tmp_path):
    out = tools._read_file({"path": "nope.py"}, _ctx(tmp_path))
    assert out.is_error


def test_read_file_bad_range(tmp_path):
    (tmp_path / "f.py").write_text("a\nb\n")
    out = tools._read_file({"path": "f.py", "start": "bad"}, _ctx(tmp_path))
    assert out.is_error


def test_read_file_start_zero_clamped(tmp_path):
    (tmp_path / "f.py").write_text("a\nb\n")
    out = tools._read_file({"path": "f.py", "start": 0}, _ctx(tmp_path))
    assert not out.is_error
    assert out.content.startswith("1:")
