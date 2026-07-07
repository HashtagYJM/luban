"""v0.5.6: UTF-8/atomic writes (E7/E8), grep out-of-scope errors (E4a),
recall token matching (E6)."""
import pytest

from luban import memory, tools


def _ctx(root):
    return tools.ToolContext(
        project_root=root,
        confirm=lambda p: True,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
    )


# ---- A1: E7/E8 UTF-8 + atomic writes ----

def test_write_file_non_cp1252_char_succeeds_as_utf8(tmp_path):
    """The crash repro: writing a → (U+2192) must not raise; lands as UTF-8."""
    ctx = _ctx(tmp_path)
    out = tools.run_tool(
        "write_file", {"path": "notes.md", "content": "Issue → fix — done ✓"}, ctx
    )
    assert not out.is_error, out.content
    f = tmp_path / "notes.md"
    assert f.read_bytes().decode("utf-8") == "Issue → fix — done ✓"


def test_edit_file_non_cp1252_char_succeeds(tmp_path):
    (tmp_path / "f.md").write_text("alpha", encoding="utf-8")
    ctx = _ctx(tmp_path)
    out = tools.run_tool(
        "edit_file",
        {"path": "f.md", "old_string": "alpha", "new_string": "α → β"},
        ctx,
    )
    assert not out.is_error, out.content
    assert (tmp_path / "f.md").read_text(encoding="utf-8") == "α → β"


def test_failed_write_leaves_original_intact(tmp_path, monkeypatch):
    """Atomic guarantee: if the write blows up, the existing file is NOT truncated."""
    target = tmp_path / "keep.md"
    target.write_text("original precious content", encoding="utf-8")
    ctx = _ctx(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(tools.Path, "write_text", boom)

    out = tools.run_tool("write_file", {"path": "keep.md", "content": "new"}, ctx)
    assert out.is_error and "Could not write" in out.content
    # original survived — no 0-byte truncation
    assert target.read_text(encoding="utf-8") == "original precious content"


def test_write_error_does_not_raise(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        tools.Path, "write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    # must return an error result, never propagate (that was the crash)
    out = tools.run_tool("write_file", {"path": "x.md", "content": "y"}, ctx)
    assert out.is_error


# ---- A2: E4a grep on unsearchable paths ----

def test_grep_nonexistent_path_errors(tmp_path):
    ctx = _ctx(tmp_path)
    out = tools.run_tool("grep", {"pattern": "x", "path": "does/not/exist"}, ctx)
    assert out.is_error and "not found" in out.content.lower()


def test_grep_out_of_scope_path_errors(tmp_path):
    ctx = _ctx(tmp_path)
    out = tools.run_tool("grep", {"pattern": "x", "path": "../escape"}, ctx)
    assert out.is_error  # not a silent "(no matches)"


def test_grep_genuine_miss_still_no_matches(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    ctx = _ctx(tmp_path)
    out = tools.run_tool("grep", {"pattern": "zzz", "path": "."}, ctx)
    assert not out.is_error and "(no matches)" in out.content


# ---- A3: E6 recall token matching ----

@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    (tmp_path / "memory").mkdir()
    return tmp_path / "memory"


def test_recall_multiword_matches_hyphenated_slug(mem):
    memory.remember("yjm-coding-style", "prefs", "Prefers a terse coding style overall.")
    assert "yjm-coding-style" in memory.recall("coding style")


def test_recall_scattered_tokens_match(mem):
    memory.remember("windows-file-tool-gotchas", "d", "On Windows, unicode writes can wipe a file.")
    out = memory.recall("windows unicode wipe file")
    assert "windows-file-tool-gotchas" in out


def test_recall_contiguous_substring_still_works(mem):
    memory.remember("env-truth", "d", "The home box uses uv and Python 3.13.")
    assert "env-truth" in memory.recall("uv and Python")


def test_recall_true_miss_returns_no_matches(mem):
    memory.remember("k", "d", "body about apples")
    assert memory.recall("quantum chromodynamics") == "(no matches)"
