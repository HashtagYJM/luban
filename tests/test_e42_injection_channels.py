"""E42, the rest of the class: THREE things ride the user's message, not one.

The reported symptom was a hook block becoming the session title. The channel it rides —
`session.pending_context`, merged into the user's message with no boundary — carries two
others, and both predate hooks: a skill body since v0.5.1, and the post-upgrade reconcile
directive since E17. So the same fault has been mis-titling sessions, and mis-writing the
journal entry derived from that title, long before hooks existed.
"""
import io
from contextlib import redirect_stdout

from luban import cli, hooks


def _session():
    return cli.Session(model="m", max_tokens=1, auto=True, stream=False, messages=[])


HOOK = hooks.wrap("session_start", "# PROGRESS: luban home\nThe working folder is...")
SKILL = "[skill: quant_research]\nLoad this before any equity-data work."
UPGRADE = cli.reconcile_directive("0.7.1", "### something shipped")

ALL_THREE = [("hook", HOOK), ("skill", SKILL), ("upgrade", UPGRADE)]


def test_every_injection_channel_is_marked():
    """The repair path can only recognise an injection it can SEE. An unmarked one is
    indistinguishable from something the user typed, which is how the reconcile
    directive kept titling sessions after hook blocks stopped."""
    for label, text in ALL_THREE:
        assert text.lstrip().startswith(cli._INJECTED_PREFIX), label


def test_the_title_is_the_typed_line_whatever_is_injected():
    for label, text in ALL_THREE:
        session = _session()
        session.pending_context.append(text)
        composed = cli.compose_user_message(session, "refactor the attribution loop")
        assert session.title == "refactor the attribution loop", label
        assert text.splitlines()[0] in composed, f"{label}: injection must still be sent"


def _stored(text: str) -> str:
    """A user message in the shape luban actually writes one."""
    session = _session()
    session.pending_context.append(text)
    return cli.compose_user_message(session, "refactor the attribution loop")


def test_a_stored_message_yields_the_typed_line_for_every_channel():
    """The fallback path — a restored session, or one saved with no typed line in hand."""
    for label, text in ALL_THREE:
        assert cli.title_from(_stored(text)) == "refactor the attribution loop", label


def test_an_already_saved_session_is_repaired_for_every_channel():
    for label, text in ALL_THREE:
        stored = _stored(text)
        data = {"title": " ".join(stored.split())[:60],
                "messages": [{"role": "user", "content": stored}]}
        assert cli._repaired_title(data) == "refactor the attribution loop", label


def test_a_multi_paragraph_injection_does_not_leak_its_tail():
    """Why the envelope replaced a list of prefixes. A prefix match drops the FIRST
    paragraph of an injection; every paragraph after it survives and reads as though the
    user typed it. The reconcile directive is many paragraphs long."""
    assert cli.title_from(_stored(UPGRADE)) == "refactor the attribution loop"
    assert "Reconcile" not in cli.title_from_body(_stored(UPGRADE))


def test_a_session_saved_before_the_envelope_still_repairs():
    """Legacy shape: a bare injection with no envelope around it. Recognised by the
    marker each kind already carried. An older upgrade directive had no marker at all
    and cannot be recovered — those sessions predate the fix and stay as they are."""
    for label, text in (("hook", HOOK), ("skill", SKILL)):
        stored = f"{text}\n\nrefactor the attribution loop"
        assert cli.title_from(stored) == "refactor the attribution loop", label


def test_the_journal_entry_is_the_title_so_it_is_fixed_too():
    """The harm that made this more than cosmetic: exit_journal records the session as
    its title, and the journal is the always-on continuity artifact. Identical
    boilerplate entries do not look broken — they look like a timeline."""
    session = _session()
    session.pending_context.append(HOOK)
    cli.compose_user_message(session, "measure S2 against the drift rows")
    assert session.title == "measure S2 against the drift rows"
    assert not session.title.startswith(cli._INJECTED_PREFIX)


def test_resume_does_not_echo_an_injection_as_the_users_words():
    """`luban -c` prints the last exchange. It read the message as stored, so it showed
    a hook's output back under "(you)" — text the user never typed."""
    messages = [
        {"role": "user", "content": f"{HOOK}\n\nrefactor the attribution loop"},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_last_exchange(messages)
    shown = buf.getvalue()
    assert "refactor the attribution loop" in shown
    assert "PROGRESS" not in shown and "[hook:" not in shown


def test_a_message_that_is_only_an_injection_echoes_nothing_of_the_user():
    messages = [{"role": "user", "content": SKILL},
                {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_last_exchange(messages)
    assert "quant_research" not in buf.getvalue()
