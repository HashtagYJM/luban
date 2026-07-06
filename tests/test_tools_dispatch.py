from pathlib import Path
from luban import tools


def _ctx(root: Path):
    return tools.ToolContext(root, lambda p: True, lambda a, b, c: None, lambda c: None)


def test_tools_schema_names():
    names = {t["name"] for t in tools.TOOLS}
    assert names == {
        "list_dir", "glob", "grep", "read_file",
        "write_file", "edit_file", "run_command", "load_skill", "sessions",
        "remember", "recall", "forget", "journal",
    }
    for t in tools.TOOLS:
        assert "description" in t and "input_schema" in t


def test_run_tool_routes(tmp_path):
    (tmp_path / "a.py").write_text("x")
    out = tools.run_tool("list_dir", {"path": "."}, _ctx(tmp_path))
    assert "a.py" in out.content


def test_run_tool_unknown(tmp_path):
    out = tools.run_tool("nope", {}, _ctx(tmp_path))
    assert out.is_error and "unknown tool" in out.content.lower()
