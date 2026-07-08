"""v0.5.8 — E14 (resume/tool_use invariant), E12 (UTF-8 policy), E16 (out-of-tree)."""
import re
from pathlib import Path

import pytest

from luban import agent, cli, tools


# ================= E14: sanitize_history invariant =================

def _asst(*blocks):
    return {"role": "assistant", "content": list(blocks)}


def _tu(id="t1"):
    return {"type": "tool_use", "id": id, "name": "x", "input": {}}


def _text(t="hi"):
    return {"type": "text", "text": t}


def test_sanitize_drops_trailing_toolonly_assistant():
    msgs = [{"role": "user", "content": "hi"}, _asst(_tu())]
    out = agent.sanitize_history(msgs)
    assert out == [{"role": "user", "content": "hi"}]  # dangling tool_use removed


def test_sanitize_keeps_text_strips_tool_use():
    msgs = [{"role": "user", "content": "hi"}, _asst(_text("partial"), _tu())]
    out = agent.sanitize_history(msgs)
    assert out[-1]["content"] == [_text("partial")]  # text kept, tool_use gone


def test_sanitize_leaves_valid_history_untouched():
    msgs = [
        {"role": "user", "content": "hi"},
        _asst(_tu("a")),
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a",
                                       "content": "ok", "is_error": False}]},
        _asst(_text("done")),
    ]
    assert agent.sanitize_history(msgs) == msgs  # already valid


def test_sanitize_handles_multiple_trailing_dangling():
    msgs = [{"role": "user", "content": "hi"}, _asst(_text("t")), _asst(_tu())]
    out = agent.sanitize_history(msgs)
    assert out[-1]["content"] == [_text("t")] and len(out) == 2


def test_sanitize_empty_and_string_content_safe():
    assert agent.sanitize_history([]) == []
    msgs = [_asst("just text")]  # string content, no blocks
    assert agent.sanitize_history(msgs) == msgs


def test_restore_repairs_corrupted_session(monkeypatch):
    """The real bug: a saved file ending in an unanswered tool_use resumes clean."""
    sess = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       messages=[], project="p", title="t")
    data = {"messages": [{"role": "user", "content": "hi"}, _asst(_tu())],
            "id": "sid", "model": "m"}
    monkeypatch.setattr(cli.ui, "print_text", lambda *a, **k: None)
    cli.restore_session(sess, data)
    assert sess.messages == [{"role": "user", "content": "hi"}]  # repaired, no crash


def test_run_turn_sanitizes_max_tokens_truncated_tool_use(monkeypatch):
    """A max_tokens stop carrying a tool_use block returns valid history."""
    import types

    class Stub:
        def __init__(s): s.messages = s
        def create(s, **k):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="…"),
                         types.SimpleNamespace(type="tool_use", id="t", name="read_file", input={})],
                stop_reason="max_tokens",
            )
    monkeypatch.setattr(agent.client_mod, "message_to_blocks",
                        lambda msg: [{"type": "text", "text": "…"}, _tu("t")])
    cfg = agent.AgentConfig("m", 50, stream=False, platform="mac")
    out = agent.run_turn(Stub(), cfg, [{"role": "user", "content": "hi"}], None, lambda t: None)
    assert not any(b.get("type") == "tool_use"
                   for m in out if isinstance(m.get("content"), list) for b in m["content"]
                   if m is out[-1])  # tail has no dangling tool_use


# ================= E12: UTF-8 policy =================

def test_read_file_reads_utf8(tmp_path):
    (tmp_path / "u.md").write_text("arrow → 中文 —", encoding="utf-8")
    ctx = tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                            render_diff=lambda *a: None, render_command=lambda c: None)
    out = tools.run_tool("read_file", {"path": "u.md"}, ctx)
    assert not out.is_error and "→ 中文 —" in out.content


def test_roundtrip_write_read_grep_all_agree(tmp_path):
    ctx = tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                            render_diff=lambda *a: None, render_command=lambda c: None)
    marker = "signal_→_中_marker"
    assert not tools.run_tool("write_file", {"path": "f.md", "content": marker}, ctx).is_error
    assert marker in tools.run_tool("read_file", {"path": "f.md"}, ctx).content
    assert "f.md" in tools.run_tool("grep", {"pattern": "signal_", "path": "."}, ctx).content


def test_configure_utf8_io_is_idempotent_and_safe():
    cli.configure_utf8_io()  # must not raise even when already utf-8 / on non-reconfigurable streams
    cli.configure_utf8_io()


def test_policy_all_file_io_pins_encoding():
    """Anti-whack-a-mole: every read_text/write_text/open in luban/ pins encoding."""
    src_dir = Path(cli.__file__).parent
    offenders = []
    pattern = re.compile(r"\.(?:read_text|write_text)\s*\(|(?<![\w.])open\s*\(")
    for py in src_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            window = text[m.start():m.start() + 200]  # same call spans a few lines
            # find the matching close paren region cheaply: check next 200 chars
            if "encoding=" not in window:
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{py.name}:{line}")
    assert not offenders, f"file I/O without encoding=: {offenders}"


# ================= E16: out-of-tree edits =================

@pytest.fixture()
def tree(tmp_path):
    proj = tmp_path / "proj"; proj.mkdir()
    sibling = tmp_path / "sibling"; sibling.mkdir()
    (sibling / "data.py").write_text("x = 1\n", encoding="utf-8")
    return proj, sibling


def test_out_of_tree_denied_by_default(tree):
    proj, sibling = tree
    with pytest.raises(ValueError):
        tools.resolve_tool_path(proj, str(sibling / "data.py"), writing=True)


def test_out_of_tree_allowed_when_enabled(tree):
    proj, sibling = tree
    got = tools.resolve_tool_path(
        proj, str(sibling / "data.py"), writing=True, allow_out_of_tree=True
    )
    assert got == (sibling / "data.py").resolve()  # sibling .py editable (the repro)


def test_out_of_tree_write_end_to_end_when_enabled(tree):
    proj, sibling = tree
    ctx = tools.ToolContext(project_root=proj, confirm=lambda p: True,
                            render_diff=lambda *a: None, render_command=lambda c: None,
                            allow_out_of_tree=True)
    out = tools.run_tool(
        "edit_file",
        {"path": str(sibling / "data.py"), "old_string": "x = 1", "new_string": "x = 2 → 中"},
        ctx,
    )
    assert not out.is_error, out.content
    assert (sibling / "data.py").read_text(encoding="utf-8") == "x = 2 → 中\n"


def test_absolute_in_project_path_allowed(tree):
    proj, _ = tree
    got = tools.resolve_tool_path(proj, str(proj / "sub" / "f.md"), writing=True)
    assert got == (proj / "sub" / "f.md").resolve()  # in-tree absolute is fine


def test_luban_home_py_guard_still_blocks_even_with_toggle(tree, monkeypatch, tmp_path):
    proj, _ = tree
    home = tmp_path / "home" / ".luban"; home.mkdir(parents=True)
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    with pytest.raises(ValueError):  # ~/.luban .py stays off-limits regardless
        tools.resolve_tool_path(proj, str(home / "tools_local.py"),
                                writing=True, allow_out_of_tree=True)
