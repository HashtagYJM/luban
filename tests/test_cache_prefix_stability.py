"""What must not change between calls, and why.

Prompt caching is a PREFIX match: the first differing byte invalidates everything after
it. luban has two breakpoints — end of the stable system block, and end of the
conversation — so the rule is simply that nothing before the second breakpoint may move
between calls.

The fact index and journal broke that rule. They were placed last in the SYSTEM prompt,
which was genuinely last while there was ONE breakpoint. The second breakpoint moved the
finish line: volatile then sat in the middle of the cached conversation prefix, so every
remember/journal write re-wrote the whole conversation — measured at 1,343,308 write
tokens against a final context of ~150,000, about nine times what a session should write.
And _HYGIENE asks the model to journal at the close of every working block, so luban was
causing this to itself.
"""
from types import SimpleNamespace

import pytest

from luban import agent, client as client_mod, usage as usage_mod


def _capture(monkeypatch):
    """Render a real turn and hand back the request luban actually built."""
    seen = {}

    def fake(client, *, system, messages, **kw):
        seen["system"] = system
        seen["messages"] = messages
        return SimpleNamespace(stop_reason="end_turn", content=[], usage=None,
                               context_management=None)

    monkeypatch.setattr(agent.client_mod, "create_turn", fake)
    return seen


def _cfg(volatile: str, **kw):
    return agent.AgentConfig("m", 100, stream=False, cache_prompt=True,
                             global_memory="STABLE MEMORY " * 40,
                             global_volatile=volatile, tools=[], **kw)


def _blocks_through_breakpoint(messages):
    """Every message block up to and including the one carrying cache_control."""
    out = []
    for m in messages:
        for b in m["content"]:
            out.append(b)
            if isinstance(b, dict) and "cache_control" in b:
                return out
    return out


# ---------------- the invariant ----------------

def test_changing_volatile_does_not_disturb_one_byte_of_the_cached_prefix(monkeypatch):
    """THE test. A remember/journal write must cost its own size and nothing else."""
    history = [{"role": "user", "content": "do the thing"},
               {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
               {"role": "user", "content": "and the next thing"}]

    seen = _capture(monkeypatch)
    agent._run_model_turn(None, _cfg("INDEX v1"), history, lambda t: None, None)
    first = (seen["system"], _blocks_through_breakpoint(seen["messages"]))

    client_mod._PROBES.clear()
    seen = _capture(monkeypatch)
    agent._run_model_turn(None, _cfg("INDEX v2 — a fact was just written"),
                          history, lambda t: None, None)
    second = (seen["system"], _blocks_through_breakpoint(seen["messages"]))

    assert first == second, "a memory write moved bytes inside the cached prefix"
    # ...and prove that is not because the write never reached the request
    assert "INDEX v2" in str(seen["messages"])


def test_volatile_rides_the_message_tail_not_the_system_prompt(monkeypatch):
    seen = _capture(monkeypatch)
    agent._run_model_turn(None, _cfg("THE INDEX"),
                          [{"role": "user", "content": "hi"}], lambda t: None, None)
    assert "THE INDEX" not in str(seen["system"])
    assert seen["messages"][-1]["content"][-1]["text"] == "THE INDEX"


def test_it_sits_after_the_breakpoint_not_before_it(monkeypatch):
    """Placed before, it would be cached — and then a write would invalidate it."""
    seen = _capture(monkeypatch)
    agent._run_model_turn(None, _cfg("THE INDEX"),
                          [{"role": "user", "content": "hi"}], lambda t: None, None)
    blocks = seen["messages"][-1]["content"]
    marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
    volatile_at = [i for i, b in enumerate(blocks) if b.get("text") == "THE INDEX"]
    assert marked, "the conversation breakpoint is gone"
    assert volatile_at and volatile_at[0] > marked[-1], "volatile is inside the cache"
    assert "cache_control" not in blocks[-1]


# ---------------- it must never be silently dropped ----------------

def test_volatile_falls_back_to_the_system_prompt_when_the_tail_cannot_take_it(monkeypatch):
    """A pause_turn re-send ends on the ASSISTANT's message. Appending there would read
    as the model having said it, so it goes back in the system prompt for that call."""
    seen = _capture(monkeypatch)
    agent._run_model_turn(
        None, _cfg("THE INDEX"),
        [{"role": "user", "content": "hi"},
         {"role": "assistant", "content": [{"type": "text", "text": "thinking"}]}],
        lambda t: None, None)
    assert "THE INDEX" in str(seen["system"])
    assert "THE INDEX" not in str(seen["messages"])


def test_volatile_survives_with_caching_switched_off(monkeypatch):
    seen = _capture(monkeypatch)
    cfg = agent.AgentConfig("m", 100, stream=False, cache_prompt=False,
                            global_volatile="THE INDEX", tools=[])
    agent._run_model_turn(None, cfg, [{"role": "user", "content": "hi"}],
                          lambda t: None, None)
    assert "THE INDEX" in seen["system"]


@pytest.mark.parametrize("history", [
    [{"role": "user", "content": "plain string"}],
    [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                   "content": "output"}]}],
])
def test_volatile_reaches_the_request_whatever_the_tail_looks_like(monkeypatch, history):
    seen = _capture(monkeypatch)
    agent._run_model_turn(None, _cfg("THE INDEX"), history, lambda t: None, None)
    assert "THE INDEX" in str(seen["system"]) + str(seen["messages"])


