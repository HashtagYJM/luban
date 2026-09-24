"""A process that dies part-way through a tool round keeps the record of what finished.

Reproduced through the real entry point: one model response asks for a write and then a
command; the command kills luban itself (SIGKILL, no cleanup). The write is on disk. The
saved session used to hold no record of it, because the session was saved only after the
whole round.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from luban import agent, cli, sessions as sessions_mod

REPO = Path(__file__).resolve().parents[1]

STUB = textwrap.dedent('''
    from types import SimpleNamespace as NS
    def _u(): return NS(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    class Messages:
        def create(self, **kw):
            return NS(stop_reason="tool_use", usage=_u(), content=[
                NS(type="tool_use", id="w1", name="write_file",
                   input={"path": "made.txt", "content": "written once\\n"}),
                NS(type="tool_use", id="k1", name="run_command",
                   input={"command": "kill -9 $PPID"})])
    class Client:
        messages = Messages()
    def build_client(): return Client()
''')


@pytest.mark.skipif(sys.platform == "win32", reason="kills its parent with a POSIX signal")
def test_a_write_before_the_process_dies_mid_round_is_in_the_saved_session(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    home.mkdir(); proj.mkdir()
    (tmp_path / "stub_client.py").write_text(STUB)
    env = {**os.environ, "PYTHONPATH": str(REPO), "LUBAN_HOME": str(home),
           "LUBAN_CLIENT_LOCAL": str(tmp_path / "stub_client.py")}
    run = subprocess.run([sys.executable, "-m", "luban", "--dir", str(proj), "--auto",
                          "--no-stream", "--model", "stub"],
                         input="make the file\n", capture_output=True, text=True,
                         env=env, timeout=60)
    assert run.returncode == -9, (run.returncode, run.stderr[-500:])
    assert (proj / "made.txt").read_text() == "written once\n"

    files = list((home / "sessions").glob("*.json"))
    assert len(files) == 1, "the session file must exist after a mutation"
    data = json.loads(files[0].read_text(encoding="utf-8"))
    names = [b["name"] for m in data["messages"] if isinstance(m["content"], list)
             for b in m["content"] if b.get("type") == "tool_use"]
    results = [b for m in data["messages"] if isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result"]
    # the finished write is recorded with its result; the command that never finished is
    # recorded as NOT finished, so a resumed model is told rather than left guessing
    assert names == ["write_file", "run_command"]
    by_id = {r["tool_use_id"]: r for r in results}
    assert not by_id["w1"]["is_error"]
    assert by_id["k1"]["is_error"] and by_id["k1"]["content"] == cli.UNFINISHED

    # resume: the history is sendable as saved, and nothing is replayed
    restored = cli.Session(model="stub", max_tokens=100, auto=True, stream=False,
                           project=str(proj))
    import luban.cli as c
    c._print_last_exchange = lambda m: None
    cli.restore_session(restored, data)
    assert agent.sanitize_history(restored.messages) == restored.messages
    assert restored.messages[0] == {"role": "user", "content": restored.messages[0]["content"]}
    (proj / "made.txt").write_text("edited by hand after the crash\n")
    calls = []
    from tests.conftest import FakeBlock, FakeClient, FakeMessage
    fc = FakeClient([FakeMessage([FakeBlock("text", text="picking up")], "end_turn")])
    ctx = cli.build_tool_context(restored, proj)
    restored.messages.append({"role": "user", "content": "carry on"})
    out = agent.run_turn(fc, agent.AgentConfig("stub", 100, stream=False),
                         restored.messages, ctx, lambda t: None)
    assert (proj / "made.txt").read_text() == "edited by hand after the crash\n"
    sent = fc.messages.calls[0]["messages"]
    assert any(isinstance(m["content"], list) and any(
        b.get("type") == "tool_result" and b.get("tool_use_id") == "w1" for b in m["content"])
        for m in sent), "the model is told the write happened"
    assert out[-1]["content"][0]["text"] == "picking up"


def _round(*blocks):
    from tests.conftest import FakeBlock, FakeClient, FakeMessage
    return FakeClient([FakeMessage(list(blocks), "tool_use"),
                       FakeMessage([FakeBlock("text", text="done")], "end_turn")])


def test_between_two_writes_the_disk_holds_the_first_and_not_the_second(tmp_path, monkeypatch):
    from tests.conftest import FakeBlock
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, project=str(tmp_path))
    s.messages = [{"role": "user", "content": "two files"}]
    fc = _round(FakeBlock("tool_use", id="a", name="write_file", input={"path": "a.txt", "content": "A"}),
                FakeBlock("tool_use", id="b", name="write_file", input={"path": "b.txt", "content": "B"}))
    snapshots = []
    hook = cli.checkpoint_tool(s)

    def after(name, inp, out, msgs):
        hook(name, inp, out, msgs)
        snapshots.append(sessions_mod.load(s.session_id)["messages"])
    cfg = agent.AgentConfig("m", 100, stream=False, after_tool=after)
    agent.run_turn(fc, cfg, s.messages, cli.build_tool_context(s, tmp_path), lambda t: None)
    first = snapshots[0]
    ids = lambda msgs, kind, key: [b[key] for m in msgs if isinstance(m["content"], list)
                                   for b in m["content"] if b.get("type") == kind]
    assert ids(first, "tool_use", "id") == ["a", "b"]
    results = {b["tool_use_id"]: b for m in first if isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result"}
    assert not results["a"]["is_error"] and results["b"]["content"] == cli.UNFINISHED
    assert agent.sanitize_history(first) == first
    assert ids(snapshots[1], "tool_use", "id") == ["a", "b"]


def test_a_round_of_reads_writes_nothing_and_a_mixed_round_saves_once_per_change(tmp_path, monkeypatch):
    from tests.conftest import FakeBlock
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    (tmp_path / "r.txt").write_text("r")
    saves = []
    monkeypatch.setattr(cli, "save_session", lambda s: saves.append(len(s.messages)))
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, project=str(tmp_path))
    read = lambda i: FakeBlock("tool_use", id=f"r{i}", name="read_file", input={"path": "r.txt"})
    fc = _round(read(1), read(2), read(3))
    cfg = agent.AgentConfig("m", 100, stream=False, after_tool=cli.checkpoint_tool(s))
    agent.run_turn(fc, cfg, [{"role": "user", "content": "look"}], cli.build_tool_context(s, tmp_path), lambda t: None)
    assert saves == []
    fc = _round(read(1), FakeBlock("tool_use", id="w", name="write_file",
                                   input={"path": "w.txt", "content": "W"}), read(2))
    agent.run_turn(fc, cfg, [{"role": "user", "content": "look and write"}],
                   cli.build_tool_context(s, tmp_path), lambda t: None)
    assert len(saves) == 1


def test_a_refused_call_saves_nothing_and_an_unsavable_result_does_not_stop_the_round(tmp_path, monkeypatch):
    from tests.conftest import FakeBlock
    printed, saves = [], []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    monkeypatch.setattr(cli.ui, "ask_confirm", lambda p, input_fn=input: "no")
    s = cli.Session(model="m", max_tokens=100, auto=False, stream=False, project=str(tmp_path))
    real = cli.save_session
    monkeypatch.setattr(cli, "save_session", lambda sess: saves.append(1) or real(sess))
    fc = _round(FakeBlock("tool_use", id="d", name="write_file", input={"path": "no.txt", "content": "x"}))
    cfg = agent.AgentConfig("m", 100, stream=False, after_tool=cli.checkpoint_tool(s))
    agent.run_turn(fc, cfg, [{"role": "user", "content": "try"}], cli.build_tool_context(s, tmp_path), lambda t: None)
    assert saves == [] and not (tmp_path / "no.txt").exists()
    # a lone surrogate in a result cannot be encoded to disk: warn, keep going
    s.auto = True
    fc = _round(FakeBlock("tool_use", id="a", name="write_file", input={"path": "a.txt", "content": "x\ud800y"}),
                FakeBlock("tool_use", id="b", name="write_file", input={"path": "b.txt", "content": "fine"}))
    agent.run_turn(fc, cfg, [{"role": "user", "content": "two"}], cli.build_tool_context(s, tmp_path), lambda t: None)
    assert (tmp_path / "b.txt").read_text() == "fine"
    assert any("could not save session" in t for t in printed)
