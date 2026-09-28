"""E56 phase 1 — sub-agents with a chosen model and role, a result header, and fan-out.

The child used to run on the parent's model with the parent's tools, its usage was booked
under the parent's model, and several spawn_subagent calls in one message ran one after
another. These tests hold the four increments: a chosen model routes by prefix and is
refused loudly when nothing can serve it; a named role narrows tools and adds a preamble;
every result says who ran, on what, doing what, for how much; and two children in one
message overlap in time without losing each other's answer or an interrupt's record.
"""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from luban import agent, cli, client as client_mod, config as config_mod, sessions as sessions_mod, tools

from tests.conftest import FakeBlock, FakeClient, FakeMessage


def _msg(content, stop, n_in=100, n_out=10):
    m = FakeMessage(content, stop)
    m.usage = SimpleNamespace(input_tokens=n_in, output_tokens=n_out,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return m


def _text(t, n_in=100, n_out=10):
    return _msg([FakeBlock("text", text=t)], "end_turn", n_in, n_out)


def _call(tid, name, **inp):
    return FakeBlock("tool_use", id=tid, name=name, input=inp)


def _session(tmp_path, model="claude-parent"):
    return cli.Session(model=model, max_tokens=100, auto=False, stream=False,
                       project=str(tmp_path))


def _rows(tmp_path):
    p = tmp_path / "audit.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _system_text(call):
    system = call["system"]
    return system if isinstance(system, str) else "".join(b["text"] for b in system)


ROLES = {"reviewer": {"model": "gpt-x", "prompt": "ROLE-PREAMBLE: review sceptically.",
                      "tools": ["read_file"], "description": "Independent review"}}


# ------------------------------------------------------- 1. model and attribution ----

def test_a_chosen_model_routes_by_prefix_and_is_booked_under_itself(tmp_path):
    anthropic = FakeClient([], model_ids=["claude-parent"])
    openai = FakeClient([_text("gpt says hi", 400, 40)], model_ids=["gpt-x"])
    s = _session(tmp_path)
    ctx = cli.build_tool_context(s, tmp_path, config_mod.Config(platform="mac", subagents=True),
                                 client=client_mod.Facade(anthropic, openai))
    out = tools.run_tool("spawn_subagent", {"task": "look", "model": "gpt-x"}, ctx)
    assert not out.is_error and "gpt says hi" in out.content
    assert len(openai.messages.calls) == 1 and openai.messages.calls[0]["model"] == "gpt-x"
    assert anthropic.messages.calls == []
    assert s.ledger.by_model["gpt-x"].input_tokens == 400
    assert "claude-parent" not in s.ledger.by_model


def test_a_model_the_backend_does_not_list_is_refused_with_no_call(tmp_path):
    anthropic = FakeClient([], model_ids=["claude-parent"])
    openai = FakeClient([], model_ids=["gpt-x"])
    ctx = cli.build_tool_context(_session(tmp_path), tmp_path,
                                 config_mod.Config(platform="mac", subagents=True),
                                 client=client_mod.Facade(anthropic, openai))
    out = tools.run_tool("spawn_subagent", {"task": "look", "model": "gpt-nope"}, ctx)
    assert out.is_error and "gpt-nope" in out.content and "Nothing was run" in out.content
    assert anthropic.messages.calls == [] and openai.messages.calls == []


def test_an_openai_model_with_no_openai_provider_is_refused_not_substituted(tmp_path):
    primary = FakeClient([_text("should not run")])  # lists no models at all
    ctx = cli.build_tool_context(_session(tmp_path), tmp_path,
                                 config_mod.Config(platform="mac", subagents=True), client=primary)
    out = tools.run_tool("spawn_subagent", {"task": "look", "model": "gpt-x"}, ctx)
    assert out.is_error and "gpt-x" in out.content and "OpenAI provider" in out.content
    assert primary.messages.calls == []


# ------------------------------------------------------------------- 2. roles ----

def test_a_role_narrows_tools_adds_its_preamble_and_uses_its_model(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    anthropic = FakeClient([], model_ids=["claude-parent"])
    openai = FakeClient([_msg([_call("g1", "grep", pattern="x")], "tool_use"),
                         _text("reviewed")], model_ids=["gpt-x"])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=ROLES)
    ctx = cli.build_tool_context(_session(tmp_path), tmp_path, cfg,
                                 client=client_mod.Facade(anthropic, openai))
    out = tools.run_tool("spawn_subagent", {"task": "review", "role": "reviewer"}, ctx)
    assert not out.is_error and "reviewed" in out.content
    first = openai.messages.calls[0]
    assert [t["name"] for t in first["tools"]] == ["read_file"]
    assert "ROLE-PREAMBLE" in _system_text(first)
    # grep was asked for anyway and refused, not run.
    refused = openai.messages.calls[1]["messages"][-1]["content"][0]
    assert refused["is_error"] and "grep" in refused["content"]
    assert any(r["tool"] == "grep" and r["outcome"] == "not_offered" for r in _rows(tmp_path))


def test_only_is_the_control_when_the_schema_offer_is_ignored(tmp_path):
    """The role's tool list is enforced at dispatch, not just offered."""
    anthropic = FakeClient([], model_ids=["claude-parent"])
    openai = FakeClient([], model_ids=["gpt-x"])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=ROLES)
    seen = {}
    real = agent.run_turn

    def spy(client, config, messages, ctx, on_text, **kw):
        seen["only"] = ctx.only
        seen["grep"] = tools.run_tool("grep", {"pattern": "x"}, ctx)
        return [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]

    agent.run_turn = spy
    try:
        ctx = cli.build_tool_context(_session(tmp_path), tmp_path, cfg,
                                     client=client_mod.Facade(anthropic, openai))
        tools.run_tool("spawn_subagent", {"task": "review", "role": "reviewer"}, ctx)
    finally:
        agent.run_turn = real
    assert seen["only"] == frozenset({"read_file"})
    assert seen["grep"].is_error and "not available" in seen["grep"].content


