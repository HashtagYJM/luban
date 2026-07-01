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
