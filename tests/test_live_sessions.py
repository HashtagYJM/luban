"""Live markers, the tab title, double-open refusal, the /sessions window, and --tidy."""
import json
import os
from datetime import datetime, timedelta

import pytest

from luban import cli, config as config_mod, live, sessions as sessions_mod, ui
from tests.conftest import FakeBlock, FakeClient, FakeMessage


@pytest.fixture(autouse=True)
def _live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    live._state.clear()
    yield
    live._state.clear()


@pytest.fixture()
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def _save(proj, sid, title, days_old=0):
    path = sessions_mod.save({"id": sid, "project": str(proj), "created": "2026-10-01T09:00:00",
                              "model": "m", "title": title,
                              "messages": [{"role": "user", "content": "hi"},
                                           {"role": "assistant",
                                            "content": [{"type": "text", "text": "hey"}]}]})
    if days_old:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["updated"] = (datetime.now() - timedelta(days=days_old)).isoformat(timespec="seconds")
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _other_process_marker(sid, proj, state="working", pid=424242):
    live.LIVE_DIR.mkdir(parents=True, exist_ok=True)
    (live.LIVE_DIR / f"{pid}.json").write_text(json.dumps(
        {"pid": pid, "session": sid, "project": str(proj), "state": state,
         "since": "2026-10-09T10:00:00", "title": "t"}), encoding="utf-8")


def _reply(text):
    return FakeMessage([FakeBlock("text", text=text)], "end_turn")


def _run_main(monkeypatch, argv, cfg, client, lines, printed=None):
    printed = [] if printed is None else printed
    feed = iter(lines)

    def read(*a, **k):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(cli.ui, "print_text", lambda t: printed.append(t))
    monkeypatch.setattr(cli.ui, "read_prompt", read)
    monkeypatch.setattr(cli.client_mod, "get_client", lambda: client)
    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "setup_custom_tools", lambda: [])
    monkeypatch.setattr(cli.memory_mod, "ensure_scaffold", lambda: None)
    cli.main(argv)
    return "".join(printed)


# ------------------------------------------------------------------ markers ----

def test_publish_live_and_clear_and_stale_markers_are_dropped(proj, monkeypatch):
    live.publish("aaa", str(proj), "working", "t")
    _other_process_marker("bbb", proj, pid=424242)
    _other_process_marker("ccc", proj, pid=424243)
    monkeypatch.setattr(live, "_pid_alive", lambda pid: pid in (os.getpid(), 424242))
    running = live.live()
    assert set(running) == {"aaa", "bbb"}
    assert not (live.LIVE_DIR / "424243.json").exists()  # stale: removed on read
    assert live.holder("aaa") is None  # own process is not "another terminal"
    assert live.holder("bbb")["pid"] == 424242
    live.clear()
    assert "aaa" not in live.live()


def test_since_survives_a_same_state_rewrite_and_resets_on_change(proj):
    live.publish("aaa", str(proj), "working")
    first = live._state["since"]
    live.publish("aaa", str(proj), "working")
    assert live._state["since"] == first
    live.publish("aaa", str(proj), "input")
    assert live._state["state"] == "input"


# ------------------------------------------------------------ title and states ----

def test_tab_title_shows_state_project_and_title_and_bell_on_waiting(proj, monkeypatch):
    emitted = []
    monkeypatch.setattr(ui, "_COLOR", True)
    monkeypatch.setattr(ui, "_emit", emitted.append)
    s = cli.Session(model="m", max_tokens=10, auto=False, stream=False, project=str(proj))
    s.session_id, s.title = "aaa", "parser fix"
    cli.set_state(s, proj, "working")
    assert "\033]2;● proj · parser fix\007" in emitted and "\a" not in emitted
    cli.set_state(s, proj, "input")
    assert "\033]2;✋ proj · parser fix\007" in emitted and "\a" in emitted
    assert live._state["state"] == "input"


def test_no_title_sequence_when_not_a_tty(monkeypatch):
    emitted = []
    monkeypatch.setattr(ui, "_COLOR", False)
    monkeypatch.setattr(ui, "_emit", emitted.append)
    ui.set_title("x")
    ui.bell()
    assert emitted == []


def test_a_confirm_publishes_approval_then_working(proj, monkeypatch):
    seen = []
    monkeypatch.setattr(cli.ui, "ask_confirm", lambda p: seen.append(live._state["state"]) or "yes")
    s = cli.Session(model="m", max_tokens=10, auto=False, stream=False, project=str(proj))
    ctx = cli.build_tool_context(s, proj)
    assert ctx.confirm("ok?") is True
    assert seen == ["approval"] and live._state["state"] == "working"


def test_a_turn_leaves_the_marker_waiting_and_exit_clears_it(proj, monkeypatch):
    states = []
    real = live.publish
    monkeypatch.setattr(live, "publish", lambda *a, **k: states.append(a[2]) or real(*a, **k))
    cfg = config_mod.Config(platform="mac", memory_enabled=False)
    _run_main(monkeypatch, ["--dir", str(proj)], cfg, FakeClient([_reply("ok")]), ["hello"])
    assert states[:3] == ["input", "working", "input"]
    assert not list(live.LIVE_DIR.glob("*.json"))  # cleared at exit


