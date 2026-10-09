"""E56 phase 2 — a scoped writer sub-agent.

A role with `write` and/or `commands` may edit files inside its globs and run its listed
commands, under the parent's permission rules and auto mode; one writer per checkout at a
time; writer calls never join the parallel batch; the result lists every file changed and
command run. Every other child stays enforced read-only.
"""
import json
import os
from types import SimpleNamespace

import pytest

from luban import agent, cli, config as config_mod, live, tools
from tests.conftest import FakeBlock, FakeClient, FakeMessage


@pytest.fixture(autouse=True)
def _live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    live._state.clear()


def _msg(content, stop):
    m = FakeMessage(content, stop)
    m.usage = SimpleNamespace(input_tokens=10, output_tokens=2,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return m


def _text(t):
    return _msg([FakeBlock("text", text=t)], "end_turn")


def _call(tid, name, **inp):
    return FakeBlock("tool_use", id=tid, name=name, input=inp)


def _round(*calls):
    return _msg(list(calls), "tool_use")


def _session(root, auto=True):
    s = cli.Session(model="m", max_tokens=100, auto=auto, stream=False, project=str(root))
    s.session_id = "parent-1"
    return s


def _rows(root):
    p = root / "audit.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


FIXER = {"fixer": {"model": "", "prompt": "fix it", "description": "writer", "tools": None,
                   "write": ["src/**", "tests/**"], "commands": ["echo *"]}}
READER = {"reader": {"model": "", "prompt": "", "description": "reads", "tools": None,
                     "write": None, "commands": None}}


def _project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    return tmp_path


def _spawn(root, cfg, client, task="fix", role="fixer", auto=True, confirm=None, printed=None):
    s = _session(root, auto=auto)
    ctx = cli.build_tool_context(s, root, cfg, client=client)
    if confirm is not None:
        ctx = ctx.__class__(**{**ctx.__dict__, "confirm": confirm})
    return tools.run_tool("spawn_subagent", {"task": task, "role": role}, ctx), ctx


# ------------------------------------------------------------- scope and tools ----

def test_a_writer_edits_in_scope_runs_listed_commands_and_is_refused_elsewhere(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([
        _round(_call("w1", "write_file", path="src/a.py", content="x = 2\n"),
               _call("w2", "write_file", path="README.md", content="nope\n"),
               _call("c1", "run_command", command="echo hi"),
               _call("c2", "run_command", command="rm -rf x"),
               _call("c3", "run_command", command="echo bg", background=True)),
        _text("done"),
    ])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client)
    assert not out.is_error, out.content
    assert (root / "src" / "a.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (root / "README.md").read_text(encoding="utf-8") == "readme\n"
    results = {r["tool_use_id"]: r for r in client.messages.calls[1]["messages"][2]["content"]}
    assert "outside this role's write scope" in results["w2"]["content"]
    assert "hi" in results["c1"]["content"]
    assert "outside this role's allowed commands" in results["c2"]["content"]
    assert "background" in results["c3"]["content"]
    header = out.content.splitlines()
    assert header[0].startswith("[fixer#1 ·") and "· ok]" in header[0]
    assert header[1] == "[files changed: src/a.py]"
    assert header[2] == "[commands run: echo hi]"
    rows = [r for r in _rows(root) if r.get("agent") == "fixer#1"]
    outcomes = {r["tool"] + ":" + str(r.get("target", "")): r["outcome"] for r in rows}
    assert outcomes["write_file:README.md"] == "out_of_scope"
    assert outcomes["run_command:rm -rf x"] == "out_of_scope"
    assert not list(live.LIVE_DIR.glob("writer-*"))  # lock released after the run


def test_a_read_only_role_is_never_offered_a_write_even_if_the_model_asks(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([_round(_call("w1", "write_file", path="src/a.py", content="x = 3\n")),
                         _text("tried")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=READER)
    out, _ = _spawn(root, cfg, client, role="reader")
    assert (root / "src" / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "write_file" not in [t["name"] for t in client.messages.calls[0]["tools"]]
    assert "not available" in client.messages.calls[1]["messages"][2]["content"][0]["content"]
    assert "[files changed" not in out.content


def test_the_grant_is_the_scope_in_config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('subagents = true\n\n'
                 '[roles.bad]\ntools = ["read_file", "edit_file"]\n\n'
                 '[roles.cmdless]\ntools = ["run_command"]\n\n'
                 '[roles.ok]\nwrite = ["src/**"]\n\n'
                 '[roles.narrow]\nwrite = ["src/**"]\ntools = ["edit_file"]\n', encoding="utf-8")
    cfg = config_mod.load_config(p)
    assert set(cfg.roles) == {"ok", "narrow"}
    warnings = " ".join(config_mod.parse_roles(
        __import__("tomllib").loads(p.read_text(encoding="utf-8"))["roles"])[1])
    assert "edit_file needs write" in warnings and "run_command needs commands" in warnings
    assert tools.writer_roles(cfg.roles) == {"ok", "narrow"}
    desc = tools.subagent_tool(cfg.roles)["description"]
    assert "ok: " in desc and "[WRITER: edits src/**]" in desc


def test_a_deny_rule_beats_the_roles_write_scope(tmp_path):
    root = _project(tmp_path)
    (root / "src" / "secret.py").write_text("s = 1\n", encoding="utf-8")
    client = FakeClient([_round(_call("w1", "write_file", path="src/secret.py", content="s = 2\n")),
                         _text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER,
                            deny=["write_file:src/secret.py"])
    _spawn(root, cfg, client)
    assert (root / "src" / "secret.py").read_text(encoding="utf-8") == "s = 1\n"
    assert "deny" in client.messages.calls[1]["messages"][2]["content"][0]["content"]


# ------------------------------------------------------------------ the lock ----

def test_a_writer_is_refused_while_another_process_holds_the_checkout(tmp_path, monkeypatch):
    root = _project(tmp_path)
    live.LIVE_DIR.mkdir(parents=True)
    live._lock_path(root).write_text(json.dumps(
        {"pid": 424242, "session": "other-1", "label": "fixer#7", "since": "2026-10-09T10:00:00"}),
        encoding="utf-8")
    monkeypatch.setattr(live, "_pid_alive", lambda pid: True)
    client = FakeClient([_text("never")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client)
    assert out.is_error and "fixer#7 in session other-1" in out.content
    assert client.messages.calls == []


def test_a_stale_lock_is_taken_over_and_released_after_the_run(tmp_path, monkeypatch):
    root = _project(tmp_path)
    live.LIVE_DIR.mkdir(parents=True)
    live._lock_path(root).write_text(json.dumps({"pid": 424242, "label": "fixer#7"}),
                                     encoding="utf-8")
    monkeypatch.setattr(live, "_pid_alive", lambda pid: pid == os.getpid())
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    client = FakeClient([_text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client)
    assert not out.is_error
    assert any("took over a stale writer lock" in p for p in printed)
    assert not live._lock_path(root).exists()


def test_the_lock_is_released_even_when_the_child_fails(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([])  # create() raises: scripted list is empty
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client)
    assert out.is_error and "Subagent failed" in out.content
    assert not live._lock_path(root).exists()


# ------------------------------------------------------------- never parallel ----

def test_writer_calls_stay_out_of_the_parallel_batch(tmp_path, monkeypatch):
    root = _project(tmp_path)
    batched = []
    real = agent._run_parallel

    def spy(batch, ctx):
        batched.append([b.input.get("role") for b in batch])
        return real(batch, ctx)

    monkeypatch.setattr(agent, "_run_parallel", spy)
    client = FakeClient([
        _round(_call("s1", "spawn_subagent", task="r1", role="reader"),
               _call("s2", "spawn_subagent", task="w1", role="fixer"),
               _call("s3", "spawn_subagent", task="r2", role="reader"),
               _call("s4", "spawn_subagent", task="w2", role="fixer")),
        _text("child answer"), _text("child answer"), _text("child answer"),
        _text("child answer"), _text("parent done"),
    ])
    cfg = config_mod.Config(platform="mac", subagents=True, roles={**FIXER, **READER})
    s = _session(root)
    ctx = cli.build_tool_context(s, root, cfg, client=client)
    msgs = agent.run_turn(client, cli.build_agent_config(s, cfg, root),
                          [{"role": "user", "content": "go"}], ctx, lambda t: None)
    assert batched == [["reader", "reader"]]  # only the readers fanned out
    results = msgs[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["s1", "s2", "s3", "s4"]
    assert results[1]["content"].startswith("[fixer#") and results[3]["content"].startswith("[fixer#")
    assert not results[1]["is_error"] and not results[3]["is_error"]


# ------------------------------------------------------------- approvals ----

def test_with_auto_off_the_parent_is_asked_under_the_childs_label(tmp_path, monkeypatch):
    root = _project(tmp_path)
    asked = []
    monkeypatch.setattr(cli.ui, "ask_confirm", lambda p: asked.append(p) or "no")
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    client = FakeClient([_round(_call("w1", "write_file", path="src/a.py", content="x = 9\n")),
                         _text("declined so stopping")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client, auto=False)
    assert asked == ["[fixer#1] Write src/a.py?"]
    assert (root / "src" / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "declined" in client.messages.calls[1]["messages"][2]["content"][0]["content"]
    assert "[files changed" not in out.content


def test_under_auto_a_writer_does_not_ask(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(cli.ui, "ask_confirm", lambda p: (_ for _ in ()).throw(AssertionError("asked")))
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    client = FakeClient([_round(_call("w1", "edit_file", path="src/a.py", old_string="x = 1", new_string="x = 5")),
                         _text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client, auto=True)
    assert (root / "src" / "a.py").read_text(encoding="utf-8") == "x = 5\n"
    assert "[files changed: src/a.py]" in out.content
