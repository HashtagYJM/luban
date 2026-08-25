"""E38: the journal line has a record model, and luban owns its bracket namespace.

`[HH:MM] [project] text` is one unescaped line, so metadata and free text shared the
bracket namespace. The rendered line is the only format example the writer sees, so it
imitates it and opens with a `[topic]` of its own — which the reader then parses AS the
project tag (entry matches no project, dropped from every window) or fails to parse at
all (entry kept for EVERY project, while the window goes on saying "entries for X only").
"""
import pytest

from luban import memory


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    memory.set_project("")
    yield tmp_path / "memory" / "journal"
    memory.set_project("")


def _today(journal_dir):
    return next(journal_dir.glob("*.md")).read_text(encoding="utf-8")


# --------------------------------------------------------------- the write side ----

def test_a_model_authored_topic_bracket_does_not_become_the_project_tag(journal):
    memory.journal_append("[GLIO sankey] rewired the loader", project="glio")
    line = _today(journal).strip()
    assert "] [glio] " in line
    assert line.endswith("GLIO sankey: rewired the loader")


def test_a_bracket_that_only_repeats_the_project_tag_is_dropped(journal):
    memory.journal_append("[luban] shipped the fix", project="luban")
    assert _today(journal).strip().endswith("[luban] shipped the fix")


def test_the_writer_imitating_the_timestamp_is_dropped_too(journal):
    memory.journal_append("[14:02] [session close] wrapped up", project="luban")
    line = _today(journal).strip()
    assert "14:02" not in line.split("] ", 1)[1]
    assert line.endswith("session close: wrapped up")


def test_an_entry_that_is_only_a_bracket_keeps_its_words(journal):
    memory.journal_append("[session close]", project="luban")
    assert _today(journal).strip().endswith("[luban] session close")


def test_an_ordinary_entry_is_written_exactly_as_given(journal):
    memory.journal_append("read the loader, it is fine", project="luban")
    assert _today(journal).strip().endswith("[luban] read the loader, it is fine")


def test_a_bracket_later_in_the_line_is_left_alone(journal):
    """Only a LEADING run is metadata wearing prose's clothes."""
    memory.journal_append("fixed the [tag] parser", project="luban")
    assert _today(journal).strip().endswith("[luban] fixed the [tag] parser")


# --------------------------------------------------------------- the read side ----

def test_a_tag_without_its_trailing_space_still_parses(journal):
    """The reader required `] ` WITH the space, so this parsed as untagged — and
    untagged is kept for every project."""
    memory.set_project("luban")
    assert memory._for_project("[09:00] [other]did a thing") == ""


def test_an_unreadable_tag_fails_closed(journal):
    """A bracket that never closes is unreadable, not absent."""
    memory.set_project("luban")
    assert memory._for_project("[09:00] [unterminated tag with no close") == ""


def test_a_genuinely_untagged_entry_is_still_kept(journal):
    """Written before tagging existed; dropping these deletes the older half of the
    timeline."""
    memory.set_project("luban")
    assert memory._for_project("[09:00] an old entry") == "[09:00] an old entry"


def test_continuation_lines_follow_their_entry(journal):
    memory.set_project("luban")
    text = "\n".join([
        "[09:00] [other]leaked entry",
        "  its second line",
        "[09:05] [luban] mine",
        "  my second line",
    ])
    assert memory._for_project(text) == "[09:05] [luban] mine\n  my second line"


def test_the_window_note_is_true_now(journal):
    """'entries for X only' was silently false while a mis-parsed tag failed open."""
    memory.set_project("luban")
    day = journal / "2026-08-24.md"
    day.write_text("[09:00] [other]not mine\n[09:05] [luban] mine\n", encoding="utf-8")
    window = memory.read_recent_journal()
    assert "not mine" not in window
    assert "mine" in window
    assert "entries for 'luban' only" in window
