import pytest

from luban import permissions, tools


@pytest.fixture(autouse=True)
def _isolate():
    yield
    tools.reset_custom()


def _ctx(tmp_path, confirm=lambda p: True, decide=None):
    rendered = []
    return rendered, tools.ToolContext(
        project_root=tmp_path,
        confirm=confirm,
        render_diff=lambda p, o, n: None,
        render_command=rendered.append,
        decide=decide,
    )


def _spec(**over):
    spec = {
        "name": "shout",
        "description": "Uppercase text.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "handler": lambda inp, root: inp["text"].upper(),
    }
    spec.update(over)
    return spec


def test_register_and_dispatch_mutating(tmp_path):
    assert tools.register_custom([_spec()]) == ["shout"]
    rendered, ctx = _ctx(tmp_path)
    out = tools.run_tool("shout", {"text": "hi"}, ctx)
    assert out.content == "HI" and not out.is_error
    assert rendered and "shout(" in rendered[0]  # command preview rendered
    assert any(t["name"] == "shout" for t in tools.TOOLS)


def test_mutating_declined(tmp_path):
    tools.register_custom([_spec()])
    _, ctx = _ctx(tmp_path, confirm=lambda p: False)
    out = tools.run_tool("shout", {"text": "hi"}, ctx)
    assert "declined" in out.content and not out.is_error


def test_read_only_skips_confirm(tmp_path):
    def deny_confirm(prompt):
        raise AssertionError("confirm must not be called for read_only tools")

    tools.register_custom([_spec(read_only=True)])
    assert "shout" in tools.READ_ONLY_TOOLS
    _, ctx = _ctx(tmp_path, confirm=deny_confirm)
    assert tools.run_tool("shout", {"text": "ok"}, ctx).content == "OK"


def test_handler_exception_is_tool_error(tmp_path):
    def boom(inp, root):
        raise RuntimeError("db down")

    tools.register_custom([_spec(handler=boom)])
    _, ctx = _ctx(tmp_path)
    out = tools.run_tool("shout", {"text": "x"}, ctx)
    assert out.is_error and "Tool error: db down" in out.content


def test_builtin_collision_skipped(tmp_path, capsys):
    assert tools.register_custom([_spec(name="read_file")]) == []
    assert "collides" in capsys.readouterr().err
    _, ctx = _ctx(tmp_path)  # built-in still intact
    (tmp_path / "a.txt").write_text("x")
    assert not tools.run_tool("read_file", {"path": "a.txt"}, ctx).is_error


def test_permission_target_and_deny_rule(tmp_path):
    tools.register_custom([_spec(name="query_sql", permission_target="sql")])
    assert permissions.target_of("query_sql", {"sql": "DROP TABLE x"}) == "DROP TABLE x"

    def decide(name, inp):
        return permissions.evaluate(name, inp, [], ["query_sql:DROP*"],
                                    read_only=name in tools.READ_ONLY_TOOLS)

    _, ctx = _ctx(tmp_path, decide=decide)
    out = tools.run_tool("query_sql", {"sql": "DROP TABLE x"}, ctx)
    assert out.is_error and "Blocked" in out.content


def test_output_coerced_and_truncated(tmp_path):
    tools.register_custom([
        _spec(name="big", handler=lambda inp, root: "y" * (tools.MAX_OUTPUT + 100)),
        _spec(name="dicty", handler=lambda inp, root: {"a": 1}),
    ])
    _, ctx = _ctx(tmp_path)
    big = tools.run_tool("big", {}, ctx).content
    assert "[truncated" in big
    assert tools.run_tool("dicty", {}, ctx).content == "{'a': 1}"


def test_active_tools_includes_custom_even_memory_disabled(tmp_path):
    tools.register_custom([_spec()])
    names = {t["name"] for t in tools.active_tools(memory_enabled=False)}
    assert "shout" in names and "remember" not in names


def test_reset_custom_removes_everything(tmp_path):
    tools.register_custom([_spec(read_only=True, permission_target="text")])
    tools.reset_custom()
    assert "shout" not in tools._DISPATCH
    assert "shout" not in tools.READ_ONLY_TOOLS
    assert all(t["name"] != "shout" for t in tools.TOOLS)
    assert permissions._TARGET_KEY.get("shout") is None
    _, ctx = _ctx(tmp_path)
    assert tools.run_tool("shout", {}, ctx).is_error  # unknown tool again
