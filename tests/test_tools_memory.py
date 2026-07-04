from pathlib import Path

import pytest

from luban import memory, tools


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    return tmp_path


def make_ctx(tmp_path, confirm=lambda p: True, decide=None):
    return tools.ToolContext(
        project_root=tmp_path,
        confirm=confirm,
        render_diff=lambda *a: None,
        render_command=lambda *a: None,
        decide=decide,
    )


def test_remember_dispatch_writes_fact(mem, tmp_path):
    out = tools.run_tool(
        "remember",
        {"name": "pref-x", "description": "d", "body": "b"},
        make_ctx(tmp_path),
    )
    assert not out.is_error
    assert (mem / "memory" / "pref-x.md").exists()


def test_remember_declined_writes_nothing(mem, tmp_path):
    out = tools.run_tool(
        "remember",
        {"name": "pref-x", "description": "d", "body": "b"},
        make_ctx(tmp_path, confirm=lambda p: False),
    )
    assert not out.is_error and "declined" in out.content.lower()
    assert not (mem / "memory" / "pref-x.md").exists()


def test_remember_invalid_slug_no_confirm(mem, tmp_path):
    def boom(prompt):
        raise AssertionError("confirm must not be called for invalid input")
    out = tools.run_tool(
        "remember", {"name": "../evil", "description": "d", "body": "b"},
        make_ctx(tmp_path, confirm=boom),
    )
    assert out.is_error


def test_remember_renders_diff(mem, tmp_path):
    seen = []
    ctx = tools.ToolContext(
        project_root=tmp_path, confirm=lambda p: True,
        render_diff=lambda *a: seen.append(a), render_command=lambda *a: None,
    )
    tools.run_tool("remember", {"name": "f", "description": "d", "body": "b"}, ctx)
    assert seen and "f.md" in seen[0][0]


def test_forget_dispatch(mem, tmp_path):
    memory.remember("f1", "d", "b")
    out = tools.run_tool("forget", {"name": "f1"}, make_ctx(tmp_path))
    assert not out.is_error
    assert not (mem / "memory" / "f1.md").exists()


def test_forget_missing_is_error(mem, tmp_path):
    out = tools.run_tool("forget", {"name": "ghost"}, make_ctx(tmp_path))
    assert out.is_error


def test_recall_needs_no_confirm(mem, tmp_path):
    memory.remember("f1", "d", "needle in fact")
    def boom(prompt):
        raise AssertionError("recall must not ask")
    out = tools.run_tool("recall", {"query": "needle"}, make_ctx(tmp_path, confirm=boom))
    assert "needle in fact" in out.content


def test_journal_dispatch(mem, tmp_path):
    out = tools.run_tool("journal", {"text": "note this"}, make_ctx(tmp_path))
    assert not out.is_error
    import datetime as dt
    path = mem / "memory" / "journal" / f"{dt.date.today().isoformat()}.md"
    assert "note this" in path.read_text(encoding="utf-8")


def test_deny_rule_blocks_remember(mem, tmp_path):
    from luban import permissions
    def decide(name, inp):
        return permissions.evaluate(name, inp, [], ["remember"], read_only=False)
    out = tools.run_tool(
        "remember", {"name": "x", "description": "d", "body": "b"},
        make_ctx(tmp_path, decide=decide),
    )
    assert out.is_error and "Blocked" in out.content
    assert not (mem / "memory" / "x.md").exists()


def test_recall_is_read_only():
    assert "recall" in tools.READ_ONLY_TOOLS


def test_active_tools_filter():
    names_on = {t["name"] for t in tools.active_tools(True)}
    names_off = {t["name"] for t in tools.active_tools(False)}
    assert tools.MEMORY_TOOL_NAMES <= names_on
    assert not (tools.MEMORY_TOOL_NAMES & names_off)
    assert "read_file" in names_off
