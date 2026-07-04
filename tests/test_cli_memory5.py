from pathlib import Path

import pytest

from luban import cli, config as config_mod, memory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    return tmp_path


def make_session(**kw):
    defaults = dict(model="m", max_tokens=64, auto=True, stream=False,
                    messages=[{"role": "user", "content": "hi"},
                              {"role": "assistant", "content": [{"type": "text", "text": "yo"}]}],
                    project="/tmp/proj")
    defaults.update(kw)
    return cli.Session(**defaults)


def make_cfg(enabled=True):
    return config_mod.Config(platform="mac", memory_enabled=enabled)


def test_flush_runs_before_compact_summary(monkeypatch, mem, tmp_path):
    order = []
    monkeypatch.setattr(cli, "flush_memory", lambda *a: order.append("flush"))
    monkeypatch.setattr(cli.sessions_mod, "save", lambda d: None)

    class Block:
        type = "text"
        text = "the summary"

    class Msg:
        content = [Block()]

    class Client:
        pass

    def fake_create_turn(client, **kw):
        order.append("summary")
        return Msg()

    monkeypatch.setattr(cli.client_mod, "create_turn", fake_create_turn)
    s = make_session()
    cli.compact_session(s, Client(), ctx=object(), cfg=make_cfg())
    assert order == ["flush", "summary"]


def test_compact_two_arg_call_still_works(monkeypatch, mem):
    monkeypatch.setattr(cli.sessions_mod, "save", lambda d: None)
    called = []
    monkeypatch.setattr(cli, "flush_memory", lambda *a: called.append(1))

    class Block:
        type = "text"
        text = "sum"

    class Msg:
        content = [Block()]

    monkeypatch.setattr(cli.client_mod, "create_turn", lambda client, **kw: Msg())
    s = make_session()
    cli.compact_session(s, object())  # legacy 2-arg: no flush, no crash
    assert not called


def test_flush_failure_is_swallowed(monkeypatch, mem, tmp_path):
    def boom(*a, **kw):
        raise RuntimeError("api down")
    monkeypatch.setattr(cli.agent, "run_turn", boom)
    s = make_session()
    cli.flush_memory(s, object(), object(), make_cfg())  # must not raise


def test_flush_skips_when_disabled_or_empty(monkeypatch, mem):
    def boom(*a, **kw):
        raise AssertionError("must not call the model")
    monkeypatch.setattr(cli.agent, "run_turn", boom)
    cli.flush_memory(make_session(), object(), object(), make_cfg(enabled=False))
    cli.flush_memory(make_session(messages=[]), object(), object(), make_cfg())


def test_reflect_leaves_session_untouched(monkeypatch, mem):
    seen = {}

    def fake_run_turn(client, config, messages, ctx, on_text, on_thinking=None):
        seen["prompt"] = messages[0]["content"]
        seen["tools"] = config.tools
        return messages
    monkeypatch.setattr(cli.agent, "run_turn", fake_run_turn)
    s = make_session()
    before = list(s.messages)
    cli.reflect_session(s, object(), object(), make_cfg())
    assert s.messages == before
    assert "journal" in seen["prompt"].lower()
    assert any(t["name"] == "remember" for t in seen["tools"])


def test_reflect_disabled_prints_note(monkeypatch, mem, capsys):
    def boom(*a, **kw):
        raise AssertionError("must not call the model")
    monkeypatch.setattr(cli.agent, "run_turn", boom)
    cli.reflect_session(make_session(), object(), object(), make_cfg(enabled=False))


def test_handle_command_reflect(monkeypatch, mem):
    called = []
    monkeypatch.setattr(cli, "reflect_session", lambda *a: called.append(1))
    s = make_session()
    assert cli.handle_command("/reflect", s, object(), object(), make_cfg()) == "handled"
    assert called


def test_handle_command_three_arg_compat():
    s = make_session()
    assert cli.handle_command("/auto", s, None) == "handled"


def test_exit_journal_written(mem, tmp_path):
    s = make_session(title="fix the bug", model="m1")
    cli.exit_journal(s, make_cfg(), Path("/tmp/proj"))
    import datetime as dt
    path = mem / "memory" / "journal" / f"{dt.date.today().isoformat()}.md"
    text = path.read_text(encoding="utf-8")
    assert "fix the bug" in text and "proj" in text


def test_exit_journal_skips_disabled_and_empty(mem):
    cli.exit_journal(make_session(), make_cfg(enabled=False), Path("/tmp/p"))
    cli.exit_journal(make_session(messages=[]), make_cfg(), Path("/tmp/p"))
    assert not (mem / "memory" / "journal").exists()


def test_build_agent_config_disabled_hides_memory(mem, tmp_path):
    cfg_on = cli.build_agent_config(make_session(), make_cfg(True), tmp_path)
    cfg_off = cli.build_agent_config(make_session(), make_cfg(False), tmp_path)
    assert cfg_on.global_memory and any(t["name"] == "remember" for t in cfg_on.tools)
    assert cfg_off.global_memory == ""
    assert not any(t["name"] == "remember" for t in cfg_off.tools)
