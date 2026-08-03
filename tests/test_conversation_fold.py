"""A lifecycle for the conversation: bounded window, full record on disk, omission stated.

The same contract already settled for the journal, applied one level up. The API is
stateless, so every call re-sends the whole conversation — 89.5% of a real install's
context — and nothing ever bounded it.
"""
from pathlib import Path

import pytest

from luban import cli, config as config_mod, usage as usage_mod


def _u(text):        return {"role": "user", "content": text}
def _a(text):        return {"role": "assistant", "content": [{"type": "text", "text": text}]}
def _call(tid):      return {"role": "assistant", "content": [
                         {"type": "tool_use", "id": tid, "name": "read_file", "input": {}}]}
def _result(tid):    return {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": tid, "content": "x" * 400}]}


def _tool_heavy(n=12):
    msgs = []
    for i in range(n):
        msgs += [_u(f"question {i} " + "q" * 300), _call(f"t{i}"), _result(f"t{i}"),
                 _a(f"answer {i} " + "a" * 300)]
    return msgs


# ---------------- constraint 1: never split a tool_use / tool_result pair ----------------

def test_a_fold_never_orphans_a_tool_result():
    """The API requires every tool_use to be followed by its tool_result. A span starting
    with an orphaned tool_result 400s — the likeliest way this feature breaks a session."""
    msgs = _tool_heavy()
    for keep in range(100, 12_000, 137):          # every plausible boundary
        cut = cli.fold_boundary(msgs, keep)
        if cut == 0:
            continue
        first = msgs[cut]
        assert cli._starts_a_clean_exchange(first), (
            f"fold at {cut} starts the kept span on {first['role']} with tool blocks")
        blocks = first.get("content")
        if isinstance(blocks, list):
            assert not any(b.get("type") == "tool_result" for b in blocks)


def test_the_boundary_is_a_user_turn_not_an_assistant_reply():
    msgs = _tool_heavy()
    cut = cli.fold_boundary(msgs, 3_000)
    assert msgs[cut]["role"] == "user"


def test_recent_turns_survive_verbatim():
    """Continuity is preserved where it matters — the working set is untouched."""
    msgs = _tool_heavy()
    cut = cli.fold_boundary(msgs, 4_000)
    kept = msgs[cut:]
    assert kept and kept[-1] == msgs[-1]
    assert len(kept) < len(msgs), "something must actually be folded"


def test_nothing_foldable_returns_zero_rather_than_a_bad_cut():
    assert cli.fold_boundary([_u("hi"), _a("hello")], 10_000) == 0


# ---------------- constraint 2: the fold must be worth its cache cost ----------------

def test_a_small_fold_is_declined(monkeypatch, tmp_path):
    """Folding invalidates the cached prefix, so many small folds are strictly worse than
    a few large ones — the same reasoning as clear_at_least in context editing."""
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=[_u("a" * 200), _a("b" * 200)])
    s.ledger.add(usage_mod.Usage(input_tokens=500))
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 2.9)
    called = []
    monkeypatch.setattr(cli.client_mod, "create_turn", lambda *a, **k: called.append(1))
    assert cli.fold_history(s, object(), config_mod.Config(platform="mac"), tmp_path) is False
    assert not called, "must not spend a model call on a fold too small to matter"


# ---------------- constraint 3: nothing is destroyed, and it is stated ----------------

def test_folding_changes_what_is_sent_never_what_is_stored(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(cli, "save_session", lambda s: saved.append(list(s.messages)))
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 2.9)

    class FakeBlock:
        type, text = "text", "SUMMARY OF EARLY WORK"
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: type("M", (), {"content": [FakeBlock()]})())
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)

    # scale the thresholds to the fixture rather than generating megabytes; the real
    # minimum-fold behaviour is covered by test_a_small_fold_is_declined
    monkeypatch.setattr(cli, "FOLD_MIN_TOKENS", 100)
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=_tool_heavy(40))
    cfg = config_mod.Config(platform="mac", warn_tokens=10_000)
    before = list(s.messages)
    assert cli.fold_history(s, object(), cfg, tmp_path) is True
    # the FULL transcript was written to disk before anything was folded
    assert saved[0] == before
    # and what is now SENT is shorter, with the fold stated
    assert len(s.messages) < len(before)
    marker = s.messages[0]["content"]
    assert "folded" in marker and "transcript" in marker
    assert "SUMMARY OF EARLY WORK" in marker
    assert cli._starts_a_clean_exchange(s.messages[0])


