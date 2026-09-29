"""One thread, one entry: /compact keeps the session, and a thread names itself.

With several sessions open in one folder, /compact used to add a "compacted: …" entry per
compact and every title was the first typed line, so /sessions could not say which thread
was which. These drive the real compact and turn-loop paths with a stub client.
"""
import json

from conftest import FakeBlock, FakeClient, FakeMessage

from luban import cli, sessions
from luban import config as config_mod

SID = "2026-07-03-1400-abcd"


def _text(t):
    return FakeMessage([FakeBlock("text", text=t)], "end_turn")


def _session(**over):
    kw = dict(model="m", max_tokens=100, auto=True, stream=False, project="/projA",
              messages=[
                  {"role": "user", "content": "fix the bug"},
                  {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
                  {"role": "user", "content": "and the test"},
                  {"role": "assistant", "content": [{"type": "text", "text": "added"}]},
              ],
              session_id=SID, created="2026-07-03T14:00:00", title="fix the bug")
    kw.update(over)
    return cli.Session(**kw)


# ------------------------------------------------------------------ /compact ----

def test_compact_keeps_the_thread_and_archives_the_full_history():
    s = _session()
    before = [dict(m) for m in s.messages]
    cli.compact_session(s, FakeClient([_text("THE SUMMARY")]))
    assert (s.session_id, s.created, s.title) == (SID, "2026-07-03T14:00:00", "fix the bug")
    [arch] = sessions.archives(SID)
    assert json.loads(arch.read_text(encoding="utf-8"))["messages"] == before
    assert arch.name in s.messages[0]["content"]
    assert sessions.load(SID)["messages"] == s.messages  # the file holds the seed now


def test_compact_leaves_the_session_list_at_one_entry_per_thread():
    s = _session()
    cli.save_session(s)
    other = _session(session_id="2026-07-03-1500-beef", title="other thread")
    cli.save_session(other)
    assert len(sessions.list_sessions("/projA")) == 2
    cli.compact_session(s, FakeClient([_text("THE SUMMARY")]))
    cli.compact_session(s, FakeClient([_text("SECOND SUMMARY")]))
    heads = sessions.list_sessions("/projA")
    assert sorted(h["id"] for h in heads) == sorted([SID, "2026-07-03-1500-beef"])
    assert len(sessions.archives(SID)) == 2


def test_resume_after_compact_returns_the_seeded_thread(monkeypatch):
    s = _session()
    cli.compact_session(s, FakeClient([_text("THE SUMMARY")]))
    assert sessions.latest("/projA")["id"] == SID  # what `luban -c` reopens
    back = cli.Session(model="m", max_tokens=100, auto=True, stream=False, project="/projA")
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    cli.restore_session(back, cli.resolve_or_report(SID, "/projA"))
    assert back.session_id == SID and back.title == "fix the bug"
    assert "THE SUMMARY" in back.messages[0]["content"]


def test_archive_failure_aborts_the_compact_with_the_session_unchanged(monkeypatch, capsys):
    def refuse(data, sessions_dir=None):
        raise OSError("disk full")
    monkeypatch.setattr(cli.sessions_mod, "archive", refuse)
    s = _session()
    before = [dict(m) for m in s.messages]
    fc = FakeClient([_text("THE SUMMARY")])
    cli.compact_session(s, fc)
    assert s.messages == before and s.session_id == SID and s.title == "fix the bug"
    assert not fc.messages.calls, "no summary is bought for a compact that cannot land"
    assert "could not write the transcript archive" in capsys.readouterr().out


def test_sessions_tool_points_at_a_threads_archives(tmp_path):
    s = _session(project=str(tmp_path))
    cli.compact_session(s, FakeClient([_text("THE SUMMARY")]))
    ctx = cli.tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                                render_diff=lambda *a: None, render_command=lambda c: None)
    out = cli.tools.run_tool("sessions", {}, ctx).content
    assert f"~/.luban/sessions/archive/{SID}-*.json" in out and "1 archive" in out


# -------------------------------------------------------------- titles ----------

def _first_turn_session(**over):
    kw = dict(messages=[
        {"role": "user", "content": "[luban-context]\nHOOK NOISE\n[/luban-context]\n\n"
                                    "why does the tag parser drop the last token"},
        {"role": "assistant", "content": [{"type": "text", "text": "Because of an "
                                                                   "off-by-one."}]},
    ], session_id="", created="", title="why does the tag parser drop the last token")
    kw.update(over)
    return _session(**kw)


