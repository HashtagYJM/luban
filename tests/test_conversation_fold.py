"""A lifecycle for the conversation: bounded window, full record on disk, omission stated.

The same contract already settled for the journal, applied one level up. The API is
stateless, so every call re-sends the whole conversation — the great majority of context
on any mature session — and nothing ever bounded it.
"""
from pathlib import Path

import pytest

from luban import cli, config as config_mod, usage as usage_mod


def _u(text):        return {"role": "user", "content": text}
def _a(text):        return {"role": "assistant", "content": [{"type": "text", "text": text}]}
def _call(tid):      return {"role": "assistant", "content": [
                         {"type": "tool_use", "id": tid, "name": "read_file", "input": {}}]}
def _result(tid, n=400):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "x" * n}]}


def _tool_heavy(n=12):
    msgs = []
    for i in range(n):
        msgs += [_u(f"question {i} " + "q" * 300), _call(f"t{i}"), _result(f"t{i}"),
                 _a(f"answer {i} " + "a" * 300)]
    return msgs


def _agentic_turn(idx, rounds, chars=2_000):
    """One human prompt, then `rounds` tool round-trips with no user text between them.

    This is the shape of a real agentic turn and the shape `_tool_heavy` never has: there
    the human speaks every fourth message, so a legal boundary is always within reach. A
    turn's worth of tool traffic is bounded by nothing, and a boundary exists only where
    the human typed.
    """
    msgs = [_u(f"prompt {idx}")]
    for r in range(rounds):
        msgs += [_call(f"{idx}_{r}"), _result(f"{idx}_{r}", chars)]
    return msgs + [_a(f"answer {idx}")]


# ---------------- constraint 1: never split a tool_use / tool_result pair ----------------

def _a_boundary_that_meets_the_target(msgs, keep):
    """A cut that is both legal and leaves at least the target verbatim. If one exists,
    refusing to fold is a bug; if none does, refusing is correct."""
    return any(cli._starts_a_clean_exchange(msgs[i]) and cli._history_chars(msgs[i:]) >= keep
               for i in range(1, len(msgs)))


def test_a_fold_never_orphans_a_tool_result():
    """The API requires every tool_use to be followed by its tool_result. A span starting
    with an orphaned tool_result 400s — the likeliest way this feature breaks a session.

    A cut of 0 is a RESULT, not a case to skip: it claims no legal boundary exists, and
    that claim has to hold too. Skipping it is how a boundary search that gives up on
    perfectly foldable histories passed for a working one.
    """
    msgs = _tool_heavy()
    for keep in range(100, 12_000, 137):          # every plausible boundary
        cut = cli.fold_boundary(msgs, keep)
        if cut == 0:
            assert not _a_boundary_that_meets_the_target(msgs, keep), (
                f"refused to fold at keep={keep} though a legal boundary exists")
            continue
        first = msgs[cut]
        assert cli._starts_a_clean_exchange(first), (
            f"fold at {cut} starts the kept span on {first['role']} with tool blocks")
        blocks = first.get("content")
        if isinstance(blocks, list):
            assert not any(b.get("type") == "tool_result" for b in blocks)


def test_a_turn_bigger_than_the_keep_window_still_folds():
    """A boundary exists only where the human typed, and one turn's tool traffic is
    bounded by nothing. Searching forward from the target finds nothing here and gives
    up on a history that is almost entirely foldable."""
    msgs = _tool_heavy(20) + _agentic_turn(99, 60)
    keep = cli._history_chars(_agentic_turn(99, 60)) // 2   # target lands mid-run
    cut = cli.fold_boundary(msgs, keep)
    assert cut > 0, "gave up rather than cutting at the last boundary before the target"
    assert cli._starts_a_clean_exchange(msgs[cut])


