"""E36 — lifecycle hooks: an instruction that binds instead of one the model may skip.

These assert the contract, not the wording: which hooks fire for an event, that output
comes back tagged, that a failure is loud, and that a re-fire replaces its own previous
injection rather than stacking copies into the conversation.
"""
import sys

from luban import hooks


def _hook(event, run, **kw):
    return hooks.Hook(event=event, run=run, **kw)


# --- declaration ------------------------------------------------------------


def test_parse_reads_an_entry_per_table():
    got, warnings = hooks.parse([
        {"event": "session_start", "run": "echo hi"},
        {"event": "post_tool_use", "run": "echo bye", "match": "write_file"},
    ])
    assert warnings == []
    assert [h.event for h in got] == ["session_start", "post_tool_use"]
    assert got[1].match == "write_file"
    assert got[0].inject is True  # injecting is the default


def test_parse_rejects_an_unknown_event_loudly():
    got, warnings = hooks.parse([{"event": "on_tuesday", "run": "echo hi"}])
    assert got == []
    assert warnings and "on_tuesday" in warnings[0]
    # The warning must name what IS valid, or the user cannot act on it.
    assert "session_start" in warnings[0]


def test_parse_rejects_an_entry_with_no_command():
    got, warnings = hooks.parse([{"event": "stop"}])
    assert got == []
    assert warnings and "run" in warnings[0]


def test_parse_rejects_a_match_on_an_event_that_cannot_match():
    # `match` filters tool names; on session_start it would silently never apply.
    got, warnings = hooks.parse([
        {"event": "session_start", "run": "echo hi", "match": "write_file"}
    ])
    assert got == []
    assert warnings and "match" in warnings[0]


# --- selection --------------------------------------------------------------


def test_for_event_selects_only_that_event():
    hs = [_hook("session_start", "a"), _hook("stop", "b")]
    assert [h.run for h in hooks.for_event(hs, "stop")] == ["b"]


def test_post_tool_use_match_filters_by_tool_name():
    hs = [
        _hook("post_tool_use", "checked", match="write_file"),
        _hook("post_tool_use", "always"),
    ]
    runs = [h.run for h in hooks.for_event(hs, "post_tool_use", tool_name="write_file")]
    assert runs == ["checked", "always"]
    # A non-matching tool leaves only the unfiltered hook.
    runs = [h.run for h in hooks.for_event(hs, "post_tool_use", tool_name="read_file")]
    assert runs == ["always"]


# --- running ----------------------------------------------------------------


def test_output_comes_back_tagged_with_its_event(tmp_path):
    out = hooks.run_hooks([_hook("session_start", "echo loaded")], "session_start", tmp_path)
    assert "loaded" in out
    assert hooks.OPEN.format(event="session_start") in out
    assert hooks.CLOSE.format(event="session_start") in out


def test_nothing_declared_costs_nothing(tmp_path):
    assert hooks.run_hooks([], "stop", tmp_path) == ""


def test_a_failing_hook_still_injects_and_says_it_failed(tmp_path):
    notices = []
    out = hooks.run_hooks(
        [_hook("stop", "echo boom; exit 3")], "stop", tmp_path, notify=notices.append
    )
    assert "boom" in out           # the output is still worth having
    assert "exit code 3" in out    # ...and the model is told it failed
    assert notices and "exit code 3" in notices[0]  # ...and so is the human


def test_a_hook_that_injects_nothing_is_a_pure_side_effect(tmp_path):
    marker = tmp_path / "ran.txt"
    out = hooks.run_hooks(
        [_hook("post_tool_use", f"echo x > {marker.name}", inject=False)],
        "post_tool_use", tmp_path,
    )
    assert out == ""            # nothing enters context
    assert marker.exists()      # but it really ran


def test_output_is_capped_and_says_so(tmp_path):
    py = sys.executable
    big = f'"{py}" -c "print(\'x\' * {hooks.MAX_HOOK_OUTPUT * 2})"'
    out = hooks.run_hooks([_hook("stop", big)], "stop", tmp_path)
    assert len(out) < hooks.MAX_HOOK_OUTPUT * 2
    assert "truncated" in out.lower()


def test_a_denied_hook_does_not_run_and_is_reported(tmp_path):
    marker = tmp_path / "should-not-exist.txt"

    class _Deny:
        action = "deny"
        reason = "blocked by rule"

    notices = []
    out = hooks.run_hooks(
        [_hook("stop", f"echo x > {marker.name}")], "stop", tmp_path,
        decide=lambda cmd: _Deny(), notify=notices.append,
    )
    assert not marker.exists()
    assert out == ""
    assert notices and "blocked" in notices[0].lower()


def test_every_fire_is_audited(tmp_path):
    entries = []
    hooks.run_hooks([_hook("stop", "echo hi")], "stop", tmp_path, audit=entries.append)
    assert len(entries) == 1
    assert entries[0]["tool"] == "hook:stop"


# --- replace on re-fire -----------------------------------------------------


def test_a_re_fire_replaces_its_own_previous_injection():
    """Recitation every turn must leave ONE copy of the plan in the conversation, not
    one per turn: the cost is paid every call thereafter, and the stale copies
    contradict the live one."""
    block = hooks.wrap("user_prompt_submit", "PLAN v1")
    messages = [
        {"role": "user", "content": f"{block}\n\ndo the thing"},
        {"role": "assistant", "content": "ok"},
    ]
    hooks.strip_previous(messages, "user_prompt_submit")
    assert "PLAN v1" not in messages[0]["content"]
    assert "do the thing" in messages[0]["content"]  # the user's own words survive


def test_strip_leaves_other_events_alone():
    keep = hooks.wrap("session_start", "SKILL")
    drop = hooks.wrap("stop", "CHECK")
    messages = [{"role": "user", "content": f"{keep}\n{drop}\nhi"}]
    hooks.strip_previous(messages, "stop")
    assert "SKILL" in messages[0]["content"]
    assert "CHECK" not in messages[0]["content"]


def test_strip_never_touches_tool_results():
    """Tool results are list-content and pair with a tool_use id; editing them is how a
    conversation starts 400ing."""
    content = [{"type": "tool_result", "tool_use_id": "1", "content": "x"}]
    messages = [{"role": "user", "content": content}]
    hooks.strip_previous(messages, "stop")
    assert messages[0]["content"] is content
