"""A turn that comes back empty must not kill the session.

Field symptom: a blank `luban>` answer, then a session that never worked again, and a
saved file that reopened just as dead. The mechanism is one line of history — the API
rejects a message whose content is empty, so a single blank response 400s every later
send, and the session is saved after every turn.

Two upstream causes reach the same place and neither is repairable upstream: the model
genuinely returns nothing, or the only block is unsigned thinking, which `message_to_blocks`
drops on purpose because an unsigned block fails validation when echoed back.
"""
from dataclasses import dataclass, field

from luban import agent, cli, history, tools


@dataclass
class Blk:
    type: str
    text: str = ""
    thinking: str = ""
    signature: str = ""


@dataclass
class Resp:
    content: list
    stop_reason: str = "end_turn"
    usage: object = None


class _Messages:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.scripted.pop(0)


class Client:
    def __init__(self, scripted):
        self.messages = _Messages(scripted)


def _ctx(tmp_path):
    return tools.ToolContext(tmp_path, lambda p: True, lambda a, b, c: None, lambda c: None)


def _run(tmp_path, response, on_empty=None):
    cfg = agent.AgentConfig("m", 100, stream=False)
    return agent.run_turn(
        Client([response]), cfg, [{"role": "user", "content": "hi"}],
        _ctx(tmp_path), lambda t: None, on_empty=on_empty,
    )


# --- the history guarantee ---------------------------------------------------


def test_an_empty_response_leaves_no_empty_message_in_history(tmp_path):
    msgs = _run(tmp_path, Resp([]))
    assert not any(m.get("content") in ([], "") for m in msgs)


def test_a_thinking_only_response_with_no_signature_does_the_same(tmp_path):
    """Unsigned thinking is dropped by message_to_blocks (correctly), which empties the
    message — so the safe-looking path arrives at the identical fatal state."""
    msgs = _run(tmp_path, Resp([Blk(type="thinking", thinking="reasoned, unsigned")]))
    assert not any(m.get("content") in ([], "") for m in msgs)


def test_sanitize_heals_a_session_already_poisoned(tmp_path):
    """Sessions saved before this fix carry the empty message on disk. Loading one must
    repair it, not merely avoid creating another."""
    poisoned = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": []},
        {"role": "user", "content": "still there?"},
    ]
    cleaned = history.sanitize_history(poisoned)
    assert {"role": "assistant", "content": []} not in cleaned
    assert len(cleaned) == 2


def test_a_whitespace_only_string_counts_as_empty():
    cleaned = history.sanitize_history([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "   \n "},
    ])
    assert len(cleaned) == 1


def test_a_normal_turn_is_untouched(tmp_path):
    msgs = _run(tmp_path, Resp([Blk(type="text", text="hello")]))
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == [{"type": "text", "text": "hello"}]


# --- and it must be LOUD -----------------------------------------------------


def test_the_human_is_told_rather_than_shown_a_blank_line(tmp_path):
    seen = []
    _run(tmp_path, Resp([], stop_reason="end_turn"), on_empty=seen.append)
    assert seen == ["end_turn"]


def test_the_typed_prompt_is_kept_for_retry(monkeypatch):
    """Same contract as a turn the network killed: the prompt is not lost, and the
    history does not end on an unanswered user turn."""
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    session = cli.Session(model="m", max_tokens=10, auto=True, stream=False, messages=[
        {"role": "assistant", "content": [{"type": "text", "text": "earlier"}]},
        {"role": "user", "content": "the prompt I typed"},
    ])
    cli.empty_turn_notice(session, "end_turn")
    assert session.last_failed == "the prompt I typed"
    assert session.messages[-1]["role"] == "assistant"
    body = "".join(printed)
    assert "empty response" in body and "/retry" in body
    # It must point at the request shape — the settings that actually cause this.
    assert "context_editing" in body
