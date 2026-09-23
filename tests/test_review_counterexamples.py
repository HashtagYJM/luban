"""Counterexamples an independent review found in the R1–U1 work, each pinned."""
import json
import os
import sys

import pytest

from luban import cli, config as config_mod, permissions, sessions as sessions_mod, tools

from tests.conftest import FakeBlock, FakeClient, FakeMessage


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".luban"
    (home / "memory").mkdir(parents=True)
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    return home


def test_ctrl_c_mid_round_keeps_the_finished_write_and_says_so_truthfully(tmp_path, monkeypatch):
    printed = []
    lines = iter(["write f then run"])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError
    monkeypatch.setattr(cli, "input", fake_input, raising=False)
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    monkeypatch.setattr(cli.ui, "render_command", lambda *a: None)
    monkeypatch.setattr(cli.config_mod, "load_config",
                        lambda: config_mod.Config(platform="mac", memory_enabled=False))
    monkeypatch.setattr(cli, "setup_custom_tools", lambda: [])
    fc = FakeClient([FakeMessage([
        FakeBlock("tool_use", id="w", name="write_file", input={"path": "f.txt", "content": "v1"}),
        FakeBlock("tool_use", id="r", name="run_command", input={"command": "sleep 60"})],
        "tool_use")])
    monkeypatch.setattr(cli.client_mod, "get_client", lambda: fc)
    real = tools.run_tool

    def run_tool(name, inp, ctx):
        if name == "run_command":
            raise KeyboardInterrupt
        return real(name, inp, ctx)
    monkeypatch.setattr(tools, "run_tool", run_tool)
    cli.main(["--dir", str(tmp_path), "--auto", "--no-stream"])
    assert (tmp_path / "f.txt").read_text() == "v1"
    out = "".join(printed)
    assert "what ran before the interrupt is saved" in out and "/retry" not in out.split("interrupted")[1]
    saved = json.loads(next((tmp_path / "sessions").glob("*.json")).read_text(encoding="utf-8"))
    uses = [b["name"] for m in saved["messages"] if isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_use"]
    results = {b["tool_use_id"]: b for m in saved["messages"] if isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result"}
    assert uses == ["write_file", "run_command"]
    assert not results["w"]["is_error"] and "Interrupted" in results["r"]["content"]


def test_dot_dot_does_not_widen_an_allow_rule(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    (proj / "src").mkdir()

    def decide(path, allow, deny=()):
        sp = tools.equivalent_targets("write_file", {"path": path}, proj)
        return permissions.evaluate("write_file", {"path": path}, list(allow), list(deny),
                                    read_only=False, targets=sp,
                                    allow_targets=sp[1:] or sp).action
    assert decide("docs/../src/main.py", ["write_file:docs/*"]) == "ask"
    assert decide("docs/readme.md", ["write_file:docs/*"]) == "allow"
    assert decide("~/.luban/memory/../config.toml", ["write_file:~/.luban/memory/*"]) == "ask"
    # and deny still matches every spelling, including the raw one
    assert decide("docs/../src/main.py", [], ["write_file:docs/*"]) == "deny"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows paths already fold case")
def test_a_different_case_spelling_of_the_home_is_still_the_home(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "client_local.py").write_text("SECRET = 'x'\n")
    shouted = tmp_path / "home" / ".LUBAN" / "client_local.py"
    if not shouted.exists():
        pytest.skip("case-sensitive filesystem: the other spelling is another file")
    with pytest.raises(ValueError, match="off-limits"):
        tools.resolve_tool_path(tmp_path / "proj", str(shouted), allow_out_of_tree=True)
    sp = tools.equivalent_targets("write_file", {"path": str(shouted.with_name("x.md"))},
                                  tmp_path / "proj", allow_out_of_tree=True)
    assert "~/.luban/x.md" in sp


def test_a_deny_rule_holds_when_a_parent_folder_is_searched(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "memory" / "facts.md").write_text("password hint: blue\n")
    (tmp_path / "home" / "notes.txt").write_text("password policy doc\n")
    cfg = config_mod.Config(platform="mac", deny=["read_file:~/.luban/*"])
    s = cli.Session(model="m", max_tokens=10, auto=False, stream=False, project=str(tmp_path / "home"))
    ctx = cli.build_tool_context(s, tmp_path / "home", cfg)
    out = tools.run_tool("grep", {"pattern": "password", "path": "."}, ctx).content
    assert "blue" not in out and "password policy" in out
    assert "withheld by a deny rule" in out


def test_polling_a_job_or_asking_a_sub_agent_is_not_a_change():
    def round_of(name, inp=None):
        return [{"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t", "name": name, "input": inp or {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": ""}]}]
    assert not cli.round_mutated(round_of("read_output", {"handle": "bg1"}))
    assert not cli.round_mutated(round_of("spawn_subagent", {"task": "look"}))
    assert cli.round_mutated(round_of("read_output", {"handle": "bg1", "kill": True}))
    assert cli.round_mutated(round_of("journal", {"text": "did x"}))
    assert cli.round_mutated(round_of("edit_file"))


def test_launch_failures_are_named_in_the_trail(tmp_path, monkeypatch):
    ctx = tools.ToolContext(tmp_path, lambda p: True, lambda *a: None, lambda c: None)
    out = tools._run_command({"command": "echo a\0b"}, ctx)
    assert out.is_error and out.outcome == "launch_failed"
    monkeypatch.setattr(tools, "_spawn", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    out = tools._run_command({"command": "echo hi", "background": True}, ctx)
    assert out.is_error and out.outcome == "launch_failed"


def test_a_stub_that_landed_is_projected_even_when_the_fold_cannot_archive(tmp_path, monkeypatch):
    from tests.test_archive_preservation import _trim_and_fold_session
    s, cfg = _trim_and_fold_session(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "standing_tokens", lambda *a: 0)
    real, calls = sessions_mod.archive, []

    def second_fails(data, sessions_dir=None):
        calls.append(1)
        if len(calls) > 1:
            raise OSError("disk full")
        return real(data, sessions_dir)
    monkeypatch.setattr(sessions_mod, "archive", second_fails)
    assert cli.fold_history(s, object(), cfg, tmp_path) is True
    assert s.ledger.projected == cli._history_chars(s.messages)  # ratio is 1.0 here
