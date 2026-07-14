"""E24 (and most of what E23 logged): a turn cut off at max_tokens MID-TOOL-CALL.

sanitize_history MUST strip the unanswered tool_use (E14: it 400s the next send), but
stripping it SILENTLY is how a write vanishes — the model announces the write, no tool
runs, no error appears anywhere, and the model never learns the call was dropped, so it
reports success. From the outside that is indistinguishable from 'announce-and-yield'.
"""
import pytest

from luban import agent, cli, config as config_mod


class Block:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class Msg:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


def _cfg(**kw):
    return agent.AgentConfig(model="m", max_tokens=8192, stream=False, platform="mac",
                             skills=[], memory="", global_memory="", tools=[], **kw)


@pytest.fixture()
def truncated_then_done(monkeypatch):
    """First turn: text + a tool_use, cut off at max_tokens. Then a clean turn."""
    calls = []

    def fake(client, config, messages, on_text, on_thinking, on_retry=None):
        calls.append(list(messages))
        if len(calls) == 1:
            return Msg("max_tokens", [
                Block("text", text="Writing the file now."),
                Block("tool_use", id="t1", name="write_file", input={"path": "a"}),
            ])
        return Msg("end_turn", [Block("text", text="done")])

    monkeypatch.setattr(agent, "_run_model_turn", fake)
    return calls


def test_a_truncated_tool_call_is_no_longer_silent(truncated_then_done):
    seen = []
    agent.run_turn(None, _cfg(), [{"role": "user", "content": "write it"}], None,
                   lambda t: None, on_truncated=lambda cap, n, tot: seen.append((cap, n)))
    assert seen == [(8192, 1)]  # the human is told, with the ceiling that bit


def test_the_model_is_told_its_tool_call_was_dropped(truncated_then_done):
    """Otherwise it assumes the write succeeded and reports success — the E24 evidence."""
    agent.run_turn(None, _cfg(), [{"role": "user", "content": "write it"}], None,
                   lambda t: None)
    second_turn = truncated_then_done[1]
    nudge = second_turn[-1]
    assert nudge["role"] == "user"
    assert "TOOL CALL WAS DROPPED" in nudge["content"]
    assert "Do not assume it succeeded" in nudge["content"]


def test_the_dangling_tool_use_is_still_stripped(truncated_then_done):
    """E14 must still hold: history may never end on an unanswered tool_use."""
    out = agent.run_turn(None, _cfg(), [{"role": "user", "content": "write it"}], None,
                         lambda t: None)
    assert out == agent.sanitize_history(out)
    for msg in out:
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            assert not any(b.get("type") == "tool_use" for b in msg["content"]
                           if isinstance(b, dict))


def test_retries_are_bounded(monkeypatch):
    """A model that keeps truncating must not loop forever."""
    calls = []

    def always_truncates(client, config, messages, on_text, on_thinking, on_retry=None):
        calls.append(1)
        return Msg("max_tokens", [
            Block("text", text="writing"),
            Block("tool_use", id="t", name="write_file", input={}),
        ])

    monkeypatch.setattr(agent, "_run_model_turn", always_truncates)
    agent.run_turn(None, _cfg(), [{"role": "user", "content": "go"}], None, lambda t: None)
    assert len(calls) == agent.MAX_TRUNCATION_RETRIES + 1


def test_a_plain_max_tokens_stop_with_no_tool_call_is_left_alone(monkeypatch):
    """Running out of room while writing prose is not a dropped tool call — don't nudge."""
    seen = []
    monkeypatch.setattr(agent, "_run_model_turn", lambda *a, **k: Msg(
        "max_tokens", [Block("text", text="a very long essay")]))
    out = agent.run_turn(None, _cfg(), [{"role": "user", "content": "essay"}], None,
                         lambda t: None, on_truncated=lambda *a: seen.append(a))
    assert seen == []
    assert out[-1]["role"] == "assistant"  # returned, not looped


# ---------------- max_tokens is a real config key now ----------------

def test_max_tokens_is_configurable(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\nmax_tokens = 64000\n', encoding="utf-8")
    assert config_mod.load_config(p).max_tokens == 64_000


def test_the_default_is_no_longer_the_old_starving_8192():
    assert config_mod.Config(platform="mac").max_tokens == 32_000


def test_an_explicit_flag_beats_config():
    cfg = config_mod.Config(platform="mac", max_tokens=32_000)
    assert cli.resolve_max_tokens(50_000, cfg, stream=True) == 50_000


def test_an_absent_flag_no_longer_overrides_config():
    """The bug: --max-tokens defaulted to 8192, so config.toml could never raise it."""
    cfg = config_mod.Config(platform="mac", max_tokens=64_000)
    assert cli.parse_args([]).max_tokens is None
    assert cli.resolve_max_tokens(None, cfg, stream=True) == 64_000


def test_no_stream_is_clamped(monkeypatch):
    """A big non-streamed request holds an idle connection open until it times out."""
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    cfg = config_mod.Config(platform="mac", max_tokens=64_000)
    assert cli.resolve_max_tokens(None, cfg, stream=False) == cli.NO_STREAM_MAX_TOKENS