def test_first_turn_makes_one_title_call_and_takes_the_title():
    s = _first_turn_session()
    fc = FakeClient([_text('"Tag parser off by one."')])
    cli.name_thread(s, fc)
    assert len(fc.messages.calls) == 1
    call = fc.messages.calls[0]
    assert call["max_tokens"] == cli.TITLE_MAX_TOKENS and call["tools"] == []
    sent = call["messages"][0]["content"]
    assert "tag parser" in sent and "off-by-one" in sent and "HOOK NOISE" not in sent
    assert (s.title, s.title_source) == ("Tag parser off by one", "auto")
    assert sessions.load(s.session_id)["title_source"] == "auto"
    assert s.ledger.calls == 1 and s.ledger.context_tokens == 0  # a side call


def test_a_second_turn_makes_no_title_call():
    s = _first_turn_session()
    s.messages += [{"role": "user", "content": "and now?"},
                   {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]
    fc = FakeClient([_text("Never used")])
    cli.name_thread(s, fc)
    assert not fc.messages.calls


def test_a_user_title_is_never_replaced_and_survives_compact(tmp_path):
    s = _first_turn_session()
    cli.handle_command("/title my own name", s)
    fc = FakeClient([_text("Never used")])
    cli.name_thread(s, fc)
    assert not fc.messages.calls
    cli.compact_session(s, FakeClient([_text("Title: Something Else\nTHE SUMMARY")]))
    assert (s.title, s.title_source) == ("my own name", "user")
    assert sessions.load(s.session_id)["title"] == "my own name"


def test_new_with_a_title_is_user_set():
    s = _first_turn_session()
    cli.handle_command("/new named thread", s)
    assert (s.title, s.title_source) == ("named thread", "user")
    cli.handle_command("/clear", s)
    assert s.title_source == "first_line"


def test_a_failed_title_call_keeps_the_first_line_and_does_not_raise():
    s = _first_turn_session()
    cli.name_thread(s, FakeClient([]))  # scripted empty: create() raises
    assert (s.title, s.title_source) == (
        "why does the tag parser drop the last token", "first_line")
    cli.name_thread(s, FakeClient([_text("   ")]))  # empty answer
    assert s.title_source == "first_line"


def test_compact_title_line_refreshes_an_auto_title_and_stays_out_of_the_seed():
    s = _session(title_source="auto")
    cli.compact_session(s, FakeClient([_text("Title: Parser fix and tests\nTHE SUMMARY")]))
    assert (s.title, s.title_source) == ("Parser fix and tests", "auto")
    assert "Title:" not in s.messages[0]["content"]
    assert "THE SUMMARY" in s.messages[0]["content"]
    assert sessions.load(SID)["title"] == "Parser fix and tests"


def test_an_old_session_file_reads_as_not_user_set_and_makes_no_title_call(monkeypatch):
    old = {"id": SID, "project": "/projA", "created": "", "model": "m",
           "title": "old title", "messages": _session().messages}
    sessions.save(old)
    assert sessions.list_sessions("/projA")[0]["title_source"] == "first_line"
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, project="/projA")
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    cli.restore_session(s, sessions.load(SID))
    fc = FakeClient([_text("Never used")])
    cli.name_thread(s, fc)
    assert not fc.messages.calls and s.title == "old title"


def test_turn_loop_titles_after_the_first_turn_only(tmp_path, monkeypatch):
    lines = iter(["why does the tag parser drop the last token", "thanks"])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError
    monkeypatch.setattr(cli, "input", fake_input, raising=False)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli.config_mod, "load_config",
                        lambda: config_mod.Config(platform="mac", memory_enabled=False))
    monkeypatch.setattr(cli, "setup_custom_tools", lambda: [])
    fc = FakeClient([_text("An off-by-one."), _text("Tag parser off by one"),
                     _text("You're welcome.")])
    monkeypatch.setattr(cli.client_mod, "get_client", lambda: fc)
    cli.main(["--dir", str(tmp_path), "--no-stream"])
    assert len(fc.messages.calls) == 3  # turn, title, turn — no second title call
    [head] = sessions.list_sessions(str(tmp_path))
    assert (head["title"], head["title_source"]) == ("Tag parser off by one", "auto")
