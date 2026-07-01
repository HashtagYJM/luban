from luban import client
from conftest import FakeBlock, FakeMessage, FakeClient


def test_create_turn_returns_message():
    msg = FakeMessage([FakeBlock("text", text="hi")], "end_turn")
    fc = FakeClient([msg])
    got = client.create_turn(fc, model="m", max_tokens=10, system="s", messages=[], tools=[])
    assert got.stop_reason == "end_turn"
    assert fc.messages.calls[0]["model"] == "m"


def test_stream_turn_streams_text():
    msg = FakeMessage([FakeBlock("text", text="hello")], "end_turn")
    fc = FakeClient([msg])
    seen = []
    got = client.stream_turn(
        fc, model="m", max_tokens=10, system="s", messages=[], tools=[],
        on_text=seen.append,
    )
    assert "hello" in "".join(seen)
    assert got.stop_reason == "end_turn"


def test_message_to_blocks_text_and_tool():
    msg = FakeMessage(
        [FakeBlock("text", text="ok"), FakeBlock("tool_use", id="t1", name="read_file", input={"path": "f"})],
        "tool_use",
    )
    blocks = client.message_to_blocks(msg)
    assert blocks[0] == {"type": "text", "text": "ok"}
    assert blocks[1] == {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "f"}}
