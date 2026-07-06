from pathlib import Path
from luban import agent, tools
from conftest import FakeBlock, FakeMessage, FakeClient


def _ctx(root: Path):
    return tools.ToolContext(root, lambda p: True, lambda a, b, c: None, lambda c: None)


def _cfg():
    return agent.AgentConfig(model="m", max_tokens=100, stream=False)


def test_system_prompt_windows_mentions_cmd():
    sp = agent.system_prompt_for("windows")
    assert "Windows" in sp and "cmd" in sp.lower()
    assert agent.SYSTEM_PROMPT in sp  # base prompt preserved


def test_system_prompt_unknown_platform_is_base_only():
    assert agent.system_prompt_for("") == agent.SYSTEM_PROMPT


def test_config_passes_platform_into_prompt(tmp_path, monkeypatch):
    # The streamed system prompt reflects config.platform.
    seen = {}

    def fake_create(client, *, model, max_tokens, system, messages, tools):
        seen["system"] = system
        return FakeMessage([FakeBlock("text", text="ok")], "end_turn")

    monkeypatch.setattr(agent.client_mod, "create_turn", fake_create)
    cfg = agent.AgentConfig(model="m", max_tokens=100, stream=False, platform="windows")
    agent.run_turn(FakeClient([]), cfg, [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    assert "Windows" in seen["system"]


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


def test_tool_use_stop_but_no_tool_blocks_terminates(tmp_path):
    # Pathological: stop_reason says tool_use but there are no tool_use blocks.
    fc = FakeClient([FakeMessage([FakeBlock("text", text="oops")], "tool_use")])
    msgs = agent.run_turn(fc, _cfg(), [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    assert msgs[-1]["role"] == "assistant"


def test_unoffered_tool_is_rejected_not_dispatched(tmp_path):
    # When config.tools restricts the tool set, an unoffered tool call is rejected
    # with is_error=True and the handler never runs (no file created).
    (tmp_path / "existing.txt").write_text("should not be changed\n")
    scripted = [
        FakeMessage(
            [FakeBlock("tool_use", id="t1", name="write_file", input={"path": "existing.txt", "content": "hacked"})],
            "tool_use",
        ),
        FakeMessage([FakeBlock("text", text="done")], "end_turn"),
    ]
    fc = FakeClient(scripted)
    # Restrict tools to only read_file (not write_file)
    cfg = agent.AgentConfig(model="m", max_tokens=100, stream=False,
                             tools=[{"name": "read_file", "description": "read", "input_schema": {}}])
    msgs = agent.run_turn(fc, cfg, [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    # Tool result should be an error
    tool_results = [
        m["content"] for m in msgs
        if m["role"] == "user" and isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert tool_results, "expected tool_result in messages"
    result = tool_results[0][0]
    assert result["is_error"] is True
    assert "not available" in result["content"].lower()
    # File should be unchanged (handler never ran)
    assert (tmp_path / "existing.txt").read_text() == "should not be changed\n"
