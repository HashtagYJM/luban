"""R2 — whole workflows, judged by what the user sees and what is on disk afterwards."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from luban import agent, cli, config as config_mod
from luban import sessions as sessions_mod, tools

from tests.conftest import FakeBlock, FakeClient, FakeMessage

REPO = Path(__file__).resolve().parents[1]


def _session(tmp_path, **kw):
    return cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       project=str(tmp_path), **kw)


def _write(tid, path, content):
    return FakeBlock("tool_use", id=tid, name="write_file", input={"path": path, "content": content})


# ------------------------------------------------ process death after a side effect ----

_CHILD = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    from luban import cli, sessions as sessions_mod, tools
    root = Path(sys.argv[1]); mutate = sys.argv[2] == "write"
    sessions_mod.SESSIONS_DIR = root / "sessions"
    cli.maintain_context = lambda *a: None
    s = cli.Session(model="m", max_tokens=10, auto=True, stream=False, project=str(root))
    s.messages = [{"role": "user", "content": "make the file"}]
    cli.save_session(s)
    print(s.session_id, flush=True)
    ctx = cli.build_tool_context(s, root)
    name = "write_file" if mutate else "read_file"
    inp = {"path": "out.txt", "content": "made"} if mutate else {"path": "out.txt"}
    (root / "out.txt").touch()
    out = tools.run_tool(name, inp, ctx)
    msgs = s.messages + [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": name, "input": inp}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": out.content}]}]
    cli.bound_turn(s, None, None, root)(msgs)
    os._exit(9)  # the terminal closes, the power goes: no finally, no exit journal
""")


@pytest.mark.parametrize("mutate", [True, False])
def test_a_process_killed_after_a_write_keeps_the_write_on_disk(tmp_path, mutate):
    env = {**os.environ, "PYTHONPATH": str(REPO), "LUBAN_HOME": str(tmp_path / "home")}
    run = subprocess.run([sys.executable, "-c", _CHILD, str(tmp_path),
                          "write" if mutate else "read"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert run.returncode == 9, run.stderr
    sid = run.stdout.strip().splitlines()[0]
    saved = json.loads((tmp_path / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))
    tool_uses = [b for m in saved["messages"] if isinstance(m["content"], list)
                 for b in m["content"] if b.get("type") == "tool_use"]
    if mutate:
        assert (tmp_path / "out.txt").read_text() == "made"
        assert [b["name"] for b in tool_uses] == ["write_file"]  # the record survived
    else:
        # a read is not saved per round: the loss is tokens, not truth — and one write per
        # mutating round is what keeps a synced home quiet
        assert tool_uses == []


# ------------------------------------------------ edit → interrupt → save → restart ----

def test_an_interrupted_turn_after_an_edit_resumes_with_the_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    monkeypatch.setattr(cli, "maintain_context", lambda *a: None)
    monkeypatch.setattr(cli, "_print_last_exchange", lambda m: None)
    s = _session(tmp_path)
    ctx = cli.build_tool_context(s, tmp_path)

    fc = FakeClient([FakeMessage([_write("t1", "f.txt", "v1")], "tool_use")])
    real_create = fc.messages.create

    def create(**kw):
        if len(fc.messages.calls) >= 1:
            fc.messages.calls.append(kw)
            raise KeyboardInterrupt
        return real_create(**kw)
    fc.messages.create = create
    s.messages.append({"role": "user", "content": "write f"})
    cfg = agent.AgentConfig("m", 100, stream=False)
    cfg.between_calls = cli.bound_turn(s, fc, config_mod.Config(platform="mac"), tmp_path)
    with pytest.raises(KeyboardInterrupt):
        s.messages = agent.run_turn(fc, cfg, s.messages, ctx, lambda t: None)
    cli.abandon_turn(s)
    # restart: a fresh process would load the file, not the object
    fresh = _session(tmp_path)
    cli.restore_session(fresh, sessions_mod.load(s.session_id))
    assert (tmp_path / "f.txt").read_text() == "v1"
    names = [b["name"] for m in fresh.messages if isinstance(m["content"], list)
             for b in m["content"] if b.get("type") == "tool_use"]
    assert names == ["write_file"]
    # and the history is sendable: the next prompt goes straight after it
    fresh.messages.append({"role": "user", "content": "next"})
    assert agent.sanitize_history(fresh.messages) == fresh.messages


# ------------------------------------------------ provider switch → fold → tool call ----

def test_a_fold_after_a_provider_switch_leaves_a_sendable_history(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 1.0)
    monkeypatch.setattr(cli, "FOLD_MIN_TOKENS", 100)
    monkeypatch.setattr(cli, "standing_tokens", lambda *a: 0)

    class FB:
        type, text = "text", "SUMMARY"
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: type("M", (), {"content": [FB()]})())
    think = {"type": "thinking", "thinking": "hm", "signature": "SIG", "_provider": "anthropic"}
    msgs = []
    for i in range(30):
        msgs += [{"role": "user", "content": f"q{i} " + "q" * 400},
                 {"role": "assistant", "content": [think, {"type": "tool_use", "id": f"t{i}",
                                                            "name": "read_file", "input": {}}]},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                               "content": "r" * 400}]},
                 {"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]}]
    s = _session(tmp_path, messages=msgs)
    s.model = "gpt-5.6"  # the /model switch
    assert cli.fold_history(s, object(), config_mod.Config(platform="mac", warn_tokens=10_000),
                            tmp_path)
    # the next send: history opens on the user, and every tool_use is answered
    sent = agent.sanitize_history(s.messages)
    assert sent[0]["role"] == "user" and sent == s.messages
    for i, m in enumerate(sent):
        uses = [b["id"] for b in m["content"] if isinstance(m["content"], list)
                and isinstance(b, dict) and b.get("type") == "tool_use"]
        if uses:
            nxt = {b.get("tool_use_id") for b in sent[i + 1]["content"]}
            assert set(uses) <= nxt


# ------------------------------------------------ two sessions, one project ----

def test_two_sessions_in_one_project_keep_their_own_files_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    a, b = _session(tmp_path), _session(tmp_path)
    a.messages = [{"role": "user", "content": "thread A"},
                  {"role": "assistant", "content": [{"type": "text", "text": "A"}]}]
    b.messages = [{"role": "user", "content": "thread B"},
                  {"role": "assistant", "content": [{"type": "text", "text": "B"}]}]
    cli.save_session(a)
    cli.save_session(b)
    arch_a, arch_b = cli.archive_session(a), cli.archive_session(b)
    assert a.session_id != b.session_id and arch_a != arch_b
    b.messages.append({"role": "user", "content": "more B"})
    cli.save_session(b)
    assert sessions_mod.load(a.session_id)["messages"] == a.messages
    assert len(sessions_mod.load(b.session_id)["messages"]) == 3
    assert {h["id"] for h in sessions_mod.list_sessions(str(tmp_path))} == {a.session_id, b.session_id}