def test_an_unknown_role_is_refused_with_no_call(tmp_path):
    fc = FakeClient([_text("should not run")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=ROLES)
    ctx = cli.build_tool_context(_session(tmp_path), tmp_path, cfg, client=fc)
    out = tools.run_tool("spawn_subagent", {"task": "x", "role": "critic"}, ctx)
    assert out.is_error and "critic" in out.content and "reviewer" in out.content
    assert fc.messages.calls == []


def test_an_explicit_model_overrides_the_roles(tmp_path):
    anthropic = FakeClient([_text("claude ran")], model_ids=["claude-parent", "claude-other"])
    openai = FakeClient([], model_ids=["gpt-x"])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=ROLES)
    s = _session(tmp_path)
    ctx = cli.build_tool_context(s, tmp_path, cfg, client=client_mod.Facade(anthropic, openai))
    out = tools.run_tool("spawn_subagent",
                         {"task": "x", "role": "reviewer", "model": "claude-other"}, ctx)
    assert "claude ran" in out.content and "model claude-other" in out.content
    assert openai.messages.calls == [] and list(s.ledger.by_model) == ["claude-other"]


def test_a_deny_rule_on_a_role_refuses_and_audits_it(tmp_path):
    openai = FakeClient([_text("should not run")], model_ids=["gpt-x"])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=ROLES,
                            deny=["spawn_subagent:reviewer"])
    ctx = cli.build_tool_context(_session(tmp_path), tmp_path, cfg,
                                 client=client_mod.Facade(FakeClient([]), openai))
    out = tools.run_tool("spawn_subagent", {"task": "x", "role": "reviewer"}, ctx)
    assert out.is_error and "deny rule" in out.content
    assert openai.messages.calls == []
    row = _rows(tmp_path)[-1]
    assert row["tool"] == "spawn_subagent" and row["target"] == "reviewer"
    assert row["outcome"] == "denied"


def test_roles_are_listed_in_the_tool_description(tmp_path):
    cfg = config_mod.Config(platform="mac", subagents=True, roles=ROLES)
    schema = next(t for t in cli.build_agent_config(_session(tmp_path), cfg, tmp_path).tools
                  if t["name"] == "spawn_subagent")
    assert "reviewer: Independent review" in schema["description"]
    assert {"task", "model", "role"} <= set(schema["input_schema"]["properties"])


