"""Token accounting measured from API responses, never estimated.

The defect this replaces: estimate_tokens assumed 4 chars/token against a measured 2.94,
a 36% undercount, and it governed the /compact nudge — which therefore fired at ~203,900
real tokens instead of 150,000. It also ignored the system prompt and tool schemas
entirely, counting only message text.
"""
import re
from pathlib import Path

import pytest

from luban import cli, client as client_mod, usage as usage_mod


class FakeUsage:
    def __init__(self, i=0, o=0, cw=0, cr=0):
        self.input_tokens, self.output_tokens = i, o
        self.cache_creation_input_tokens, self.cache_read_input_tokens = cw, cr


class FakeMsg:
    def __init__(self, usage, original=None):
        self.usage = usage
        if original is not None:
            self.context_management = type("CM", (), {"original_input_tokens": original})()


def test_usage_is_read_from_the_response():
    u = usage_mod.from_response(FakeMsg(FakeUsage(i=1_200, o=800, cw=14_000, cr=0)))
    assert (u.input_tokens, u.output_tokens) == (1_200, 800)
    assert u.cache_creation_input_tokens == 14_000


def test_missing_usage_never_raises():
    assert usage_mod.from_response(object()).context_tokens == 0


def test_context_size_counts_cached_tokens_too():
    """input_tokens EXCLUDES cache reads, so it alone understates a cached turn badly."""
    u = usage_mod.from_response(FakeMsg(FakeUsage(i=900, cr=14_000)))
    assert u.input_tokens == 900
    assert u.context_tokens == 14_900, "cached tokens are still in the model's context"


def test_context_is_the_last_call_not_the_sum():
    """Spend accumulates; context does not. Conflating them is how a spend figure gets
    compared against a window threshold."""
    led = usage_mod.Ledger()
    for _ in range(5):
        led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=10_000, o=500))))
    assert led.context_tokens == 10_000        # window size
    assert led.total_tokens == 52_500          # spend


def test_cache_weighted_spend_tracks_consumption():
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=1_000, o=1_000, cr=100_000))))
    assert led.total_tokens == 102_000
    assert led.effective_tokens == pytest.approx(12_000)   # cache read at ~0.1x
    assert led.cache_hit_rate == pytest.approx(100_000 / 101_000)


def test_cleared_tokens_are_measured_from_the_api_not_claimed():
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=25_000), original=70_000)))
    assert led.cleared_tokens == 45_000
    assert "45,000" in usage_mod.report(led, 150_000)


def test_the_turn_line_shows_context_output_and_session_spend():
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=900, o=1_500, cr=14_000))))
    line = usage_mod.turn_line(led, 150_000)
    assert "ctx 14.9k/150.0k" in line and "out" in line and "session" in line


# ---------------- the guard on the defect itself ----------------

def test_the_nudge_is_driven_by_measured_tokens_not_the_estimator():
    src = Path("luban/cli.py").read_text(encoding="utf-8")
    nudge = src[src.index("save_session(session)\n            # The live token line"):
                src.index("consider /compact")]
    assert "ledger.context_tokens" in nudge, "the nudge must use MEASURED context"
    assert "est > cfg.warn_tokens" not in nudge, "the 4-chars/token estimate is gone"


def test_no_module_asserts_four_chars_per_token():
    """The constant that caused a 54,000-token overshoot must not govern anything."""
    offenders = []
    for f in Path("luban").glob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"//\s*4\b", line) and "token" in line.lower():
                offenders.append(f"{f.name}:{i}")
    assert not offenders, f"4-chars/token estimation still governs: {offenders}"


# ---------------- context editing ----------------

def test_context_management_config_is_derived_from_warn_tokens():
    cm = client_mod.context_management(150_000)["edits"][0]
    assert cm["type"] == "clear_tool_uses_20250919"
    assert cm["trigger"]["value"] == 90_000            # 0.6 x warn_tokens
    assert cm["clear_at_least"]["value"] >= 1          # worth the cache invalidation
    for tool in ("remember", "recall", "forget", "journal"):
        assert tool in cm["exclude_tools"], "memory results must never be cleared"


def test_a_backend_without_a_beta_surface_falls_back_silently():
    """A corporate proxy lacking client.beta must keep working, not fail the turn."""
    client_mod._PROBES.clear()
    got = client_mod._try_context_managed(
        object(), "create", {}, {}, client_mod.context_management(150_000), None)
    assert got is None
    assert client_mod.probes("")["ctx_mgmt"] is False
    client_mod._PROBES.clear()


def test_context_editing_is_off_by_default():
    """The only change that alters the request shape ships OFF.

    Not because the values are unknown — they derive from warn_tokens — but because it
    could not be exercised against the corporate proxy from the dev machine. Measure with
    /usage first, then enable and measure again. A rollback switch for an untested path is
    a different thing from a knob that hands a decision to the user.
    """
    from luban import config as config_mod
    assert config_mod.Config(platform="mac").context_editing is False


