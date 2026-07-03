from luban import permissions, tools


def _ctx(root, decide=None, audit=None, confirm=None):
    calls = []

    def default_confirm(prompt: str) -> bool:
        calls.append(prompt)
        return True

    ctx = tools.ToolContext(
        project_root=root,
        confirm=confirm or default_confirm,
        render_diff=lambda *a: None,
        render_command=lambda *a: None,
        decide=decide,
        audit=audit,
    )
    return ctx, calls


def _decide_with(action, reason=""):
    def decide(name, tool_input):
        return permissions.Decision(action, reason)

    return decide


def test_deny_short_circuits_with_reason(tmp_path):
    ctx, _ = _ctx(tmp_path, decide=_decide_with("deny", "blocked by deny rule: run_command:del *"))
    out = tools.run_tool("run_command", {"command": "del x"}, ctx)
    assert out.is_error
    assert "Blocked" in out.content and "del *" in out.content


def test_deny_blocks_write_before_execution(tmp_path):
    ctx, _ = _ctx(tmp_path, decide=_decide_with("deny", "blocked"))
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "x"}, ctx)
    assert out.is_error
    assert not (tmp_path / "a.txt").exists()


def test_allow_skips_confirm_on_mutating_tool(tmp_path):
    def never_confirm(prompt: str) -> bool:
        raise AssertionError("confirm must not be called when rule-allowed")

    ctx, _ = _ctx(tmp_path, decide=_decide_with("allow", "rule"), confirm=never_confirm)
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "hi"}, ctx)
    assert not out.is_error
    assert (tmp_path / "a.txt").read_text() == "hi"


def test_ask_still_confirms(tmp_path):
    ctx, calls = _ctx(tmp_path, decide=_decide_with("ask"))
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "hi"}, ctx)
    assert not out.is_error
    assert calls  # confirm consulted


def test_no_decide_is_legacy_behavior(tmp_path):
    ctx, calls = _ctx(tmp_path)
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "hi"}, ctx)
    assert not out.is_error and calls


def test_audit_receives_entries_including_deny(tmp_path):
    entries = []
    ctx, _ = _ctx(tmp_path, decide=_decide_with("deny", "nope"), audit=entries.append)
    tools.run_tool("run_command", {"command": "del x"}, ctx)
    assert entries[0]["tool"] == "run_command"
    assert entries[0]["decision"] == "deny_rule"
    assert entries[0]["target"] == "del x"
    assert entries[0]["is_error"] is True
