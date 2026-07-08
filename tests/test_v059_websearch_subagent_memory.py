"""v0.5.9 — E11 web search wiring, E15 subagent tool, E9 recall wikilinks."""
import types

import pytest

from luban import agent, client as client_mod, memory, tools


# ================= E11: web search wiring =================

class _Rec:
    """Records the tools passed to create(); returns a plain end_turn message."""
    def __init__(self): self.calls = []; self.messages = self
    def create(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")


def _ctx(tmp_path):
    return tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                             render_diff=lambda *a: None, render_command=lambda c: None)


def test_web_search_tool_added_only_when_enabled(tmp_path):
    rec = _Rec()
    cfg = agent.AgentConfig("m", 100, stream=False, platform="mac", tools=[],
                            web_search=True, web_search_tool_type="web_search_20260209")
    agent.run_turn(rec, cfg, [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    sent = rec.calls[0]["tools"]
    assert {"type": "web_search_20260209", "name": "web_search"} in sent


def test_web_search_absent_by_default(tmp_path):
    rec = _Rec()
    cfg = agent.AgentConfig("m", 100, stream=False, platform="mac", tools=[])
    agent.run_turn(rec, cfg, [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    assert all("web_search" not in t.get("name", "") for t in rec.calls[0]["tools"])


def test_message_to_blocks_preserves_server_tool_blocks():
    class B:
        def __init__(s, t, d): s.type = t; s._d = d
        def model_dump(s, exclude_none=False): return s._d
    msg = types.SimpleNamespace(content=[
        B("text", None) if False else types.SimpleNamespace(type="text", text="found:"),
        B("server_tool_use", {"type": "server_tool_use", "id": "s1", "name": "web_search"}),
        B("web_search_tool_result", {"type": "web_search_tool_result", "tool_use_id": "s1",
                                      "content": [{"type": "web_search_result", "url": "x"}]}),
    ])
    blocks = client_mod.message_to_blocks(msg)
    types_ = [b["type"] for b in blocks]
    assert "server_tool_use" in types_ and "web_search_tool_result" in types_


# ================= E15: subagent tool =================

def test_spawn_subagent_disabled_returns_error(tmp_path):
    out = tools.run_tool("spawn_subagent", {"task": "do it"}, _ctx(tmp_path))
    assert out.is_error and "not enabled" in out.content


def test_spawn_subagent_runs_and_returns_result(tmp_path):
    calls = {"n": 0}
    def fake_sub(task): calls["n"] += 1; return f"researched: {task}"
    ctx = tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                            render_diff=lambda *a: None, render_command=lambda c: None,
                            subagent=fake_sub)
    out = tools.run_tool("spawn_subagent", {"task": "find X"}, ctx)
    assert not out.is_error and "researched: find X" in out.content and calls["n"] == 1


def test_spawn_subagent_bad_task(tmp_path):
    ctx = tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                            render_diff=lambda *a: None, render_command=lambda c: None,
                            subagent=lambda t: "x")
    assert tools.run_tool("spawn_subagent", {"task": ""}, ctx).is_error


def test_subagent_failure_is_reported_not_raised(tmp_path):
    def boom(task): raise RuntimeError("model down")
    ctx = tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                            render_diff=lambda *a: None, render_command=lambda c: None,
                            subagent=boom)
    out = tools.run_tool("spawn_subagent", {"task": "x"}, ctx)
    assert out.is_error and "Subagent failed" in out.content


def test_subagent_tool_offered_only_when_config_on(tmp_path, monkeypatch):
    from luban import cli, config as config_mod
    sess = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[],
                       project="p", title="t")
    monkeypatch.setattr(cli.skills_mod, "list_skills", lambda p: [])
    off = cli.build_agent_config(sess, config_mod.Config(platform="mac", memory_enabled=False,
                                                         subagents=False), tmp_path)
    on = cli.build_agent_config(sess, config_mod.Config(platform="mac", memory_enabled=False,
                                                        subagents=True), tmp_path)
    assert not any(t["name"] == "spawn_subagent" for t in off.tools)
    assert any(t["name"] == "spawn_subagent" for t in on.tools)


# ================= E9: recall follows wikilinks =================

@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "m")
    (tmp_path / "m").mkdir()
    return tmp_path / "m"


def test_recall_follows_wikilink(mem):
    memory.remember("active-work", "current tasks", "Building [[project-x]] now.")
    memory.remember("project-x", "the widget project", "Lives at ~/dev/x; status green.")
    out = memory.recall("active work")  # matches active-work; should pull in project-x
    assert "active-work" in out and "project-x" in out
    assert "widget project" in out and "linked from" in out


def test_recall_wikilink_to_missing_fact_is_safe(mem):
    memory.remember("a", "d", "see [[does-not-exist]] for more")
    out = memory.recall("a")  # must not crash on a dangling link
    assert "[a]" in out


def test_recall_no_infinite_loop_on_mutual_links(mem):
    memory.remember("x", "d", "paired with [[y]]")
    memory.remember("y", "d", "paired with [[x]]")
    out = memory.recall("paired")  # both match directly; nothing to pull via links
    # each appears once as a direct hit; neither is re-added as a "linked from" entry
    assert out.count("[x]\ndescription") == 1 and out.count("[y]\ndescription") == 1
    assert "linked from" not in out
