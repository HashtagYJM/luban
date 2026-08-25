"""E37: hooks get a payload, a project scope, and a place to keep scripts.

The first cut could only run a fixed command and be told an event happened. So the case
the feature exists for — check the file that was just written — could not be written at
all, a per-project recitation fired in every project, and every hook had to be inlined
into config.toml.
"""
import json
import sys

import pytest

from luban import hooks, paths, tools


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LUBAN_HOME", str(tmp_path / "home"))
    paths.luban_home.cache_clear()
    yield tmp_path / "home"
    paths.luban_home.cache_clear()


def _hook(**kw):
    kw.setdefault("event", "post_tool_use")
    kw.setdefault("run", "")
    return hooks.Hook(**kw)


def _reads_stdin(tmp_path):
    """A hook script that echoes back the payload it was handed."""
    script = tmp_path / "echo_payload.py"
    script.write_text("import sys; sys.stdout.write(sys.stdin.read())", encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


# ------------------------------------------------------------------ (a) payload ----

def test_the_payload_reaches_the_hook_on_stdin(tmp_path, home):
    out = hooks.run_hooks([_hook(run=_reads_stdin(tmp_path))], "post_tool_use", tmp_path,
                          tool_name="write_file",
                          tool_input={"path": "docs/x.md", "content": "hi"})
    body = out.split("\n", 1)[1].rsplit("\n", 1)[0]
    data = json.loads(body)
    assert data["event"] == "post_tool_use"
    assert data["tool_name"] == "write_file"
    assert data["tool_input"]["path"] == "docs/x.md"
    assert data["project"] == tmp_path.name


def test_the_same_values_are_in_the_environment(tmp_path, home):
    script = tmp_path / "echo_env.py"
    script.write_text("import os; print(os.environ['LUBAN_TOOL_PATH'])", encoding="utf-8")
    out = hooks.run_hooks([_hook(run=f'"{sys.executable}" "{script}"')], "post_tool_use",
                          tmp_path, tool_name="write_file",
                          tool_input={"path": "docs/x.md"})
    assert "docs/x.md" in out


def test_a_non_tool_event_carries_no_tool_keys(tmp_path, home):
    data = hooks.payload("session_start", tmp_path)
    assert "tool_name" not in data and "tool_input" not in data
    assert data["project_dir"] == str(tmp_path)


def test_the_tool_result_is_not_in_the_payload(tmp_path, home):
    """It can be a whole file; a hook's own output is capped tighter than a tool
    result for the same reason."""
    data = hooks.payload("post_tool_use", tmp_path, "read_file", {"path": "a"})
    assert "tool_result" not in data and "result" not in data


def test_the_tool_input_reaches_a_real_post_tool_use_fire(tmp_path, home):
    """Through run_tool, which is the only path a tool call takes."""
    script = tmp_path / "echo_name.py"
    script.write_text("import json,sys; print(json.load(sys.stdin)['tool_input']['path'])",
                      encoding="utf-8")
    (tmp_path / "seen.md").write_text("hello", encoding="utf-8")
    ctx = tools.ToolContext(
        project_root=tmp_path, confirm=lambda p: True, render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
        hooks=[_hook(run=f'"{sys.executable}" "{script}"', match="read_file")])
    out = tools.run_tool("read_file", {"path": "seen.md"}, ctx)
    assert "seen.md" in out.content


# ------------------------------------------------------------ (b) project scope ----

def test_a_scoped_hook_fires_only_in_its_project(tmp_path):
    here = tmp_path / "luban"
    here.mkdir()
    assert hooks.in_project(_hook(project="luban"), here)
    assert not hooks.in_project(_hook(project="glio"), here)
    assert hooks.in_project(_hook(project="lub*"), here)
    assert hooks.in_project(_hook(project=""), here)  # unscoped fires everywhere


def test_a_scope_can_name_a_path_not_just_a_name(tmp_path):
    here = tmp_path / "work" / "luban"
    here.mkdir(parents=True)
    assert hooks.in_project(_hook(project="*/work/luban"), here)
    assert hooks.in_project(_hook(project="work/luban"), here)


def test_the_scope_is_read_from_config(tmp_path):
    parsed, warnings = hooks.parse([
        {"event": "stop", "run": "echo hi", "project": "luban"}])
    assert parsed[0].project == "luban"
    assert warnings == []


def test_for_event_applies_the_scope(tmp_path):
    here = tmp_path / "glio"
    here.mkdir()
    declared = [_hook(event="stop", run="a", project="luban"),
                _hook(event="stop", run="b")]
    assert [h.run for h in hooks.for_event(declared, "stop", "", here)] == ["b"]


# --------------------------------------------------------------- (c) script dir ----

def test_a_hooks_path_resolves_against_the_users_hooks_dir(home):
    (home / "hooks").mkdir(parents=True)
    (home / "hooks" / "guard.py").write_text("print('ok')", encoding="utf-8")
    assert str(home / "hooks" / "guard.py") in hooks.resolve_run("python hooks/guard.py")


def test_a_project_with_its_own_hooks_dir_is_left_alone(home):
    """Nothing of that name in the user's hooks dir — the command means what it says."""
    assert hooks.resolve_run("python hooks/theirs.py") == "python hooks/theirs.py"


def test_an_absolute_path_is_never_rewritten(home):
    (home / "hooks").mkdir(parents=True)
    (home / "hooks" / "guard.py").write_text("print('ok')", encoding="utf-8")
    run = "python /opt/project/hooks/guard.py"
    assert hooks.resolve_run(run) == run