def test_a_role_naming_a_mutating_tool_is_a_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('platform = "mac"\nsubagents = true\n\n'
                    '[roles.reviewer]\nmodel = "gpt-x"\ntools = ["read_file"]\n\n'
                    '[roles.fixer]\ntools = ["read_file", "write_file"]\n', encoding="utf-8")
    cfg = config_mod.load_config(path)
    assert set(cfg.roles) == {"reviewer"} and cfg.model == ""
    warnings = config_mod.config_warnings(path)
    assert any("fixer" in w and "write_file" in w for w in warnings)
    # --sync-config must not lift a role's `model` to the top level.
    config_mod.sync_config(path)
    assert config_mod.load_config(path).roles["reviewer"]["model"] == "gpt-x"
    assert config_mod.load_config(path).model == ""


# ----------------------------------------------------------- 3. header and audit ----

def test_the_result_header_names_role_model_calls_tokens_and_status(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("hello")
    openai = FakeClient([_msg([_call("r1", "read_file", path="a.txt")], "tool_use", 300, 5),
                         _msg([_call("r2", "read_file", path="a.txt")], "tool_use", 310, 5),
                         _text("two reads done", 320, 7)], model_ids=["gpt-x"])
    s = _session(tmp_path)
    cfg = config_mod.Config(platform="mac", subagents=True, roles=ROLES)
    ctx = cli.build_tool_context(s, tmp_path, cfg, client=client_mod.Facade(FakeClient([]), openai))
    before = s.ledger.total_tokens
    out = tools.run_tool("spawn_subagent", {"task": "x", "role": "reviewer"}, ctx)
    header, _, body = out.content.partition("\n")
    delta = s.ledger.total_tokens - before
    assert delta == 947
    for part in ("reviewer#1", "model gpt-x", "1 tools offered", "2 tool calls",
                 f"{delta:,} tokens", "ok"):
        assert part in header
    assert body == "two reads done"
    assert header in capsys.readouterr().out  # the terminal shows the same line
    rows = _rows(tmp_path)
    child = [r for r in rows if r["tool"] == "read_file"]
    assert len(child) == 2 and all(r["agent"] == "reviewer#1" for r in child)
    parent = [r for r in rows if r["tool"] == "spawn_subagent"]
    assert parent and all("agent" not in r for r in parent)


def test_an_empty_completion_reports_status_empty(tmp_path):
    fc = FakeClient([_msg([], "end_turn")])
    ctx = cli.build_tool_context(_session(tmp_path), tmp_path,
                                 config_mod.Config(platform="mac", subagents=True), client=fc)
    out = tools.run_tool("spawn_subagent", {"task": "x"}, ctx)
    assert out.is_error
    header = out.content.splitlines()[0]
    assert header.endswith("· empty]") and "subagent#1" in header
    assert "EMPTY COMPLETION" in out.content


# ------------------------------------------------------------ 4. parallel fan-out ----

class _Stub:
    """One client for parent and children. A child is recognised by its system prompt;
    it sleeps, records when it ran, and answers its own task, so interleaving cannot mix
    answers up. The parent is scripted."""

    def __init__(self, parent_script, delay=0.3, block=None):
        self.parent = list(parent_script)
        self.delay = delay
        self.block = block or {}  # task -> Event the child waits on
        self.spans = {}
        self.lock = threading.Lock()
        self.messages = self

    def create(self, **kw):
        if "research sub-agent" not in _system_text(kw):
            with self.lock:
                return self.parent.pop(0)
        task = kw["messages"][0]["content"]
        if isinstance(task, list):
            task = task[0]["text"]
        start = time.monotonic()
        if task in self.block:
            self.block[task].wait(5)
        else:
            time.sleep(self.delay)
        if task == "boom":
            raise ValueError("child exploded")
        with self.lock:
            self.spans[task] = (start, time.monotonic(), threading.get_ident())
        return _text(f"answer:{task}")


def _parent_cfg(tmp_path, s, cfg):
    return cli.build_agent_config(s, cfg, tmp_path)


def _round(*calls):
    return _msg(list(calls), "tool_use")


def test_two_children_overlap_in_time_and_answer_in_call_order(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    stub = _Stub([_round(_call("s1", "spawn_subagent", task="alpha"),
                         _call("r1", "read_file", path="a.txt"),
                         _call("s2", "spawn_subagent", task="beta")),
                  _text("parent done")])
    s = _session(tmp_path)
    cfg = config_mod.Config(platform="mac", subagents=True)
    ctx = cli.build_tool_context(s, tmp_path, cfg, client=stub)
    t0 = time.monotonic()
    msgs = agent.run_turn(stub, _parent_cfg(tmp_path, s, cfg),
                          [{"role": "user", "content": "go"}], ctx, lambda t: None)
    wall = time.monotonic() - t0
    (a0, a1, ta), (b0, b1, tb) = stub.spans["alpha"], stub.spans["beta"]
    assert a0 < b1 and b0 < a1, "the children did not overlap"
    assert ta != tb
    assert wall < 0.55  # one child's delay, not two
    results = msgs[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["s1", "r1", "s2"]
    assert "answer:alpha" in results[0]["content"] and "hello" in results[1]["content"]
    assert "answer:beta" in results[2]["content"]
    assert all(r["content"].splitlines()[0].endswith("· ok]") for r in (results[0], results[2]))


def test_one_child_raising_does_not_lose_the_others_answer(tmp_path):
    stub = _Stub([_round(_call("s1", "spawn_subagent", task="boom"),
                         _call("s2", "spawn_subagent", task="fine")),
                  _text("parent done")], delay=0.05)
    s = _session(tmp_path)
    cfg = config_mod.Config(platform="mac", subagents=True)
    ctx = cli.build_tool_context(s, tmp_path, cfg, client=stub)
    msgs = agent.run_turn(stub, _parent_cfg(tmp_path, s, cfg),
                          [{"role": "user", "content": "go"}], ctx, lambda t: None)
    boom, fine = msgs[2]["content"]
    assert boom["is_error"] and "child exploded" in boom["content"]
    assert "· error]" in boom["content"].splitlines()[0]
    assert not fine["is_error"] and "answer:fine" in fine["content"]


def test_ctrl_c_mid_batch_answers_every_call_and_the_session_reloads(tmp_path, monkeypatch):
    slow = threading.Event()
    stub = _Stub([_round(_call("s1", "spawn_subagent", task="quick"),
                         _call("s2", "spawn_subagent", task="slow"),
                         _call("s3", "spawn_subagent", task="later"))],
                 delay=0.01, block={"slow": slow, "later": slow})
    s = _session(tmp_path)
    cfg = config_mod.Config(platform="mac", subagents=True)
    ctx = cli.build_tool_context(s, tmp_path, cfg, client=stub)
    real_wait = agent.futures.wait

    def wait(pending, timeout=None):
        done, rest = real_wait(pending, timeout=timeout)
        if "quick" in stub.spans:
            raise KeyboardInterrupt  # the user presses Ctrl-C once one child is back
        return done, rest

    monkeypatch.setattr(agent.futures, "wait", wait)
    s.messages = [{"role": "user", "content": "go"}]
    with pytest.raises(KeyboardInterrupt) as info:
        agent.run_turn(stub, _parent_cfg(tmp_path, s, cfg), s.messages, ctx, lambda t: None)
    slow.set()  # let the abandoned children finish their in-flight call
    history = info.value.luban_messages
    asked = [b["id"] for b in history[-2]["content"] if b["type"] == "tool_use"]
    answered = {r["tool_use_id"]: r for r in history[-1]["content"]}
    assert set(asked) == set(answered) == {"s1", "s2", "s3"}
    assert [r["tool_use_id"] for r in history[-1]["content"]] == ["s1", "s2", "s3"]
    assert "answer:quick" in answered["s1"]["content"] and not answered["s1"]["is_error"]
    assert answered["s2"]["is_error"] and "Interrupted" in answered["s2"]["content"]
    assert agent.sanitize_history(history) == history  # nothing left unanswered
    s.messages = history
    cli.save_session(s)
    data = sessions_mod.load(s.session_id)
    assert data["messages"] == json.loads(json.dumps(history))
    reloaded = _session(tmp_path)
    cli.restore_session(reloaded, data)
    assert agent.sanitize_history(reloaded.messages) == reloaded.messages


def test_a_single_subagent_call_is_not_threaded(tmp_path):
    stub = _Stub([_round(_call("s1", "spawn_subagent", task="solo")), _text("done")],
                 delay=0.01)
    s = _session(tmp_path)
    cfg = config_mod.Config(platform="mac", subagents=True)
    ctx = cli.build_tool_context(s, tmp_path, cfg, client=stub)
    agent.run_turn(stub, _parent_cfg(tmp_path, s, cfg),
                   [{"role": "user", "content": "go"}], ctx, lambda t: None)
    assert stub.spans["solo"][2] == threading.get_ident()