def test_the_tail_never_leaks_into_the_saved_transcript():
    """session.messages is what goes to disk and replays on resume."""
    original = [{"role": "user", "content": "hello"}]
    out, placed = agent.with_cache_breakpoint(original, "m", "THE INDEX")
    assert placed
    assert original == [{"role": "user", "content": "hello"}]
    assert "THE INDEX" in str(out)


def test_a_tool_result_still_comes_first_in_its_message():
    """The API requires tool_result blocks at the START of a user message; volatile is
    appended after them, never in front."""
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "out"}]}]
    out, _ = agent.with_cache_breakpoint(msgs, "m", "THE INDEX")
    blocks = out[-1]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[-1]["text"] == "THE INDEX"


# ---------------- cache entries that survive thinking time ----------------

def test_cache_entries_are_written_with_the_one_hour_ttl():
    """5-minute entries died during any pause, and an expiry re-writes the ENTIRE prefix
    rather than a delta. A 1h write costs 2x input against 1.25x, so it pays for itself
    the first time it prevents one expiry."""
    assert agent.cache_control("m") == {"type": "ephemeral", "ttl": "1h"}
    blocks = agent.build_system_param("stable", "", cache=True, model="m")
    assert blocks[0]["cache_control"]["ttl"] == "1h"
    out, _ = agent.with_cache_breakpoint([{"role": "user", "content": "hi"}], "m")
    assert out[-1]["content"][-1]["cache_control"]["ttl"] == "1h"


def test_a_backend_that_rejects_the_ttl_still_caches():
    client_mod.probes("m")["cache_ttl"] = False
    assert agent.cache_control("m") == {"type": "ephemeral"}


# ---------------- every call is on the meter ----------------

def test_side_calls_are_counted_but_do_not_report_the_context_size():
    """Fold, compact, flush and reflect send their own payload, not the conversation.
    They cost real money — a fold sends the whole early span UNCACHED — so they must be
    counted; but letting one set `last` would report the session's context as whatever
    that side call happened to send."""
    led = usage_mod.Ledger()
    led.add(usage_mod.Usage(input_tokens=100_000), "m")           # a real turn
    led.add(usage_mod.Usage(input_tokens=133_000), "m", context=False)  # a fold
    assert led.context_tokens == 100_000, "a side call must not become the window size"
    assert led.calls == 2
    assert led.input_tokens == 233_000, "but it is still on the bill"


def test_every_model_call_site_reports_usage():
    """The four side calls were invisible to /usage, which is exactly where the largest
    uncached request in a session lives."""
    import inspect
    from luban import cli
    for fn in (cli.fold_history, cli.compact_session):
        assert "ledger.add" in inspect.getsource(fn), fn.__name__
    for fn in (cli.flush_memory, cli.reflect_session, cli.build_agent_config):
        assert "on_usage" in inspect.getsource(fn), fn.__name__
