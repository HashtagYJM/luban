"""Session notes: one luban session leaves an addressed note; the target reads it once at
its next turn. Plus the startup wiring of role model patterns, which runs in main()."""
import pytest

from luban import cli, config as config_mod, memory as memory_mod, sessions as sessions_mod, tools
from tests.conftest import FakeBlock, FakeClient, FakeMessage


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_mod, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(memory_mod, "_project", "")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _save(proj, sid, title):
    sessions_mod.save({"id": sid, "project": str(proj), "created": "2026-10-08T09:00:00",
                       "model": "m", "title": title,
                       "messages": [{"role": "user", "content": "hi"},
                                    {"role": "assistant", "content": [{"type": "text", "text": "hey"}]}]})


def _ctx(proj, sid, confirm=True):
    return tools.ToolContext(project_root=proj, confirm=lambda p: confirm,
                             render_diff=lambda *a: None, render_command=lambda c: None,
                             session_id=sid)


def _reply(text):
    return FakeMessage([FakeBlock("text", text=text)], "end_turn")


def _user_texts(call):
    out = []
    for m in call["messages"]:
        if m["role"] != "user":
            continue
        c = m["content"]
        out.append(c if isinstance(c, str) else " ".join(b.get("text", "") for b in c
                                                         if isinstance(b, dict)))
    return out


def _run_main(monkeypatch, argv, cfg, client, lines):
    printed = []
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


# ------------------------------------------------------------------ sending ----

def test_a_note_to_a_saved_session_is_queued_and_counted(home):
    _save(home, "bbb", "parser work")
    out = tools.run_tool("note_to_session", {"session": "bbb", "text": "tests are green"},
                         _ctx(home, "aaa"))
    assert not out.is_error and "next turn" in out.content
    assert sessions_mod.pending_notes("bbb") == 1
    listing = tools.run_tool("sessions", {}, _ctx(home, "aaa")).content
    assert "1 note(s)" in listing
    assert len(sessions_mod.list_sessions(str(home))) == 1  # the notes file is not a session


def test_unknown_self_and_declined_notes_write_nothing(home):
    _save(home, "aaa", "me")
    assert tools.run_tool("note_to_session", {"session": "nope", "text": "x"},
                          _ctx(home, "aaa")).is_error
    assert tools.run_tool("note_to_session", {"session": "aaa", "text": "x"},
                          _ctx(home, "aaa")).is_error
    _save(home, "bbb", "other")
    out = tools.run_tool("note_to_session", {"session": "bbb", "text": "x"},
                         _ctx(home, "aaa", confirm=False))
    assert "declined" in out.content
    assert sessions_mod.pending_notes("bbb") == 0 and sessions_mod.pending_notes("aaa") == 0


def test_sub_agents_and_reflect_cannot_send():
    assert "note_to_session" not in tools.READ_ONLY_TOOLS
    scope = cli.reflect_scope(_ctx(".", "aaa"))
    assert scope("note_to_session", {"session": "b", "text": "x"})


# ------------------------------------------------------------------ delivery ----

def test_the_target_gets_the_note_once_at_its_next_turn_as_machine_context(home, monkeypatch):
    _save(home, "bbb", "parser work")
    tools.run_tool("note_to_session", {"session": "bbb", "text": "do not edit config.py"},
                   _ctx(home, "aaa"))
    client = FakeClient([_reply("noted"), _reply("ok")])
    cfg = config_mod.Config(platform="mac", memory_enabled=False)
    out = _run_main(monkeypatch, ["--dir", str(home), "-c"], cfg, client, ["hello", "again"])

    first, second = client.messages.calls[0], client.messages.calls[1]
    last_user = _user_texts(first)[-1]
    assert "do not edit config.py" in last_user and "hello" in last_user
    assert "the user did not type it" in last_user
    # Paid once: the second turn's NEW user message carries nothing, and the file is gone.
    assert "do not edit config.py" not in _user_texts(second)[-1]
    assert sessions_mod.pending_notes("bbb") == 0
    assert "✉ note from session aaa" in out
    saved = sessions_mod.load("bbb")
    assert saved["title"] == "parser work"  # a note never becomes the thread's name


def test_a_note_appended_while_taking_waits_for_the_next_turn(home, monkeypatch):
    _save(home, "bbb", "t")
    sessions_mod.send_note("bbb", "first", "aaa", "proj")
    real_read = sessions_mod.Path.read_text

    def read_then_race(self, *a, **k):
        text = real_read(self, *a, **k)
        if self.suffix == ".taking":
            sessions_mod.send_note("bbb", "second", "aaa", "proj")
        return text

    monkeypatch.setattr(sessions_mod.Path, "read_text", read_then_race)
    assert [n["text"] for n in sessions_mod.take_notes("bbb")] == ["first"]
    monkeypatch.setattr(sessions_mod.Path, "read_text", real_read)
    assert [n["text"] for n in sessions_mod.take_notes("bbb")] == ["second"]


# ------------------------------------------------------------------ /sessions ----

def test_session_list_shows_each_threads_next_step_and_waiting_notes(home, monkeypatch):
    _save(home, "aaa", "import")
    _save(home, "bbb", "analysis")
    memory_mod.checkpoint(home.name, "run the import on the March file", writer="aaa")
    memory_mod.checkpoint(home.name, "compare the two factor models", writer="bbb")
    sessions_mod.send_note("aaa", "March file is fixed", "bbb", home.name)
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: printed.append(t))
    cli._print_session_list(sessions_mod.list_sessions(str(home)))
    out = "".join(printed)
    assert "run the import on the March file" in out
    assert "compare the two factor models" in out
    assert out.count("note(s) waiting") == 1


# ----------------------------------------------------- role patterns at startup ----

def test_startup_resolves_a_role_pattern_and_prints_it(home, monkeypatch):
    roles = {"reviewer": {"model": "claude-sonnet-*", "prompt": "", "description": "",
                          "tools": None}}
    cfg = config_mod.Config(platform="mac", memory_enabled=False, subagents=True, roles=roles)
    client = FakeClient([], model_ids=["claude-sonnet-4-6", "claude-sonnet-5", "m"])
    out = _run_main(monkeypatch, ["--dir", str(home)], cfg, client, [])
    assert "claude-sonnet-* → claude-sonnet-5" in out
    assert cfg.roles["reviewer"]["model"] == "claude-sonnet-5"
