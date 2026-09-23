"""R3 — nested runs are counted and bounded; the audit tells a refusal from a failure."""
import json
import subprocess
import sys
from types import SimpleNamespace

from luban import agent, audit as audit_mod, cli, config as config_mod, tools, usage as usage_mod

from tests.conftest import FakeBlock, FakeClient, FakeMessage


def _use(n_in, n_out):
    return SimpleNamespace(input_tokens=n_in, output_tokens=n_out,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _msg(content, stop, n_in=100, n_out=10):
    m = FakeMessage(content, stop)
    m.usage = _use(n_in, n_out)
    return m


def _read(tid, path="a.txt"):
    return FakeBlock("tool_use", id=tid, name="read_file", input={"path": path})


def _rows(tmp_path):
    p = tmp_path / "audit.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ---------------------------------------------------------------- nested accounting ----

def test_every_child_call_reaches_the_ledger_once_as_a_side_call(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    fc = FakeClient([_msg([_read("t1")], "tool_use", 1_000, 20),
                     _msg([FakeBlock("text", text="found it")], "end_turn", 1_200, 30)])
    s = cli.Session(model="m", max_tokens=100, auto=False, stream=False, project=str(tmp_path))
    s.ledger.add(usage_mod.Usage(input_tokens=50_000, output_tokens=5), "m")  # the parent
    cfg = config_mod.Config(platform="mac", subagents=True)
    ctx = cli.build_tool_context(s, tmp_path, cfg, client=fc)
    assert "found it" in ctx.subagent("look")
    assert s.ledger.calls == 3
    assert s.ledger.input_tokens == 52_200 and s.ledger.output_tokens == 55
    assert s.ledger.context_tokens == 50_000  # still the PARENT's window
    assert s.ledger.by_model["m"].calls == 3 and s.ledger.by_model["m"].input_tokens == 52_200


def test_a_failed_child_call_still_counts_what_it_spent(tmp_path):
    (tmp_path / "a.txt").write_text("hello")

    fc = FakeClient([_msg([_read("t1")], "tool_use", 700, 7)])  # second call has no script
    s = cli.Session(model="m", max_tokens=100, auto=False, stream=False, project=str(tmp_path))
    ctx = cli.build_tool_context(s, tmp_path, config_mod.Config(platform="mac", subagents=True),
                                 client=fc)
    out = tools.run_tool("spawn_subagent", {"task": "look"}, ctx)
    assert out.is_error or "error" in out.content.lower()
    assert s.ledger.calls == 1 and s.ledger.input_tokens == 700


def test_a_child_run_ends_with_an_answer_at_its_round_budget(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    script = [_msg([_read(f"t{i}")], "tool_use") for i in range(3)]
    script.append(_msg([FakeBlock("text", text="partial: read a.txt three times")], "end_turn"))
    fc = FakeClient(script)
    cfg = agent.AgentConfig("m", 100, stream=False, max_tool_rounds=3,
                            tools=[t for t in tools.TOOLS if t["name"] == "read_file"])
    ctx = tools.ToolContext(tmp_path, lambda p: False, lambda *a: None, lambda c: None)
    msgs = agent.run_turn(fc, cfg, [{"role": "user", "content": "go"}], ctx, lambda t: None)
    assert cli._final_text(msgs).startswith("partial")
    last_call = fc.messages.calls[-1]
    assert not last_call.get("tools")  # the answering call offers nothing
    notice = msgs[-2]["content"][-1]["content"]
    assert "tool budget reached" in notice
    assert all(m["role"] != "user" or not isinstance(m["content"], str)
               or m["content"] == "go" for m in msgs)  # no text block after a result


def test_a_child_that_calls_a_tool_after_the_budget_is_stopped_cleanly(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    fc = FakeClient([_msg([_read("t0")], "tool_use"),
                     _msg([FakeBlock("text", text="one more"), _read("t1")], "tool_use")])
    cfg = agent.AgentConfig("m", 100, stream=False, max_tool_rounds=1,
                            tools=[t for t in tools.TOOLS if t["name"] == "read_file"])
    ctx = tools.ToolContext(tmp_path, lambda p: False, lambda *a: None, lambda c: None)
    msgs = agent.run_turn(fc, cfg, [{"role": "user", "content": "go"}], ctx, lambda t: None)
    assert len(fc.messages.calls) == 2
    assert not any(b.get("type") == "tool_use" and b.get("id") == "t1"
                   for m in msgs if isinstance(m["content"], list) for b in m["content"])


def test_the_child_window_is_bounded_between_calls():
    big = lambda tid: {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "x" * 50_000}]}
    call = lambda tid: {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": "read_file", "input": {}}]}
    msgs = [{"role": "user", "content": "go"}]
    for i in range(6):
        msgs += [call(f"t{i}"), big(f"t{i}")]
    out = cli.bound_subagent(msgs, budget_chars=120_000)
    assert cli._history_chars(out) <= 120_000
    assert out[-1]["content"][0]["content"] == "x" * 50_000  # the live result untouched
    assert "dropped" in out[2]["content"][0]["content"]


# ---------------------------------------------------------------- audit outcomes ----

def _ctx(tmp_path, cfg=None, auto=False):
    s = cli.Session(model="m", max_tokens=10, auto=auto, stream=False, project=str(tmp_path))
    ctx = cli.build_tool_context(s, tmp_path, cfg or config_mod.Config(platform="mac"))
    return ctx


def test_each_outcome_is_distinct_in_the_trail(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    monkeypatch.setattr(cli.ui, "render_command", lambda *a: None)
    monkeypatch.setattr(cli.ui, "ask_confirm", lambda p, input_fn=input: "no")
    py = sys.executable
    cfg = config_mod.Config(platform="mac", deny=["run_command:rm *"],
                            allow=[f"run_command:{py} *"])
    ctx = _ctx(tmp_path, cfg)
    tools.run_tool("write_file", {"path": "a.txt", "content": "x"}, ctx)        # declined
    tools.run_tool("run_command", {"command": "rm -rf x"}, ctx)                   # denied
    tools.run_tool("run_command", {"command": f'{py} -c "print(1)"'}, ctx)       # ok
    tools.run_tool("run_command", {"command": f'{py} -c "raise SystemExit(3)"'}, ctx)
    tools.run_tool("run_command", {"command": f'{py} -c "import time; time.sleep(5)"',
                                   "timeout": 1}, ctx)                            # timed out
    monkeypatch.setattr(tools, "_spawn", lambda *a, **k: (_ for _ in ()).throw(OSError("no sh")))
    tools.run_tool("run_command", {"command": f"{py} -V"}, ctx)                  # launch failed
    tools.run_tool("no_such_tool", {}, ctx)                                       # unknown
    got = [(r["tool"], r["outcome"], r.get("exit_code")) for r in _rows(tmp_path)]
    assert got == [
        ("write_file", "declined", None),
        ("run_command", "denied", None),
        ("run_command", "ok", 0),
        ("run_command", "nonzero_exit", 3),
        ("run_command", "timed_out", None),
        ("run_command", "launch_failed", None),
        ("no_such_tool", "unknown", None),
    ]
    assert not (tmp_path / "a.txt").exists()
    # the fields existing consumers read are all still there
    assert all({"ts", "project", "session", "tool", "target", "decision", "is_error"} <= r.keys()
               for r in _rows(tmp_path))


def test_a_tool_the_turn_never_offered_is_recorded_exactly_once(tmp_path):
    fc = FakeClient([
        FakeMessage([FakeBlock("tool_use", id="t1", name="write_file",
                               input={"path": "x.txt", "content": "no"})], "tool_use"),
        FakeMessage([FakeBlock("text", text="ok")], "end_turn")])
    ctx = _ctx(tmp_path)
    cfg = agent.AgentConfig("m", 100, stream=False,
                            tools=[t for t in tools.TOOLS if t["name"] == "read_file"])
    agent.run_turn(fc, cfg, [{"role": "user", "content": "hi"}], ctx, lambda t: None)
    rows = _rows(tmp_path)
    assert [(r["tool"], r["outcome"]) for r in rows] == [("write_file", "not_offered")]


def test_a_background_job_s_exit_code_reaches_the_trail(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ui, "render_command", lambda *a: None)
    tools.kill_all_jobs()
    ctx = cli.build_tool_context(
        cli.Session(model="m", max_tokens=10, auto=True, stream=False, project=str(tmp_path)),
        tmp_path, config_mod.Config(platform="mac"))
    started = tools.run_tool("run_command", {"command": f'{sys.executable} -c "raise SystemExit(4)"',
                                             "background": True}, ctx)
    assert not started.is_error
    handle = next(iter(tools._JOBS))
    tools._JOBS[handle].proc.wait(timeout=10)
    tools.run_tool("read_output", {"handle": handle}, ctx)
    row = _rows(tmp_path)[-1]
    assert row["tool"] == "read_output" and row["outcome"] == "nonzero_exit" and row["exit_code"] == 4
    tools.kill_all_jobs()


def test_an_unwritable_trail_is_said_once_and_never_stops_the_work(tmp_path, monkeypatch):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    (tmp_path / "audit.jsonl").mkdir()  # a directory where the file should be
    ctx = _ctx(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    for _ in range(3):
        assert not tools.run_tool("read_file", {"path": "a.txt"}, ctx).is_error
    assert sum("audit log unavailable" in t for t in printed) == 1
