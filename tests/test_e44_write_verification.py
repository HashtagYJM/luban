"""E44 — a confirmed diff is not proof of the bytes on disk.

`edit_file` reported success when `os.replace` did not raise. That is a claim about the
write CALL, not about the file, and everything below luban — a newline translation, an
editor holding the file open, a sync client, a filter driver — can change the content
with nothing raised. The field symptom was content missing from a confirmed insert, found
only when a LATER edit failed with `old_string not found`.

These tests state the invariant: a write tool reports success only about bytes it has
read back.
"""
from pathlib import Path

import pytest

from luban import paths, tools


def _ctx(root: Path):
    return tools.ToolContext(
        project_root=root,
        confirm=lambda prompt: True,
        render_diff=lambda path, old, new: None,
        render_command=lambda cmd: None,
    )


def _corrupting_writer(drop: str):
    """A writer that silently drops `drop` from what it was handed."""
    def write(target: Path, text: str) -> None:
        paths.atomic_write_text(target, text.replace(drop, ""))
    return write


def test_edit_reports_error_when_the_file_is_not_what_was_asked_for(tmp_path, monkeypatch):
    (tmp_path / "f.py").write_text("a\nOLD\nb\n")
    monkeypatch.setattr(tools, "_atomic_write_text", _corrupting_writer('"""doc"""\n'))
    out = tools._edit_file(
        {"path": "f.py", "old_string": "OLD", "new_string": 'def f():\n    """doc"""\n    pass'},
        _ctx(tmp_path),
    )
    assert out.is_error, "a write whose bytes were never checked must not report success"
    assert "f.py" in out.content


def test_write_reports_error_when_the_file_is_not_what_was_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_atomic_write_text", _corrupting_writer("two\n"))
    out = tools._write_file({"path": "f.py", "content": "one\ntwo\nthree\n"}, _ctx(tmp_path))
    assert out.is_error
    assert "f.py" in out.content


def test_the_deviation_names_where_the_file_diverges(tmp_path, monkeypatch):
    """A report that only says 'it differs' sends the reader back to a full diff."""
    monkeypatch.setattr(tools, "_atomic_write_text", _corrupting_writer("two\n"))
    out = tools._write_file({"path": "f.py", "content": "one\ntwo\nthree\n"}, _ctx(tmp_path))
    assert "2" in out.content  # the first differing line number


def test_a_clean_write_still_reports_success(tmp_path):
    (tmp_path / "f.py").write_text("a\nOLD\nb\n")
    out = tools._edit_file(
        {"path": "f.py", "old_string": "OLD", "new_string": "NEW"}, _ctx(tmp_path)
    )
    assert not out.is_error
    assert (tmp_path / "f.py").read_text() == "a\nNEW\nb\n"


def test_verification_ignores_the_platform_newline_translation(tmp_path):
    """CRLF on disk is not a deviation.

    `Path.write_text` translates "\\n" to the platform separator, so on Windows the bytes
    on disk are NEVER the string handed to the tool. Verification compares what the next
    `edit_file` will match `old_string` against — text read with universal newlines — so a
    line-ending convention is invisible to it by design. Otherwise every Windows write
    would report a deviation and the signal would be worth nothing.
    """
    target = tmp_path / "f.py"
    target.write_bytes(b"one\r\ntwo\r\n")
    assert paths.written_deviation(target, "one\ntwo\n") == ""


def test_written_deviation_reports_a_missing_line(tmp_path):
    target = tmp_path / "f.py"
    target.write_text("one\nthree\n")
    dev = paths.written_deviation(target, "one\ntwo\nthree\n")
    assert dev
    assert "2" in dev


def test_written_deviation_reports_a_file_that_vanished(tmp_path):
    dev = paths.written_deviation(tmp_path / "gone.py", "x\n")
    assert dev


@pytest.mark.parametrize("text", ["", "x", "a\nb\nc\n"])
def test_written_deviation_is_empty_for_an_honest_write(tmp_path, text):
    target = tmp_path / "f.txt"
    paths.atomic_write_text(target, text)
    assert paths.written_deviation(target, text) == ""