def test_the_marker_points_at_where_the_record_still_lives(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "save_session", lambda s: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 2.9)
    class FB:
        type, text = "text", "s"
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: type("M", (), {"content": [FB()]})())
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli, "FOLD_MIN_TOKENS", 100)
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=_tool_heavy(40), session_id="abc123")
    cli.fold_history(s, object(), config_mod.Config(platform="mac", warn_tokens=10_000),
                     tmp_path)
    assert "abc123" in s.messages[0]["content"]


# ---------------- driven by measurement, and never silent ----------------

def test_folding_is_driven_by_measured_context_not_an_estimate():
    src = Path("luban/cli.py").read_text(encoding="utf-8")
    body = src[src.index("def maintain_context"):src.index("def compact_session")]
    assert "ledger.context_tokens" in body
    assert "estimate_tokens" not in body


def test_a_declined_fold_leaves_history_untouched(monkeypatch):
    """auto_fold = false restores the prompt, and declining must change nothing."""
    monkeypatch.setattr("builtins.input", lambda p: "n")
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=_tool_heavy(40))
    s.ledger.add(usage_mod.Usage(input_tokens=140_000))
    before = list(s.messages)
    cli.maintain_context(s, object(),
                         config_mod.Config(platform="mac", auto_fold=False), Path("."))
    assert s.messages == before
    assert "left as-is" in "".join(out)


def test_no_offer_below_the_trigger(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail("must not ask"))
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[])
    s.ledger.add(usage_mod.Usage(input_tokens=40_000))
    cli.maintain_context(s, object(), config_mod.Config(platform="mac"), Path("."))


# ---------------- automatic, but never silent ----------------

def _over_threshold(messages=None):
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=messages if messages is not None else _tool_heavy(40))
    s.ledger.add(usage_mod.Usage(input_tokens=140_000))
    return s


def _auto(**kw):
    """warn_tokens scaled to the fixture so a fold is actually reachable, as elsewhere
    in this file — generating a real 150k-token conversation would cost megabytes."""
    return config_mod.Config(platform="mac", warn_tokens=10_000, **kw)


def test_it_folds_without_asking(monkeypatch, tmp_path):
    """A fold you have to approve is always late: every call between the prompt and the
    answer pays for the full window, and the saving only accrues to calls made AFTER."""
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail("must not ask"))
    monkeypatch.setattr(cli, "save_session", lambda s: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 2.9)
    monkeypatch.setattr(cli, "FOLD_MIN_TOKENS", 100)
    class FB:
        type, text = "text", "SUMMARY"
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: type("M", (), {"content": [FB()]})())
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    s = _over_threshold()
    before = len(s.messages)
    cli.maintain_context(s, object(), _auto(), tmp_path)
    assert len(s.messages) < before


def test_an_automatic_fold_announces_itself_before_and_after(monkeypatch, tmp_path):
    """Silent context trimming is the one thing this must never become."""
    monkeypatch.setattr(cli, "save_session", lambda s: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 2.9)
    monkeypatch.setattr(cli, "FOLD_MIN_TOKENS", 100)
    class FB:
        type, text = "text", "SUMMARY"
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: type("M", (), {"content": [FB()]})())
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    cli.maintain_context(_over_threshold(), object(), _auto(), tmp_path)
    said = "".join(out)
    assert "folding now" in said                     # before
    assert "auto_fold" in said                       # and how to stop it
    assert "folded" in said and "transcript" in said  # after: what, and where it still is


def test_a_failed_fold_is_not_retried_every_turn(monkeypatch, tmp_path):
    """Failure costs a model call, and context is still over the threshold — without a
    latch an automatic fold would burn one call per turn for the rest of the session."""
    monkeypatch.setattr(cli, "save_session", lambda s: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 2.9)
    monkeypatch.setattr(cli, "FOLD_MIN_TOKENS", 100)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("gateway said no")

    monkeypatch.setattr(cli.client_mod, "create_turn", boom)
    s = _over_threshold()
    cfg = _auto()
    for _ in range(5):
        cli.maintain_context(s, object(), cfg, tmp_path)
    assert len(calls) == 1
    assert s.fold_failed is True


def test_a_fold_too_small_to_matter_does_not_latch(monkeypatch, tmp_path):
    """Declining costs nothing — no model call is made — so it must stay retryable as the
    conversation grows into being worth folding."""
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 2.9)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: pytest.fail("no call for a declined fold"))
    s = _over_threshold(messages=[_u("a" * 200), _a("b" * 200)])
    cli.maintain_context(s, object(), _auto(), tmp_path)
    assert s.fold_failed is False


def test_auto_fold_is_on_by_default_and_switchable():
    assert config_mod.Config(platform="mac").auto_fold is True
    assert config_mod.Config(platform="mac", auto_fold=False).auto_fold is False
