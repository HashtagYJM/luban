"""E39: duplicate detection only ever caught duplicates worded alike.

The pass scored token overlap against one threshold and reported what cleared it. Facts
stating the same rule in different words score below it, so the pass reported a clean
store while several real overlaps sat in it — and the store kept growing.
"""
import pytest

from luban import cli, memory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    return tmp_path / "memory"


def _semantic_pair():
    """Two facts, one rule, no shared vocabulary to speak of."""
    memory.remember("empty-output-retry", "what to do when a tool returns nothing",
                    "If a tool hands back no content, treat it as a failure and say so.")
    memory.remember("blank-result-handling", "reading a silent step",
                    "A step that produced no body has not run; raise it, never proceed.")


def test_the_pair_the_threshold_misses_is_still_put_in_front_of_the_curator(mem):
    _semantic_pair()
    assert memory.duplicate_candidates() == []          # the old pass: clean store
    out = memory.audit()
    assert "empty-output-retry" in out and "blank-result-handling" in out


def test_the_listing_says_it_is_a_floor_not_an_answer(mem):
    _semantic_pair()
    out = memory.audit()
    assert "FLOOR" in out
    assert "WORDED alike" in out


def test_every_fact_is_listed_one_line_each(mem):
    memory.remember("alpha", "the first thing", "body one")
    memory.remember("beta", "the second thing", "body two")
    index = dict(memory.description_index())
    assert index == {"alpha": "the first thing", "beta": "the second thing"}
    assert "EVERY FACT, ONE LINE EACH (2)" in memory.audit()


def test_continuity_pointers_stay_out_of_the_index(mem):
    """One per project, meant to look alike — the curator is told not to merge them."""
    memory.remember("alpha", "the first thing", "body one")
    memory.checkpoint("luban", "still on the tracker", "sessions/x.json")
    assert [slug for slug, _ in memory.description_index()] == ["alpha"]


def test_a_strong_pair_is_still_shown_above_the_line(mem):
    memory.remember("coding-style", "how the user likes code written",
                    "prefers ruff and type hints on every function")
    memory.remember("user-code-preferences", "how the user likes code written",
                    "prefers ruff and type hints on every function")
    out = memory.audit()
    assert "above the threshold" in out
    assert memory.duplicate_candidates()


def test_the_prompt_asks_for_meaning_not_wording():
    assert "MEANING" in cli.REFLECT_PROMPT
    assert "floor" in cli.REFLECT_PROMPT
    assert "no duplicates" in cli.REFLECT_PROMPT
