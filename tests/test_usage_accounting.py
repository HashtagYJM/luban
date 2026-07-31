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
    client_mod._CONTEXT_MGMT_SUPPORTED = None
    got = client_mod._try_context_managed(
        object(), "create", {}, {}, client_mod.context_management(150_000), None)
    assert got is None
    assert client_mod._CONTEXT_MGMT_SUPPORTED is False
    client_mod._CONTEXT_MGMT_SUPPORTED = None
