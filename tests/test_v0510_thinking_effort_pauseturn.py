"""v0.5.10 — adaptive thinking + effort request params, /thinking + /effort,
pause_turn resume."""
import types

import pytest

from luban import agent, client as client_mod, cli, config as config_mod, tools


class _Rec:
    def __init__(self, stop="end_turn"):
        self.calls = []
        self.messages = self
        self._stop = stop
    def create(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")


def _ctx(tmp_path):
    return tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                             render_diff=lambda *a: None, render_command=lambda c: None)


@pytest.fixture(autouse=True)
def _reset_extras():
    client_mod._EXTRAS_SUPPORTED = None
    yield
    client_mod._EXTRAS_SUPPORTED = None


# ---- thinking/effort request shaping ----

def test_thinking_and_effort_sent_when_on(tmp_path):
    rec = _Rec()
    cfg = agent.AgentConfig("m", 100, stream=False, platform="mac", tools=[],
                            thinking=True, effort="xhigh")
    agent.run_turn(rec, cfg, [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    sent = rec.calls[0]
    assert sent["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["output_config"] == {"effort": "xhigh"}


def test_no_thinking_params_when_off(tmp_path):
    rec = _Rec()
    cfg = agent.AgentConfig("m", 100, stream=False, platform="mac", tools=[], thinking=False)
    agent.run_turn(rec, cfg, [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    assert "thinking" not in rec.calls[0] and "output_config" not in rec.calls[0]


def test_backend_rejecting_extras_degrades_to_plain(tmp_path):
    class Picky:
        def __init__(s): s.calls = []; s.messages = s
        def create(s, **kw):
            s.calls.append(kw)
            if "thinking" in kw:
                raise ValueError("400: unsupported parameter 'thinking'")
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")
    picky = Picky()
    cfg = agent.AgentConfig("m", 100, stream=False, platform="mac", tools=[], thinking=True)
    out = agent.run_turn(picky, cfg, [{"role": "user", "content": "hi"}], _ctx(tmp_path), lambda t: None)
    assert out  # did not raise; degraded
    assert client_mod._EXTRAS_SUPPORTED is False
    # a second turn skips extras entirely (no wasted probe)
    n = len(picky.calls)
    agent.run_turn(picky, cfg, [{"role": "user", "content": "again"}], _ctx(tmp_path), lambda t: None)
    assert all("thinking" not in c for c in picky.calls[n:])


# ---- pause_turn resume ----

def test_pause_turn_resumes_then_completes(tmp_path):
    class Pauser:
        def __init__(s): s.n = 0; s.messages = s
        def create(s, **kw):
            s.n += 1
            stop = "pause_turn" if s.n == 1 else "end_turn"
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="…")], stop_reason=stop)
    p = Pauser()
    cfg = agent.AgentConfig("m", 100, stream=False, platform="mac", tools=[], thinking=False)
    agent.run_turn(p, cfg, [{"role": "user", "content": "search"}], _ctx(tmp_path), lambda t: None)
    assert p.n == 2  # re-sent once to resume, then finished


def test_pause_turn_is_bounded(tmp_path):
    class Stuck:
        def __init__(s): s.n = 0; s.messages = s
        def create(s, **kw):
            s.n += 1
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="…")], stop_reason="pause_turn")
    stuck = Stuck()
    cfg = agent.AgentConfig("m", 100, stream=False, platform="mac", tools=[], thinking=False)
    agent.run_turn(stuck, cfg, [{"role": "user", "content": "x"}], _ctx(tmp_path), lambda t: None)
    assert stuck.n <= agent.MAX_PAUSE_RESUMES + 1  # doesn't loop forever


# ---- /thinking and /effort commands ----

def _session():
    return cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       messages=[], project="p", title="t", thinking=True, effort="high")


def test_slash_thinking_toggles(monkeypatch):
    monkeypatch.setattr(cli.ui, "print_text", lambda *a, **k: None)
    s = _session()
    cli.handle_command("/thinking off", s)
    assert s.thinking is False
    cli.handle_command("/thinking on", s)
    assert s.thinking is True


def test_slash_effort_sets_valid_only(monkeypatch):
    monkeypatch.setattr(cli.ui, "print_text", lambda *a, **k: None)
    s = _session()
    cli.handle_command("/effort xhigh", s)
    assert s.effort == "xhigh"
    cli.handle_command("/effort bogus", s)  # rejected
    assert s.effort == "xhigh"


def test_config_defaults_thinking_on_effort_high():
    cfg = config_mod.Config(platform="mac")
    assert cfg.thinking is True and cfg.effort == "high"