def test_context_editing_is_only_sent_when_enabled():
    import inspect
    src = inspect.getsource(cli.build_agent_config)
    assert "cfg.context_editing" in src, "ctx_mgmt must be gated on the config switch"


def test_a_rejected_beta_param_does_not_burn_retries():
    """A 400 is not transient, so the fallback costs one call, not four."""
    class Rejected(Exception):
        status_code = 400
    assert client_mod.is_transient(Rejected()) is False


def test_two_breakpoints_and_what_that_means_for_context_editing():
    """There are now TWO cache breakpoints, and that CHANGES the context-editing analysis.

    Before: one breakpoint on the stable system block. Tool results lived in `messages`,
    after it, so server-side clearing could not invalidate the cached prefix.

    Now: a second breakpoint marks the end of the conversation, because the cached amount
    was a constant (~11-13k across four measured sessions) while the conversation was
    re-billed in full every call — 1,319,849 of 1,954,702 tokens spent were repeats.

    The consequence: cleared tool results sit INSIDE the second cached prefix, so every
    clear now invalidates the conversation cache. The two features partially conflict, and
    caching wins on the measured numbers — which is a further reason context_editing stays
    off by default, and why clear_at_least matters if it is ever turned on.
    """
    import ast, inspect
    from luban import agent as agent_mod
    src = Path("luban/agent.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # count FUNCTIONS that set a breakpoint, not lines — one mark can have two branches
    # (string vs block content) and that is still a single breakpoint.
    def sets_a_breakpoint(node):
        body = ast.get_source_segment(src, node) or ""
        code = "\n".join(ln for ln in body.splitlines()
                         if not ln.lstrip().startswith("#"))
        return '"cache_control"' in code        # a real dict key, not prose
    setters = {n.name for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and sets_a_breakpoint(n)}
    assert setters == {"build_system_param", "with_cache_breakpoint"}, (
        f"cache breakpoints moved or multiplied: {setters}")


def test_the_breakpoint_never_mutates_the_saved_transcript():
    """Request-shaping metadata must not leak into session.messages, which is what gets
    written to disk and replayed on resume."""
    from luban import agent as agent_mod
    original = [{"role": "user", "content": "hello"}]
    out, _placed = agent_mod.with_cache_breakpoint(original)
    assert "cache_control" not in str(original), "the caller's list was mutated"
    assert out[-1]["content"][-1]["cache_control"]["type"] == "ephemeral"


def test_a_string_content_message_becomes_markable():
    from luban import agent as agent_mod
    out, _placed = agent_mod.with_cache_breakpoint(
        [{"role": "user", "content": "plain text"}])
    block = out[-1]["content"][-1]
    assert block["type"] == "text" and block["text"] == "plain text"


def test_a_tool_result_tail_is_markable_too():
    """An agentic turn often ends on a tool_result; it must still take the breakpoint."""
    from luban import agent as agent_mod
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "out"}]}]
    out, _placed = agent_mod.with_cache_breakpoint(msgs)
    assert out[-1]["content"][-1]["cache_control"]["type"] == "ephemeral"
    assert "cache_control" not in str(msgs)


def test_count_tokens_includes_the_tool_schemas():
    """The cacheable prefix is tools -> system, so measuring `system` alone under-reports it.

    On a real install that omission was 6,286 chars of tool surface, which made the
    /context figure not comparable with the cached figure /usage reads off the API — two
    numbers describing the same thing and disagreeing.
    """
    seen = {}
    class FakeMessages:
        def count_tokens(self, **kw):
            seen.update(kw)
            return type("R", (), {"input_tokens": 1234})()
    client = type("C", (), {"messages": FakeMessages()})()
    got = cli.count_tokens(client, "m", "SYSTEM", [{"name": "read_file"}])
    assert got == 1234
    assert seen.get("tools") == [{"name": "read_file"}], "tool schemas must be counted"


def test_count_tokens_still_works_without_tools():
    class FakeMessages:
        def count_tokens(self, **kw):
            assert "tools" not in kw
            return type("R", (), {"input_tokens": 7})()
    client = type("C", (), {"messages": FakeMessages()})()
    assert cli.count_tokens(client, "m", "SYSTEM") == 7


def test_usage_totals_come_from_the_response_not_count_tokens():
    """The headline numbers must never depend on a second API call or a conversion —
    msg.usage IS the billing record."""
    import inspect
    from luban import usage as usage_mod
    src = inspect.getsource(usage_mod)
    assert "count_tokens" not in src, "usage accounting must read the response only"
    assert "usage" in inspect.getsource(usage_mod.from_response)


# ---------------- cost estimation ----------------