def test_the_kept_span_is_never_smaller_than_the_target():
    """The working set is the point of folding. A boundary search that overshoots the
    target can summarise away the run still being worked on and report success."""
    msgs = _tool_heavy(20) + _agentic_turn(98, 40) + _agentic_turn(99, 1, 50)
    for keep in range(2_000, cli._history_chars(msgs), 4_001):
        cut = cli.fold_boundary(msgs, keep)
        if cut == 0:
            continue
        assert cli._history_chars(msgs[cut:]) >= keep, (
            f"kept {cli._history_chars(msgs[cut:])} chars against a target of {keep}")


def test_zero_means_no_boundary_exists_at_all():
    """The one history that genuinely cannot be folded: a single unbroken run. Here the
    refusal is true, and it is the only shape for which it is."""
    assert cli.fold_boundary(_agentic_turn(1, 60), 10_000) == 0


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


def test_the_measured_ratio_is_unmoved_by_server_side_clearing(monkeypatch):
    """Clearing shrinks what the model READS but not the local message list. Measuring
    chars-per-token against the cleared count inflates the ratio, and every threshold
    derived from it inflates too — folds get rarer and quieter exactly when tool output
    is heaviest, which is when folding is needed."""
    monkeypatch.setattr(cli, "count_tokens", lambda *a: 0)
    msgs = _tool_heavy(20)

    def ratio_for(original):
        s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=msgs)
        s.ledger.add(usage_mod.Usage(input_tokens=10_000, original_input_tokens=original))
        return cli.chars_per_token(s, object(), config_mod.Config(platform="mac"),
                                   Path("."))

    assert ratio_for(40_000) < ratio_for(0), (
        "a cleared call must not report four times the characters per token")


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
    assert s.fold_blocked is True


def test_a_fold_too_small_to_matter_does_not_latch(monkeypatch, tmp_path):
    """Declining costs nothing — no model call is made — so it must stay retryable as the
    conversation grows into being worth folding."""
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 2.9)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: pytest.fail("no call for a declined fold"))
    s = _over_threshold(messages=[_u("a" * 200), _a("b" * 200)])
    cli.maintain_context(s, object(), _auto(), tmp_path)
    assert s.fold_blocked is False


def test_auto_fold_is_on_by_default_and_switchable():
    assert config_mod.Config(platform="mac").auto_fold is True
    assert config_mod.Config(platform="mac", auto_fold=False).auto_fold is False


# ---------------- the fold has to CONVERGE, not just run ----------------
# A fold that leaves context above the trigger fires again next turn, and every turn after
# — each one the most expensive call in the session. Running is not the property; landing
# under the trigger is.

def _folding_loop(monkeypatch, tmp_path, messages, turn, turns=6, standing=25_000,
                  ratio=2.9, warn=150_000):
    """Run the post-turn path repeatedly, feeding the ledger a MEASURED context derived
    from what would actually be on the wire. Returns (transcript per turn, fold calls)."""
    monkeypatch.setattr(cli, "save_session", lambda s: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a, **k: ratio)
    monkeypatch.setattr(cli, "standing_tokens", lambda *a, **k: standing)
    calls = []

    class _Blk:
        type, text = "text", "SUMMARY. " * 50

    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: calls.append(1) or type("M", (), {"content": [_Blk()]})())
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=list(messages))
    cfg = config_mod.Config(platform="mac", warn_tokens=warn)
    said, after = [], []
    for i in range(turns):
        s.messages.extend(turn(i))
        s.ledger.add(usage_mod.Usage(
            input_tokens=standing + int(cli._history_chars(s.messages) / ratio)))
        out.clear()
        cli.maintain_context(s, object(), cfg, tmp_path)
        said.append("".join(out))
        # what the NEXT call will actually send, which is what the fold has to have moved
        after.append(standing + int(cli._history_chars(s.messages) / ratio))
    return s, said, calls, after


