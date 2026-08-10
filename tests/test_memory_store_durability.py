"""The memory store must survive a write that fails partway.

Path.write_text opens with mode "w", which truncates the target to zero bytes BEFORE the
first byte of new content is written — so any failure in between destroys content that was
never at risk of being wrong. sessions.py and tools.py already write via a temp file and
os.replace; memory.py was the one persistence surface that did not.

Failure is injected by making the temp write raise, which reproduces a partial write
without needing a real disk to fill up.
"""
from pathlib import Path

import pytest

from luban import memory as memory_mod, paths, sessions, tools


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A memory store rooted in tmp_path, scaffolded, with two facts and a correct index."""
    monkeypatch.setattr(memory_mod, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(memory_mod, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory_mod, "USER_PATH", tmp_path / "USER.md")
    memory_mod.ensure_scaffold()  # steady state first: the scaffold itself writes an index
    memory_mod.remember("alpha", "the first fact", "body one")
    memory_mod.remember("beta", "the second fact", "body two")
    return tmp_path


def _break_writes(monkeypatch):
    """Reproduce mode-"w" semantics: the open TRUNCATES, and only then does the write fail.

    Patching write_text to raise immediately would test nothing — it fails BEFORE
    truncating, which is the very behaviour the fix is supposed to produce. The bug is
    that the real write_text destroys the file first and fails second.
    """
    def truncate_then_fail(self, *a, **kw):
        self.open("w").close()  # what mode "w" does before writing a single byte
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Path, "write_text", truncate_then_fail)


# ---------------- F1: a failed write destroys nothing ----------------

def test_a_failed_fact_write_leaves_the_previous_fact_intact(store, monkeypatch):
    """The unrecoverable surface: a fact file is the source of truth, derived from nothing."""
    before = (store / "alpha.md").read_text(encoding="utf-8")
    _break_writes(monkeypatch)
    memory_mod.remember("alpha", "replacement", "replacement body")
    assert (store / "alpha.md").read_text(encoding="utf-8") == before


def test_a_failed_fact_write_reports_failure(store, monkeypatch):
    """'Could not save memory' must mean nothing changed — which is only true atomically."""
    _break_writes(monkeypatch)
    assert "Could not save" in memory_mod.remember("alpha", "replacement", "body")


def test_a_failed_index_rebuild_leaves_the_previous_index_intact(store, monkeypatch):
    """_rebuild_index swallows OSError, claiming facts on disk stay authoritative. That
    claim is only true if the failed rebuild did not already truncate the index."""
    before = (store / "MEMORY.md").read_text(encoding="utf-8")
    assert "alpha" in before
    _break_writes(monkeypatch)
    memory_mod._rebuild_index()
    assert (store / "MEMORY.md").read_text(encoding="utf-8") == before


def test_no_tmp_file_is_ever_read_as_a_fact(store):
    """The atomic write leaves <name>.md.tmp on failure; the store globs *.md."""
    (store / "gamma.md.tmp").write_text("description: half-written\n\nbody", encoding="utf-8")
    memory_mod._rebuild_index()
    assert "gamma" not in (store / "MEMORY.md").read_text(encoding="utf-8")


def test_journal_append_still_appends(store):
    """Mode 'a' never truncates, so it must NOT be converted — an atomic rewrite here
    would replace the day's file with a single line."""
    memory_mod.journal_append("first")
    memory_mod.journal_append("second")
    day = next((store / "journal").glob("*.md"))
    text = day.read_text(encoding="utf-8")
    assert "first" in text and "second" in text


# ---------------- F2: a damaged index is repaired at startup ----------------

def test_a_truncated_index_is_rebuilt_at_startup(store):
    """Cause 2: ensure_scaffold wrote the index only `if not exists()`, and a truncated
    file DOES exist — so the damage outlived every restart."""
    (store / "MEMORY.md").write_text("", encoding="utf-8")
    memory_mod.ensure_scaffold()
    index = (store / "MEMORY.md").read_text(encoding="utf-8")
    assert "alpha" in index and "beta" in index


def test_the_startup_rebuild_is_idempotent(store):
    """Over-reach guard: a correct index must survive startup unchanged."""
    before = (store / "MEMORY.md").read_text(encoding="utf-8")
    memory_mod.ensure_scaffold()
    assert (store / "MEMORY.md").read_text(encoding="utf-8") == before


# ---------------- one helper, three callers ----------------

def test_there_is_one_atomic_write_implementation():
    """The extraction must not leave a caller behind: moving the helper and updating only
    the new caller passes every test above."""
    assert tools._atomic_write_text is paths.atomic_write_text
    assert sessions._atomic_write_text is paths.atomic_write_text


def test_the_helper_replaces_rather_than_truncates(tmp_path, monkeypatch):
    target = tmp_path / "f.md"
    target.write_text("original", encoding="utf-8")
    _break_writes(monkeypatch)
    with pytest.raises(OSError):
        paths.atomic_write_text(target, "replacement")
    assert target.read_text(encoding="utf-8") == "original"