def test_cost_applies_the_cache_multipliers():
    """A cache read is a tenth of the input rate and a write is 1.25x — that ratio is the
    whole reason caching is the largest available lever."""
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=1_000_000))))
    assert usage_mod.cost(led, "claude-opus-4-8") == pytest.approx(5.00)
    led2 = usage_mod.Ledger()
    led2.add(usage_mod.from_response(FakeMsg(FakeUsage(cr=1_000_000))))
    assert usage_mod.cost(led2, "claude-opus-4-8") == pytest.approx(0.50)
    led3 = usage_mod.Ledger()
    led3.add(usage_mod.from_response(FakeMsg(FakeUsage(cw=1_000_000))))
    assert usage_mod.cost(led3, "claude-opus-4-8") == pytest.approx(6.25)


def test_output_is_priced_five_times_input():
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(o=1_000_000))))
    assert usage_mod.cost(led, "claude-opus-4-8") == pytest.approx(25.00)


def test_an_unknown_model_reports_no_price_rather_than_a_wrong_one():
    """A wrong number is worse than no number for something a person budgets against."""
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=1000))))
    assert usage_mod.cost(led, "some-internal-alias") is None
    text = usage_mod.report(led, 150_000, "some-internal-alias")
    assert "no price on file" in text and "$" not in text.split("no price")[0].split("tokens")[-1]


def test_the_estimate_is_labelled_as_one():
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=1000, o=100))))
    text = usage_mod.report(led, 150_000, "claude-opus-4-8")
    assert "ESTIMATED" in text and "list prices" in text
    assert "actual bill may differ" in text


def test_longest_prefix_wins_so_aliases_still_price():
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=1_000_000))))
    assert usage_mod.cost(led, "claude-opus-4-8-some-suffix") == pytest.approx(5.00)
    assert usage_mod.cost(led, "claude-haiku-4-5-20251001") == pytest.approx(1.00)


def test_a_backend_reporting_no_usage_says_so_loudly():
    """A provider switch must not silently reinstate the estimator this module replaced."""
    class OAUsage:
        prompt_tokens, completion_tokens = 12_000, 800
    class OAMsg:
        usage = OAUsage()
    led = usage_mod.Ledger()
    for _ in range(3):
        led.add(usage_mod.from_response(OAMsg()))
    assert led.blind is True
    text = usage_mod.report(led, 150_000, "gpt-5")
    assert "no usage data" in text and "UNMEASURED" in text
    assert "36%" in text


def test_a_normal_session_is_not_flagged_blind():
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=100, o=10))))
    assert led.blind is False
    assert "UNMEASURED" not in usage_mod.report(led, 150_000, "claude-opus-4-8")


# ---------------- vendored pricing (LiteLLM subset) ----------------

def test_prices_ship_with_the_package():
    """The table must be inside the wheel — the target machine is offline."""
    import json
    from pathlib import Path as P
    import luban
    data = json.loads((P(luban.__file__).with_name("prices.json")).read_text(encoding="utf-8"))
    assert len(data) > 50
    assert "claude-opus-4-8" in data


def test_cache_rates_are_per_model_not_a_global_multiplier():
    """The reason to vendor rather than hand-roll.

    A global 'write = 1.25x input, read = 0.1x' is an Anthropic-shaped assumption. OpenAI
    charges for cache READS and not for WRITES at all, so a multiplier would invent a
    charge that does not exist.
    """
    anthropic = usage_mod.rates("claude-opus-4-8")
    assert anthropic["cache_creation_input_token_cost"] == pytest.approx(6.25e-06)
    assert anthropic["cache_read_input_token_cost"] == pytest.approx(5e-07)

    openai = usage_mod.rates("gpt-5")
    assert "cache_read_input_token_cost" in openai
    assert "cache_creation_input_token_cost" not in openai, (
        "if OpenAI ever charges for cache writes this test should be updated, not deleted")


def test_a_missing_cache_rate_bills_at_the_plain_input_rate():
    """Not at an invented premium."""
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(cw=1_000_000))))
    inp = usage_mod.rates("gpt-5")["input_cost_per_token"]
    assert usage_mod.cost(led, "gpt-5") == pytest.approx(1_000_000 * inp)


def test_multi_provider_pricing_is_now_a_data_lookup():
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=1_000_000))))
    assert usage_mod.cost(led, "claude-opus-4-8") == pytest.approx(5.00)
    assert usage_mod.cost(led, "gpt-5") == pytest.approx(1.25)
    assert usage_mod.cost(led, "claude-sonnet-5") == pytest.approx(2.00)


def test_a_corrupt_or_missing_table_shows_tokens_not_a_wrong_price(monkeypatch):
    monkeypatch.setattr(usage_mod, "_PRICES", {})
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(FakeMsg(FakeUsage(i=1000))))
    assert usage_mod.cost(led, "claude-opus-4-8") is None