# ---------------------------------------------------------- double-open guard ----

def test_resuming_a_session_open_elsewhere_is_refused(proj, monkeypatch):
    _save(proj, "aaa", "held")
    _other_process_marker("aaa", proj)
    monkeypatch.setattr(live, "_pid_alive", lambda pid: True)
    cfg = config_mod.Config(platform="mac", memory_enabled=False)
    out = _run_main(monkeypatch, ["--dir", str(proj), "-r", "aaa"], cfg, FakeClient([]), [])
    assert "already open in another terminal" in out and "pid 424242" in out
    assert "resumed" not in out


def test_continue_skips_held_sessions_and_takes_the_newest_free_one(proj, monkeypatch):
    _save(proj, "old1", "free one", days_old=1)
    _save(proj, "new1", "held one")
    _other_process_marker("new1", proj)
    monkeypatch.setattr(live, "_pid_alive", lambda pid: True)
    cfg = config_mod.Config(platform="mac", memory_enabled=False)
    out = _run_main(monkeypatch, ["--dir", str(proj), "-c"], cfg, FakeClient([]), [])
    assert "1 session(s) here are open in other terminals" in out
    assert 'resumed [proj] "free one"' in out


def test_in_session_resume_of_a_held_thread_is_refused(proj, monkeypatch):
    _save(proj, "aaa", "held")
    _other_process_marker("aaa", proj, state="input")
    monkeypatch.setattr(live, "_pid_alive", lambda pid: True)
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    s = cli.Session(model="m", max_tokens=10, auto=False, stream=False, project=str(proj))
    cli.handle_command("/resume aaa", s, None, cli.build_tool_context(s, proj), None)
    assert "already open in another terminal" in "".join(printed)
    assert s.session_id == "" and s.messages == []


# ------------------------------------------------------------ /sessions window ----

def test_sessions_hides_old_threads_by_default_and_numbers_the_shown_list(proj, monkeypatch):
    _save(proj, "old1", "ancient", days_old=40)
    _save(proj, "mid1", "last month", days_old=10)
    _save(proj, "new1", "this week")
    _save(proj, "held", "held but old", days_old=20)
    _other_process_marker("held", proj)
    monkeypatch.setattr(live, "_pid_alive", lambda pid: True)
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    s = cli.Session(model="m", max_tokens=10, auto=False, stream=False, project=str(proj))
    ctx = cli.build_tool_context(s, proj)
    cli.handle_command("/sessions", s, None, ctx, None)
    out = "".join(printed)
    assert "this week" in out and "held but old" in out  # recent, and live despite age
    assert "ancient" not in out and "last month" not in out
    assert "2 session(s) older than 7 days hidden" in out and "● working" in out
    # A number picks from the list that was shown, not from the full list.
    assert cli.resolve_or_report("2", str(proj))["id"] == "held"
    printed.clear()
    cli.handle_command("/sessions old", s, None, ctx, None)
    out = "".join(printed)
    assert "ancient" in out and "hidden" not in out


# -------------------------------------------------------------------- --tidy ----

def test_tidy_moves_old_sessions_with_archives_and_notes_into_the_attic(proj, monkeypatch):
    _save(proj, "keep1", "recent")
    _save(proj, "old1", "stale", days_old=45)
    _save(proj, "live1", "live but stale", days_old=45)
    sessions_mod.archive(sessions_mod.load("old1"))
    sessions_mod.send_note("old1", "late note", "x", "proj")
    _other_process_marker("live1", proj)
    monkeypatch.setattr(live, "_pid_alive", lambda pid: True)
    cfg = config_mod.Config(platform="mac", memory_enabled=False)
    out = _run_main(monkeypatch, ["--tidy"], cfg, FakeClient([]), [])
    assert "moved 1 session(s)" in out and "1 fold archive(s)" in out
    ids = {h["id"] for h in sessions_mod.list_sessions(None)}
    assert ids == {"keep1", "live1"}
    month = (datetime.now() - timedelta(days=45)).strftime("%Y-%m")
    attic = sessions_mod.SESSIONS_DIR / "attic" / month
    assert (attic / "old1.json").exists() and (attic / "old1.notes.jsonl").exists()
    assert len(list(attic.glob("old1-*.json"))) == 1
    assert not list((sessions_mod.SESSIONS_DIR / "archive").glob("old1-*"))


def test_tidy_with_a_custom_age(proj, monkeypatch):
    _save(proj, "d10", "ten days", days_old=10)
    cfg = config_mod.Config(platform="mac", memory_enabled=False)
    out = _run_main(monkeypatch, ["--tidy", "7"], cfg, FakeClient([]), [])
    assert "moved 1 session(s) untouched for 7+ days" in out
    assert sessions_mod.list_sessions(None) == []
