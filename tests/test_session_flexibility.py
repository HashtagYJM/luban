"""Sessions as first-class named threads: resolve by number/id/name, /new, /title,
/sessions all, -r <ref>. The workflow this serves: two threads in one project folder."""
import pytest

from luban import cli, sessions as sessions_mod


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_mod, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(cli.ui, "print_text", lambda t: OUT.append(t))
    OUT.clear()
    return tmp_path


OUT: list[str] = []


def _save(project, title, sid, model="m"):
    sessions_mod.save({"id": sid, "project": str(project), "created": "2026-07-14T09:00:00",
                       "model": model, "title": title,
                       "messages": [{"role": "user", "content": title}]})


def _session(project, **kw):
    return cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       messages=[], project=str(project), title="", **kw)


# ---------------- resolve: number, id, name fragment ----------------

def test_resolve_by_name_fragment(store):
    proj = store / "p"
    _save(proj, "market update pipeline", "2026-07-14-0900-aaaa")
    _save(proj, "luban refactor", "2026-07-14-1000-bbbb")
    assert sessions_mod.resolve("market", str(proj))["id"] == "2026-07-14-0900-aaaa"


def test_resolve_by_id_fragment(store):
    proj = store / "p"
    _save(proj, "a", "2026-07-14-0900-aaaa")
    _save(proj, "b", "2026-07-14-1000-bbbb")
    assert sessions_mod.resolve("bbbb", str(proj))["id"] == "2026-07-14-1000-bbbb"


def test_resolve_prefers_this_project(store):
    _save(store / "p", "notes", "here-1")
    _save(store / "other", "notes", "there-1")
    assert sessions_mod.resolve("notes", str(store / "p"))["id"] == "here-1"


def test_ambiguous_fragment_raises_with_the_candidates(store):
    proj = store / "p"
    _save(proj, "notes one", "id-1")
    _save(proj, "notes two", "id-2")
    with pytest.raises(sessions_mod.AmbiguousSession) as exc:
        sessions_mod.resolve("notes", str(proj))
    assert len(exc.value.matches) == 2


def test_resolve_unknown_raises(store):
    _save(store / "p", "notes", "id-1")
    with pytest.raises(sessions_mod.SessionNotFound):
        sessions_mod.resolve("nothing-like-this", str(store / "p"))


def test_resolve_number_is_the_listing_order(store):
    proj = store / "p"
    _save(proj, "older", "id-old")
    _save(proj, "newer", "id-new")  # saved later ⇒ sorts first (by updated, desc)
    want = sessions_mod.list_sessions(str(proj))[1]["id"]
    assert sessions_mod.resolve("2", str(proj))["id"] == want


# ---------------- /resume, /sessions ----------------

def test_resume_by_name_switches_thread(store):
    proj = store / "p"
    _save(proj, "market update pipeline", "sid-market")
    _save(proj, "luban refactor", "sid-luban")  # the latest
    s = _session(proj)
    cli.handle_command("/resume market", s)
    assert s.session_id == "sid-market"


def test_resume_ambiguous_lists_instead_of_guessing(store):
    proj = store / "p"
    _save(proj, "notes one", "id-1")
    _save(proj, "notes two", "id-2")
    s = _session(proj)
    cli.handle_command("/resume notes", s)
    out = "".join(OUT)
    assert "matches 2 sessions" in out and "id-1" in out and "id-2" in out
    assert s.session_id == ""  # nothing was guessed


def test_resume_resets_journal_segment(store):
    """The thread you left had already journaled; the one you switch to hasn't."""
    proj = store / "p"
    _save(proj, "other", "sid-other")
    s = _session(proj, journaled=True)
    cli.handle_command("/resume sid-other", s)
    assert s.journaled is False


def test_sessions_all_spans_projects(store):
    _save(store / "p", "here", "id-here")
    _save(store / "other", "elsewhere", "id-there")
    s = _session(store / "p")
    cli.handle_command("/sessions", s)
    assert "elsewhere" not in "".join(OUT)
    OUT.clear()
    cli.handle_command("/sessions all", s)
    assert "elsewhere" in "".join(OUT) and "here" in "".join(OUT)


# ---------------- /new and /title ----------------

def test_new_saves_the_current_thread_and_starts_a_named_one(store):
    s = _session(store / "p")
    s.messages.append({"role": "user", "content": "first thread"})
    cli.handle_command("/new market update", s)
    assert s.messages == [] and s.title == "market update" and s.session_id == ""
    kept = sessions_mod.list_sessions(str(store / "p"))
    assert len(kept) == 1 and kept[0]["title"] == "first thread"  # not lost


def test_new_titles_survive_the_save(store):
    s = _session(store / "p")
    cli.handle_command("/new market update", s)
    s.messages.append({"role": "user", "content": "what's the S&P doing"})
    cli.save_session(s)
    assert sessions_mod.list_sessions(str(store / "p"))[0]["title"] == "market update"


def test_title_renames_and_persists_immediately(store):
    s = _session(store / "p")
    s.messages.append({"role": "user", "content": "hello"})
    cli.save_session(s)
    cli.handle_command("/title portfolio commentary", s)
    assert sessions_mod.load(s.session_id)["title"] == "portfolio commentary"


def test_title_with_no_arg_reports(store):
    s = _session(store / "p")
    s.title = "current name"
    cli.handle_command("/title", s)
    assert "current name" in "".join(OUT)


def test_clear_still_does_not_rename(store):
    s = _session(store / "p")
    s.messages.append({"role": "user", "content": "x"})
    s.title = "old"
    cli.handle_command("/clear", s)
    assert s.title == "" and s.messages == []


def test_autotitle_is_the_first_line_whitespace_collapsed(store):
    s = _session(store / "p")
    s.messages.append({"role": "user", "content": "fix   the parser\n\nTraceback...\n  File x"})
    cli.save_session(s)
    assert s.title == "fix the parser Traceback... File x"[:60]
    assert "\n" not in s.title


# ---------------- CLI flags ----------------

def test_bare_r_still_prompts(store):
    ns = cli.parse_args(["-r"])
    assert ns.resume == "" and ns.resume is not None  # falsy but present


def test_r_takes_a_reference(store):
    assert cli.parse_args(["-r", "market"]).resume == "market"
    assert cli.parse_args(["--resume", "2"]).resume == "2"


def test_no_r_is_none(store):
    assert cli.parse_args([]).resume is None


def test_pick_session_with_ref_does_not_prompt(store):
    proj = store / "p"
    _save(proj, "market update", "sid-market")

    def boom(_prompt):
        raise AssertionError("should not have prompted")

    data = cli.pick_session(str(proj), all_projects=False, ref="market", input_fn=boom)
    assert data["id"] == "sid-market"


def test_pick_session_bare_prompts_and_accepts_a_name(store):
    proj = store / "p"
    _save(proj, "market update", "sid-market")
    _save(proj, "luban refactor", "sid-luban")
    data = cli.pick_session(str(proj), all_projects=False, ref="",
                            input_fn=lambda _p: "market")
    assert data["id"] == "sid-market"
