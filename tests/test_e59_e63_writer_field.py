"""E59-E63 writer sub-agent field fixes."""
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


def _results(client):
    return {r["tool_use_id"]: r for r in client.messages.calls[1]["messages"][2]["content"]}


def _system(client):
    system = client.messages.calls[0]["system"]
    if isinstance(system, list):
        system = "".join(b["text"] for b in system)
    return system


def _outside(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "lib.py").write_text("secret = 42\n", encoding="utf-8")
    return outside


# ------------------------------------------------------------------ E60 ----

def test_e60_chained_or_redirected_commands_are_refused(tmp_path):
    root = _project(tmp_path)
    cmds = ["echo ok & echo bad", "echo ok && echo bad", "echo ok | more", "echo ok > src/a.py",
            "echo ok < x", "echo ok; echo bad", "echo $(whoami)", "echo `whoami`"]
    client = FakeClient([
        _round(*[_call(f"c{i}", "run_command", command=c) for i, c in enumerate(cmds)]),
        _text("done"),
    ])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client)
    results = _results(client)
    for i in range(len(cmds)):
        assert "may not chain, pipe, redirect or substitute" in results[f"c{i}"]["content"], cmds[i]
    assert (root / "src" / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "[commands run" not in out.content
    rows = [r for r in _rows(root) if r.get("agent") == "fixer#1" and r["tool"] == "run_command"]
    assert rows
    assert all(r["outcome"] == "out_of_scope" for r in rows)


def test_e60_a_plain_listed_command_still_runs(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([_round(_call("c0", "run_command", command="echo plain-ok")), _text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client)
    assert "plain-ok" in _results(client)["c0"]["content"]
    assert "[commands run: echo plain-ok]" in out.content


# ------------------------------------------------------------------ E61 ----

def test_e61_a_writer_is_told_what_it_may_do_and_gets_the_platform_line(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([_text("done")])
    cfg = config_mod.Config(platform="windows", subagents=True, roles=FIXER)
    _spawn(root, cfg, client)
    system = _system(client)
    assert "WRITER sub-agent" in system
    assert "`src/**`" in system
    assert "`echo *`" in system
    assert "cmd.exe" in system
    assert "You cannot change anything" not in system


def test_e61_a_read_only_child_keeps_the_read_only_prompt_and_no_platform_line(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([_text("done")])
    cfg = config_mod.Config(platform="windows", subagents=True, roles=READER)
    _spawn(root, cfg, client, role="reader")
    system = _system(client)
    assert "You cannot change anything" in system
    assert "cmd.exe" not in system
    assert "WRITER" not in system


def test_e61_a_command_only_writer_gets_the_platform_line_but_no_write_promise(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([_text("done")])
    roles = {"checker": {"model": "", "prompt": "", "description": "runs checks", "tools": None,
                         "write": None, "commands": ["echo *"]}}
    cfg = config_mod.Config(platform="windows", subagents=True, roles=roles)
    _spawn(root, cfg, client, role="checker")
    system = _system(client)
    assert "cmd.exe" in system
    assert "edit and write files" not in system


# ------------------------------------------------------------------ E62 ----

def test_e62_a_child_inherits_the_parents_out_of_tree_reach(tmp_path):
    root = _project(tmp_path)
    outside = _outside(tmp_path)
    client = FakeClient([_round(_call("r0", "read_file", path=str(outside / "lib.py"))), _text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=READER,
                            allow_out_of_tree_file_edits=True)
    _spawn(root, cfg, client, role="reader")
    assert "secret = 42" in _results(client)["r0"]["content"]


def test_e62_without_the_setting_a_child_cannot_read_out_of_tree(tmp_path):
    root = _project(tmp_path)
    outside = _outside(tmp_path)
    client = FakeClient([_round(_call("r0", "read_file", path=str(outside / "lib.py"))), _text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=READER,
                            allow_out_of_tree_file_edits=False)
    _spawn(root, cfg, client, role="reader")
    assert "secret = 42" not in _results(client)["r0"]["content"]


def test_e62_a_writer_still_cannot_write_outside_the_project_even_with_out_of_tree_on(tmp_path):
    root = _project(tmp_path)
    outside = _outside(tmp_path)
    client = FakeClient([_round(_call("w0", "write_file", path=str(outside / "lib.py"),
                                      content="pwned\n")), _text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER,
                            allow_out_of_tree_file_edits=True)
    _spawn(root, cfg, client)
    assert (outside / "lib.py").read_text(encoding="utf-8") == "secret = 42\n"
    assert "outside the project" in _results(client)["w0"]["content"]


# ------------------------------------------------------------------ E59 ----

def test_e59_the_tools_own_refusal_reaches_the_child(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([_round(_call("w0", "write_file", path="../escape.py", content="x\n")),
                         _text("done")])
    roles = {"fixer": {**FIXER["fixer"], "write": ["**"]}}
    cfg = config_mod.Config(platform="mac", subagents=True, roles=roles)
    _spawn(root, cfg, client)
    r = _results(client)["w0"]
    assert "outside the project, so outside this role's write scope" not in r["content"]
    assert r["is_error"] is True
    assert not (tmp_path.parent / "escape.py").exists()


# ------------------------------------------------------------------ E63 ----

def test_e63_a_timed_out_command_is_listed_with_a_marker(tmp_path, monkeypatch):
    import luban.tools as tools_mod
    root = _project(tmp_path)

    class _Proc:
        pid = 999999
        returncode = None

        def __init__(self):
            self.n = 0

        def communicate(self, timeout=None):
            self.n += 1
            if self.n == 1:
                raise tools_mod.subprocess.TimeoutExpired("echo slow", 1)
            return ("partial", "")

    monkeypatch.setattr(tools_mod, "_spawn", lambda *a, **k: _Proc())
    monkeypatch.setattr(tools_mod, "_kill_tree", lambda proc: None)
    client = FakeClient([_round(_call("c0", "run_command", command="echo slow", timeout=1)),
                         _text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client)
    assert "[commands run: echo slow (timed out)]" in out.content


def test_e63_a_refused_command_is_not_listed(tmp_path):
    root = _project(tmp_path)
    client = FakeClient([_round(_call("c0", "run_command", command="rm -rf x")), _text("done")])
    cfg = config_mod.Config(platform="mac", subagents=True, roles=FIXER)
    out, _ = _spawn(root, cfg, client)
    assert "[commands run" not in out.content