def test_a_fold_lands_under_the_trigger_it_was_fired_by(monkeypatch, tmp_path):
    """The trigger measures the WHOLE prompt; the target sized only the HISTORY. So a fold
    could report success and leave the total above the line that fired it — which fires it
    again next turn, and every turn after, at the most expensive call in the session."""
    base = [_u("q" * 3000), _a("a" * 9000)] * 30
    s, said, calls, after = _folding_loop(monkeypatch, tmp_path, base,
                                          lambda i: [_u("q" * 3000), _a("a" * 60_000)])
    assert calls, "the fixture must actually cross the trigger"
    landed = [n for t, n in zip(said, after) if "✓ folded" in t]
    assert landed, "the fixture must actually fold"
    assert all(n < 150_000 * cli.FOLD_TRIGGER for n in landed), (
        f"a fold landed at {landed} against a trigger of {150_000 * cli.FOLD_TRIGGER:,.0f} "
        "— it will fire again on the very next turn")


def test_the_kept_span_leaves_room_for_the_standing_prefix(monkeypatch, tmp_path):
    """40% of warn_tokens of HISTORY plus a 25k system prompt is not 40% of warn_tokens."""
    monkeypatch.setattr(cli, "standing_tokens", lambda *a, **k: 25_000)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a, **k: 2.9)
    keep = cli.fold_keep_chars(object(), object(), config_mod.Config(platform="mac"),
                               Path("."), 2.9)
    assert keep / 2.9 + 25_000 <= 150_000 * cli.FOLD_TRIGGER


def test_a_fold_updates_the_context_figure_it_will_be_judged_by(monkeypatch, tmp_path):
    """The ledger measures the last CALL, and a fold makes no call with the new history —
    so without this the turn's own '/compact' note prints the pre-fold number, and the next
    decision is taken against a context that no longer exists."""
    monkeypatch.setattr(cli, "save_session", lambda s: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a, **k: 2.9)
    monkeypatch.setattr(cli, "standing_tokens", lambda *a, **k: 10_000)
    monkeypatch.setattr(cli, "FOLD_MIN_TOKENS", 100)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)

    class _Blk:
        type, text = "text", "SUMMARY"

    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: type("M", (), {"content": [_Blk()]})())
    s = _over_threshold()
    before = s.ledger.context_tokens
    cli.fold_history(s, object(), _auto(), tmp_path)
    assert s.ledger.context_tokens < before, "the figure still describes the folded-away span"
    # and a real measurement supersedes the projection
    s.ledger.add(usage_mod.Usage(input_tokens=99_999))
    assert s.ledger.context_tokens == 99_999


def test_no_way_out_of_a_fold_is_silent(monkeypatch, tmp_path):
    """Three exits leave the conversation unchanged — no boundary, too small to be worth
    the cache write, and a fold that cannot reach the bulk. Announcing the attempt and then
    saying nothing reads as a fold that did nothing, which is what the field saw."""
    monkeypatch.setattr(cli, "save_session", lambda s: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a, **k: 2.9)
    monkeypatch.setattr(cli, "standing_tokens", lambda *a, **k: 1_000)
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: pytest.fail("no model call for a no-op fold"))
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    # too small to matter: the oldest span is tiny, the bulk is in the working set
    s = _over_threshold(messages=[_u("a" * 200), _a("b" * 200)])
    cli.fold_history(s, object(), _auto(), tmp_path)
    assert "".join(out).strip(), "a declined fold said nothing at all"
    assert s.fold_blocked is False, "no model call was spent, so it must stay retryable"


def test_the_warning_waits_for_the_situation_to_change(monkeypatch, tmp_path):
    """Repeating an unchanged four-line warning every turn trains the reader to skip it."""
    base = [_u("q" * 2000)] + sum(([_call(f"t{i}"), _result(f"t{i}", 9000)]
                                   for i in range(60)), [])
    s, said, calls, after = _folding_loop(
        monkeypatch, tmp_path, base,
        lambda i: sum(([_call(f"n{i}_{r}"), _result(f"n{i}_{r}", 9000)]
                       for r in range(4)), []))
    warned = [t for t in said if "context is" in t]
    assert len(warned) < len(said), "the same warning fired on every single turn"


