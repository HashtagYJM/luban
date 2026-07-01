from pathlib import Path
from luban import tools


def _ctx(root: Path, confirm_value: bool, calls: list):
    return tools.ToolContext(
        project_root=root,
        confirm=lambda prompt: confirm_value,
        render_diff=lambda path, old, new: calls.append((path, old, new)),
        render_command=lambda cmd: None,
    )


def test_write_file_confirmed(tmp_path):
    calls = []
    out = tools._write_file({"path": "f.py", "content": "hi\n"}, _ctx(tmp_path, True, calls))
    assert not out.is_error
    assert (tmp_path / "f.py").read_text() == "hi\n"
    assert calls  # diff was rendered


def test_write_file_declined(tmp_path):
    out = tools._write_file({"path": "f.py", "content": "hi\n"}, _ctx(tmp_path, False, []))
    assert "declined" in out.content.lower()
    assert not (tmp_path / "f.py").exists()


def test_edit_file_unique_match(tmp_path):
    (tmp_path / "f.py").write_text("a\nOLD\nb\n")
    out = tools._edit_file(
        {"path": "f.py", "old_string": "OLD", "new_string": "NEW"},
        _ctx(tmp_path, True, []),
    )
    assert not out.is_error
    assert (tmp_path / "f.py").read_text() == "a\nNEW\nb\n"


def test_edit_file_no_match(tmp_path):
    (tmp_path / "f.py").write_text("a\n")
    out = tools._edit_file(
        {"path": "f.py", "old_string": "ZZZ", "new_string": "N"},
        _ctx(tmp_path, True, []),
    )
    assert out.is_error


def test_edit_file_ambiguous(tmp_path):
    (tmp_path / "f.py").write_text("x\nx\n")
    out = tools._edit_file(
        {"path": "f.py", "old_string": "x", "new_string": "y"},
        _ctx(tmp_path, True, []),
    )
    assert out.is_error and "unique" in out.content.lower()
