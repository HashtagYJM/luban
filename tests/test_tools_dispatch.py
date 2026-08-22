from pathlib import Path
from luban import tools


def _ctx(root: Path):
    return tools.ToolContext(root, lambda p: True, lambda a, b, c: None, lambda c: None)


def test_tools_schema_names():
    """Offered and dispatchable must be the same set. A name in one and not the other
    is either a tool the model can see and cannot call, or one it can call and was
    never told about. spawn_subagent is config-gated, so cli appends its schema
    separately — it is still dispatchable and still has to be accounted for here."""
    names = {t["name"] for t in tools.TOOLS} | {tools.SUBAGENT_TOOL["name"]}
    assert names == set(tools._DISPATCH)
    for t in tools.TOOLS:
        assert "description" in t and "input_schema" in t


def test_run_tool_routes(tmp_path):
    (tmp_path / "a.py").write_text("x")
    out = tools.run_tool("list_dir", {"path": "."}, _ctx(tmp_path))
    assert "a.py" in out.content


def test_run_tool_unknown(tmp_path):
    out = tools.run_tool("nope", {}, _ctx(tmp_path))
    assert out.is_error and "unknown tool" in out.content.lower()
