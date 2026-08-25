"""E42: a hook's output became the name of every session.

A session's title was the first line of the first user message AS STORED — and a user
message stores whatever luban prepends to it. Once a session_start hook existed, every
session in a project was titled with the same opening characters of that hook's output:
indistinguishable in the listing, and `/resume <fragment>` could no longer tell two
threads apart either, since resolve() matches a fragment against the title.
"""
from luban import cli, hooks


HOOK = hooks.wrap("session_start", "# PROGRESS: luban home\nThe working folder is ...")


def _session(**kw):
    s = cli.Session(model="m", max_tokens=10, auto=False, stream=True,
                     project="luban")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# --------------------------------------------------------- the name is the user's ----

def test_the_title_is_what_the_user_typed_not_what_the_hook_said():
    s = _session(pending_context=[HOOK])
    composed = cli.compose_user_message(s, "fix the journal tag parser")
    assert s.title == "fix the journal tag parser"
    assert HOOK in composed  # the hook still reaches the model, untouched


def test_two_sessions_behind_the_same_hook_get_different_names():
    a, b = _session(pending_context=[HOOK]), _session(pending_context=[HOOK])
    cli.compose_user_message(a, "look at the openai adapter")
    cli.compose_user_message(b, "why is /reflect missing duplicates")
    assert a.title != b.title


def test_a_title_the_user_chose_is_never_overwritten():
    s = _session(title="the good name", pending_context=[HOOK])
    cli.compose_user_message(s, "some later prompt")
    assert s.title == "the good name"


def test_a_blank_line_names_nothing():
    s = _session()
    cli.compose_user_message(s, "   ")
    assert s.title == ""


def test_the_title_is_still_capped_and_collapsed():
    s = _session()
    cli.compose_user_message(s, "  spaced   out  " + "x" * 100)
    assert len(s.title) == 60
    assert "  " not in s.title


# ------------------------------------------------------------- the save fallback ----

def test_a_message_that_arrived_composed_still_titles_from_the_user_part():
    """The path with no typed line in hand: a session file, or a restored thread."""
    assert cli.title_from(f"{HOOK}\n\nfix the journal tag parser") \
        == "fix the journal tag parser"


def test_a_skill_body_is_not_a_title_either():
    assert cli.title_from("[skill: quant_research]\nbody\n\nrun the backtest") \
        == "run the backtest"


def test_the_fallback_takes_the_first_non_empty_line():
    """A pasted brief or a stack trace must not fill the sixty characters with noise —
    the reason titling was cut down in the first place."""
    assert cli.title_from("just a normal prompt\nsecond line") == "just a normal prompt"
    assert cli.title_from("\n\n   \nfirst real line\nmore") == "first real line"
    assert cli.title_from("x" * 100) == "x" * 60


def test_the_close_journal_entry_carries_the_users_words(tmp_path, monkeypatch):
    """The harm the row is actually about: exit_journal writes the title as the
    session's close record, so the journal — the continuity artifact — held several
    sessions under one name and looked healthy doing it."""
    from luban import config as config_mod, memory

    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    s = _session(pending_context=[HOOK], model="claude-opus-5")
    s.messages.append({"role": "user", "content": cli.compose_user_message(
        s, "why is /reflect missing duplicates")})
    cli.exit_journal(s, config_mod.Config(platform="linux"), tmp_path / "luban")
    written = next((tmp_path / "memory" / "journal").glob("*.md")).read_text()
    assert "why is /reflect missing duplicates" in written
    assert "[hook:" not in written


def test_save_session_uses_the_users_words(tmp_path, monkeypatch):
    from luban import sessions as sessions_mod
    monkeypatch.setattr(sessions_mod, "SESSIONS_DIR", tmp_path)
    s = _session(messages=[{"role": "user", "content": f"{HOOK}\n\nwhat broke in E38?"}])
    cli.save_session(s)
    assert s.title == "what broke in E38?"


# ------------------------------------------------------- repairing what is saved ----

def test_a_session_already_named_after_a_hook_is_re_derived_on_load():
    data = {"id": "x", "title": "[hook: session_start] # PROGRESS: luban home The ",
            "messages": [{"role": "user", "content": f"{HOOK}\n\nfix the tag parser"}]}
    assert cli._repaired_title(data) == "fix the tag parser"


def test_a_compacted_session_keeps_its_prefix_when_repaired():
    data = {"id": "x",
            "title": "compacted: [hook: session_start] # PROGRESS: luban home",
            "messages": [{"role": "user", "content": f"{HOOK}\n\nfix the tag parser"}]}
    assert cli._repaired_title(data) == "compacted: fix the tag parser"


def test_a_title_the_user_gave_is_left_alone_on_load():
    data = {"id": "x", "title": "E41 provider switch",
            "messages": [{"role": "user", "content": f"{HOOK}\n\nsomething else"}]}
    assert cli._repaired_title(data) == "E41 provider switch"


def test_a_hook_titled_session_with_nothing_else_in_it_keeps_its_title():
    """Repair only when there is something better to say."""
    data = {"id": "x", "title": "[hook: session_start] # PROGRESS",
            "messages": [{"role": "user", "content": HOOK}]}
    assert cli._repaired_title(data) == "[hook: session_start] # PROGRESS"


def test_restore_applies_the_repair(capsys):
    s = _session()
    cli.restore_session(s, {
        "id": "2026-08-25-1130-43b7", "model": "m", "created": "", "project": "luban",
        "title": "[hook: session_start] # PROGRESS: luban home The ",
        "messages": [{"role": "user", "content": f"{HOOK}\n\nfix the tag parser"}]})
    assert s.title == "fix the tag parser"
