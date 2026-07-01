from pathlib import Path
from luban import agent, tools
from conftest import FakeBlock, FakeMessage, FakeClient


def _ctx(root: Path):
    return tools.ToolContext(root, lambda p: True, lambda a, b, c: None, lambda c: None)


def _cfg():
    return agent.AgentConfig(model="m", max_tokens=100, stream=False)


def test_plain_text_turn(tmp_path):
    fc = FakeClient([FakeMessage([FakeBlock("text", text="just answering")], "end_turn")])
    msgs = agent.run_turn(fc, _cfg(), [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    assert msgs[-1]["role"] == "assistant"


def test_tool_use_turn(tmp_path):
    (tmp_path / "f.py").write_text("secret\n")
    scripted = [
        FakeMessage(
            [FakeBlock("tool_use", id="t1", name="read_file", input={"path": "f.py"})],
            "tool_use",
        ),
        FakeMessage([FakeBlock("text", text="the file says secret")], "end_turn"),
    ]
    fc = FakeClient(scripted)
    msgs = agent.run_turn(fc, _cfg(), [{"role": "user", "content": "read f.py"}], _ctx(tmp_path), lambda t: None)
    # a tool_result user message must have been inserted before the final assistant reply
    tool_result_msgs = [
        m for m in msgs
        if m["role"] == "user" and isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert tool_result_msgs
    assert "secret" in tool_result_msgs[0]["content"][0]["content"]
    assert msgs[-1]["content"][0]["text"] == "the file says secret"


def test_multiple_tool_uses_in_one_turn(tmp_path):
    (tmp_path / "a.py").write_text("A\n")
    (tmp_path / "b.py").write_text("B\n")
    scripted = [
        FakeMessage(
            [
                FakeBlock("tool_use", id="t1", name="read_file", input={"path": "a.py"}),
                FakeBlock("tool_use", id="t2", name="read_file", input={"path": "b.py"}),
            ],
            "tool_use",
        ),
        FakeMessage([FakeBlock("text", text="done")], "end_turn"),
    ]
    fc = FakeClient(scripted)
    msgs = agent.run_turn(fc, _cfg(), [{"role": "user", "content": "read both"}], _ctx(tmp_path), lambda t: None)
    tr = [m for m in msgs if m["role"] == "user" and isinstance(m["content"], list)][-1]
    ids = {b["tool_use_id"] for b in tr["content"]}
    assert ids == {"t1", "t2"}
