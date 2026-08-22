"""E36 — where hooks fire, and where they must NOT.

The module contract is in test_hooks.py. This is about the wiring: the fire points in a
session, and the two contexts that are deliberately denied hooks.
"""
from pathlib import Path

from luban import cli, config as config_mod, hooks, tools


def _cfg(entries, **kw):
    parsed, warnings = hooks.parse(entries)
    assert warnings == []
    return config_mod.Config(platform="mac", hooks=parsed, **kw)


def _session():
    return cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[])


def _ctx(root):
    return tools.ToolContext(root, lambda p: True, lambda a, b, c: None, lambda c: None)


# --- the fire points --------------------------------------------------------


def test_fire_hooks_parks_output_for_the_next_model_call(tmp_path):
    session = _session()
    cfg = _cfg([{"event": "session_start", "run": "echo SKILL-TEXT"}])
    cli.fire_hooks(session, cfg, _ctx(tmp_path), "session_start")
    assert any("SKILL-TEXT" in p for p in session.pending_context)
    # pending_context is the channel the reconcile directive already uses: it merges
    # into the next user message rather than becoming a turn of its own.
    composed = cli.compose_user_message(session, "do the thing")
    assert "SKILL-TEXT" in composed and composed.endswith("do the thing")


def test_nothing_declared_fires_nothing(tmp_path):
    session = _session()
    cli.fire_hooks(session, _cfg([]), _ctx(tmp_path), "session_start")
    assert session.pending_context == []


def test_a_recited_hook_leaves_one_copy_not_one_per_turn(tmp_path):
    """The whole point of replace-on-re-fire: injected text stays in the conversation
    and is re-sent on every later call, so N turns of recitation would otherwise cost N
    copies — and the stale ones contradict the live plan."""
    session = _session()
    cfg = _cfg([{"event": "user_prompt_submit", "run": "echo PLAN-BODY"}])
    ctx = _ctx(tmp_path)
    for turn in range(3):
        cli.fire_hooks(session, cfg, ctx, "user_prompt_submit")
        session.messages.append(
            {"role": "user", "content": cli.compose_user_message(session, f"turn {turn}")}
        )
        session.messages.append({"role": "assistant", "content": "ok"})
    body = "\n".join(
        m["content"] for m in session.messages
        if m["role"] == "user" and isinstance(m["content"], str)
    )
    assert body.count("PLAN-BODY") == 1
    # ...and every prompt the user actually typed is still there.
    for turn in range(3):
        assert f"turn {turn}" in body


def test_the_fire_points_exist_in_the_session_loop():
    """Guard: each event has a call site. A hook mechanism whose events never fire is
    the failure it was built to fix, wearing the fix's clothes."""
    src = Path(cli.__file__).read_text(encoding="utf-8")
    for event in ("session_start", "user_prompt_submit", "stop"):
        assert f'fire_hooks(session, cfg, ctx, "{event}")' in src, event
    # session_start fires twice: at launch, and again after /compact resets the session.
    assert src.count('fire_hooks(session, cfg, ctx, "session_start")') == 2


# --- post_tool_use rides the tool result ------------------------------------


def test_post_tool_use_output_is_attached_to_that_tools_result(tmp_path):
    ctx = tools.ToolContext(
        tmp_path, lambda p: True, lambda a, b, c: None, lambda c: None,
        hooks=hooks.parse([
            {"event": "post_tool_use", "run": "echo CHECKED", "match": "write_file"}
        ])[0],
    )
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "x"}, ctx)
    assert "CHECKED" in out.content          # the model sees the check with the write
    assert (tmp_path / "a.txt").exists()     # ...and the write still happened


def test_a_non_matching_tool_fires_nothing(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    ctx = tools.ToolContext(
        tmp_path, lambda p: True, lambda a, b, c: None, lambda c: None,
        hooks=hooks.parse([
            {"event": "post_tool_use", "run": "echo CHECKED", "match": "write_file"}
        ])[0],
    )
    out = tools.run_tool("read_file", {"path": "a.txt"}, ctx)
    assert "CHECKED" not in out.content


def test_a_broken_hook_does_not_fail_the_tool_call(tmp_path):
    ctx = tools.ToolContext(
        tmp_path, lambda p: True, lambda a, b, c: None, lambda c: None,
        hooks=hooks.parse([{"event": "post_tool_use", "run": "exit 9"}])[0],
    )
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "x"}, ctx)
    assert not out.is_error                  # the write succeeded and says so
    assert "exit code 9" in out.content      # the failure is still reported


# --- the contexts denied hooks ----------------------------------------------


def test_a_context_without_hooks_fires_none(tmp_path):
    """A subagent builds its own ToolContext and gets no hooks: a nested read-only run
    must not fire a write-check, and it must not spend the parent's budget."""
    ctx = _ctx(tmp_path)
    assert ctx.hooks == []
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "x"}, ctx)
    assert "[hook:" not in out.content


def test_the_flush_turn_is_denied_hooks(monkeypatch, tmp_path):
    """The pre-compact flush turn inherits the conversation but must not fire hooks —
    it has one job and a budget to protect."""
    seen = {}

    def _capture(client, config, msgs, ctx, *a, **kw):
        seen["ctx"] = ctx
        return msgs

    monkeypatch.setattr(cli.agent, "run_turn", _capture)
    monkeypatch.setattr(cli.memory_mod, "checkpoint", lambda *a, **kw: None)
    session = _session()
    session.messages = [{"role": "user", "content": "hi"}]
    cfg = _cfg([{"event": "post_tool_use", "run": "echo NO"}], memory_enabled=True)
    ctx = tools.ToolContext(
        tmp_path, lambda p: True, lambda a, b, c: None, lambda c: None, hooks=cfg.hooks
    )
    cli.flush_memory(session, object(), ctx, cfg)
    assert seen["ctx"].hooks == []
    assert seen["ctx"].only == frozenset({"journal", "checkpoint"})  # still allowlisted


# --- config -----------------------------------------------------------------


def test_config_reads_a_hooks_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'platform = "mac"\n\n'
        '[[hooks]]\n'
        'event = "session_start"\n'
        'run = "echo hi"\n',
        encoding="utf-8",
    )
    cfg = config_mod.load_config(path)
    assert [h.event for h in cfg.hooks] == ["session_start"]


def test_a_malformed_hook_is_reported_to_the_human(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'platform = "mac"\n\n[[hooks]]\nevent = "on_tuesday"\nrun = "echo hi"\n',
        encoding="utf-8",
    )
    assert config_mod.load_config(path).hooks == []
    warnings = config_mod.config_warnings(path)
    assert warnings and "on_tuesday" in warnings[0]


def test_the_default_config_documents_the_feature():
    text = config_mod._default_text("windows")
    assert "[[hooks]]" in text
    for event in hooks.EVENTS:
        assert event in text
