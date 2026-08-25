"""E41: a mid-session /model switch must not poison history permanently.

Reasoning state is provider-specific and both providers ride it on the same block —
Anthropic's `signature` IS what the OpenAI adapter sends as `encrypted_content`. So a
stored Claude signature replayed to a gpt model was rejected with
`invalid_encrypted_content` on every later turn, forever: the block never left history,
so /retry re-sent the same rejected request.
"""
from types import SimpleNamespace

import pytest

from luban import agent, client as client_mod, history
from luban.providers import openai as oa

from tests.test_multi_provider import FakeOpenAI, _resp


CLAUDE_SIG = "EvgOCkYIBxgCKkD..."      # what Anthropic actually returns
OPENAI_SEALED = "gAAAAABo3..."          # what the Responses API returns


def _thinking(signature, **extra):
    return {"type": "thinking", "thinking": "...", "signature": signature, **extra}


# ------------------------------------------------------- tagging at the store ----

def test_a_stored_thinking_block_records_which_provider_made_it():
    msg = SimpleNamespace(content=[SimpleNamespace(
        type="thinking", thinking="hm", signature=CLAUDE_SIG)], stop_reason="end_turn")
    blocks = client_mod.message_to_blocks(msg, "anthropic")
    assert blocks[0][history.PROVIDER_KEY] == "anthropic"


def test_an_openai_reasoning_item_is_tagged_openai_and_keeps_its_id():
    fake = FakeOpenAI(_resp([{"type": "reasoning", "id": "rs_1",
                              "encrypted_content": OPENAI_SEALED, "summary": []}]))
    msg = oa.OpenAIAdapter(fake).messages.create(
        model="gpt-5.6", max_tokens=10, system="", messages=[], tools=[])
    block = client_mod.message_to_blocks(msg, "openai")[0]
    assert block[history.PROVIDER_KEY] == "openai"
    assert block["id"] == "rs_1"


def test_the_tag_never_reaches_the_wire():
    """It is not in either provider's content-block schema."""
    msgs = [{"role": "assistant", "content": [_thinking(CLAUDE_SIG, provider="anthropic")]}]
    sent = history.for_send(msgs, "anthropic")
    assert sent[0]["content"][0] == _thinking(CLAUDE_SIG)


# ------------------------------------------------------ dropping foreign state ----

def test_a_claude_signature_is_not_sent_to_openai():
    msgs = [{"role": "assistant", "content": [_thinking(CLAUDE_SIG, provider="anthropic")]}]
    assert history.for_send(msgs, "openai") == []


def test_an_openai_blob_is_not_sent_to_anthropic():
    """The mirror case is equally invalid — this is not a one-directional guard."""
    msgs = [{"role": "assistant",
             "content": [_thinking(OPENAI_SEALED, id="rs_1", provider="openai")]}]
    assert history.for_send(msgs, "anthropic") == []


def test_everything_else_in_the_turn_survives_the_drop():
    msgs = [{"role": "assistant", "content": [
        _thinking(CLAUDE_SIG, provider="anthropic"),
        {"type": "text", "text": "keep me"},
    ]}]
    assert history.for_send(msgs, "openai") == [
        {"role": "assistant", "content": [{"type": "text", "text": "keep me"}]}]


def test_untagged_state_is_classified_by_shape():
    """Sessions written before the tag existed. An OpenAI reasoning item is replayed
    with its own id; an Anthropic thinking block has none — that is all the evidence
    there is."""
    legacy_claude = [{"role": "assistant", "content": [_thinking(CLAUDE_SIG)]}]
    legacy_openai = [{"role": "assistant", "content": [_thinking(OPENAI_SEALED, id="rs_9")]}]
    assert history.for_send(legacy_claude, "openai") == []
    assert history.for_send(legacy_claude, "anthropic") == legacy_claude
    assert history.for_send(legacy_openai, "anthropic") == []
    assert history.for_send(legacy_openai, "openai") == legacy_openai


def test_history_with_no_reasoning_state_is_left_alone():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}]
    assert history.for_send(msgs, "openai") == msgs
    assert history.for_provider(msgs, "openai") is msgs  # no copy when nothing changes


def test_the_two_existing_history_rules_still_run_on_the_way_out():
    msgs = [{"role": "assistant",
             "content": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]}]
    assert history.for_send(msgs, "anthropic") == []


# ------------------------------------------------------------ the live repro ----

def test_the_switch_that_used_to_400_now_sends_a_clean_request():
    """The whole bug, end to end: a Claude turn, /model to gpt, next send."""
    fake = FakeOpenAI(_resp([]))
    claude_turn = client_mod.message_to_blocks(
        SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="hm",
                                                 signature=CLAUDE_SIG)],
                        stop_reason="end_turn"),
        "anthropic")
    client_mod.create_turn(
        oa.OpenAIAdapter(fake), model="gpt-5.6", max_tokens=10, system="", tools=[],
        messages=[{"role": "user", "content": "hi"},
                  {"role": "assistant", "content": claude_turn}])
    sent = fake.requests[0]["input"]
    assert not [i for i in sent if i.get("type") == "reasoning"]
    assert not any(CLAUDE_SIG in str(i) for i in sent)


def test_the_agent_loop_tags_what_it_stores(monkeypatch):
    """The tag has to be written where the block is stored, or nothing downstream can
    tell whose it is."""
    def fake_turn(client, config, messages, on_text, on_thinking, on_retry=None):
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="hm", signature=CLAUDE_SIG)],
            stop_reason="end_turn", usage=None)

    monkeypatch.setattr(agent, "_run_model_turn", fake_turn)
    cfg = agent.AgentConfig("claude-opus-4-8", 10, stream=False)
    out = agent.run_turn(None, cfg, [{"role": "user", "content": "hi"}], None,
                         lambda t: None)
    assert out[-1]["content"][0][history.PROVIDER_KEY] == "anthropic"


# --------------------------------------------------- E41(b): asking for the state ----

def test_the_request_asks_for_encrypted_reasoning_state():
    """store=false returns replayable reasoning state only if you ask for it."""
    req = oa.build_request({"model": "gpt-5.6", "messages": [],
                            "output_config": {"effort": "high"}})
    assert req["store"] is False
    assert req["include"] == ["reasoning.encrypted_content"]


def test_a_backend_that_rejects_include_degrades_instead_of_failing_every_turn():
    class Rejecting(FakeOpenAI):
        def __init__(self):
            super().__init__(_resp([]))
            outer = self

            class _Responses:
                @staticmethod
                def create(**req):
                    outer.requests.append(req)
                    if "include" in req:
                        raise _Status400("Unknown parameter: 'include'.")
                    return _resp([])

            self.responses = _Responses()

    class _Status400(Exception):
        status_code = 400

    oa._INCLUDE_OK["reasoning"] = None
    fake = Rejecting()
    oa.OpenAIAdapter(fake).messages.create(model="gpt-5.6", max_tokens=10, system="",
                                           messages=[], tools=[])
    assert "include" not in fake.requests[-1]
    assert oa._INCLUDE_OK["reasoning"] is False
    oa.OpenAIAdapter(fake).messages.create(model="gpt-5.6", max_tokens=10, system="",
                                           messages=[], tools=[])
    assert "include" not in fake.requests[-1]  # asked once, not once per turn


@pytest.fixture(autouse=True)
def _reset_include_probe():
    yield
    oa._INCLUDE_OK["reasoning"] = None