# ---------------- one oversized tool result is the shape folding cannot reach ----------------
# A web search or a PDF read lands ONE enormous tool_result in the recent span the fold is
# built to protect. Folding the older span frees nothing, so context stays over the line
# with no lever at all. The pair is indivisible structurally; nothing requires the result to
# keep its BODY.

def _huge(tid, n=400_000):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "x" * n}]}


def test_an_oversized_tool_result_is_bounded_rather_than_left_alone():
    msgs = [_u("read the pdf"), _call("t1"), _huge("t1"), _a("done"),
            _u("now what?"), _a("thinking")]
    freed = cli.shrink_oversized_results(msgs, limit_chars=50_000, session_id="s1")
    assert freed > 300_000
    assert cli._history_chars(msgs) < 60_000


def test_shrinking_keeps_the_pair_and_says_what_it_dropped():
    """Stated, never silent — and the record is still on disk, so the marker must say so."""
    msgs = [_u("read the pdf"), _call("t1"), _huge("t1"), _a("done"),
            _u("next"), _a("ok")]
    cli.shrink_oversized_results(msgs, limit_chars=50_000, session_id="s1")
    block = msgs[2]["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "t1", (
        "the tool_use/tool_result pair must survive structurally or the next send 400s")
    assert "s1" in block["content"] and "transcript" in block["content"]


def test_the_live_exchange_is_never_shrunk():
    """Summarising away the result the model is working on right now is the failure mode
    folding already learned once."""
    msgs = [_u("a"), _a("b"), _u("read it"), _call("t1"), _huge("t1")]
    assert cli.shrink_oversized_results(msgs, limit_chars=50_000, session_id="s1") == 0


def test_a_pending_oversized_result_does_not_latch_folding_off(monkeypatch, tmp_path):
    """"Cannot help yet" and "cannot help at all" must not collapse into one latch. The
    live exchange is protected, so a huge result that just arrived blocks every lever —
    and becomes reachable the moment the conversation moves past it."""
    monkeypatch.setattr(cli, "save_session", lambda s: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a, **k: 2.9)
    monkeypatch.setattr(cli, "standing_tokens", lambda *a, **k: 10_000)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: pytest.fail("no model call is possible here"))
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=[_u("read it"), _call("t1"), _huge("t1")])
    s.ledger.add(usage_mod.Usage(input_tokens=140_000))
    cli.fold_history(s, object(), config_mod.Config(platform="mac"), tmp_path)
    assert s.fold_blocked is False
    # the conversation moves on, and now the same result is reachable
    s.messages += [_u("thanks"), _a("ok")]
    assert cli.shrink_oversized_results(s.messages, limit_chars=50_000, session_id="s") > 0


def test_a_projection_never_feeds_the_ratio_that_produced_it(monkeypatch):
    """The projection is derived FROM the ratio. Measuring the ratio against it would let
    the estimate calibrate itself, and drift would compound every fold."""
    monkeypatch.setattr(cli, "count_tokens", lambda *a: 0)
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=_tool_heavy(20))
    s.ledger.add(usage_mod.Usage(input_tokens=10_000))
    cfg, root = config_mod.Config(platform="mac"), Path(".")
    measured = cli.chars_per_token(s, object(), cfg, root)
    s.ledger.project_context(999_999)
    assert cli.chars_per_token(s, object(), cfg, root) == measured


def test_a_web_search_result_is_never_touched():
    """The server_tool_use/web_search_tool_result pair is not independently editable — the
    API rejects a search whose result does not follow, and the strand is unrecoverable."""
    msgs = [_u("search"),
            {"role": "assistant", "content": [
                {"type": "server_tool_use", "id": "w1", "name": "web_search", "input": {}}]},
            {"role": "user", "content": [
                {"type": "web_search_tool_result", "tool_use_id": "w1",
                 "content": "x" * 400_000}]},
            _a("done"), _u("next"), _a("ok")]
    assert cli.shrink_oversized_results(msgs, limit_chars=50_000, session_id="s1") == 0
