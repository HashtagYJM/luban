"""Routing by re-injection cost, and a continuity pointer nobody has to maintain.

The organising rule: a store that is always-on is re-sent on EVERY model call, so it
holds POINTERS; a store read on demand holds the depth. These tests state the four
mechanisms that fall out of it — the pointer, the ring-fence that stops /reflect
deleting it, the project tag that makes the timeline it indexes into usable, and the
dispatch allowlist that makes "this turn may not write facts" a control rather than
a request.
"""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from luban import cli, config as config_mod, memory, tools


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(memory, "_project", "")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    return tmp_path / "memory"


def _ctx(root):
    return tools.ToolContext(
        project_root=Path(root), confirm=lambda p: True,
        render_diff=lambda p, o, n: None, render_command=lambda c: None,
    )


def _day(mem, offset, text):
    path = mem / "journal" / f"{date.today() - timedelta(days=offset)}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ================= the tag is applied at the chokepoint, not by callers =============

def test_every_writer_tags_because_the_chokepoint_does(mem, monkeypatch):
    """Half the timeline was unlabelled: one caller prefixed the text, the other didn't.

    Enforcing at journal_append is the same move as sanitize_history at the send —
    a correct rule reached by an incomplete set of call sites presents as a broken one.
    """
    memory.set_project("alpha")
    memory.journal_append("from the tool default")               # the journal tool's path
    memory.journal_append("from an explicit caller", project="beta")
    cli.exit_journal(
        cli.Session(model="m", max_tokens=1, auto=True, stream=False,
                    messages=[{"role": "user", "content": "x"}], project="p", title="t"),
        config_mod.Config(platform="mac"), Path("/tmp/gamma"),
    )
    text = (mem / "journal" / f"{date.today()}.md").read_text(encoding="utf-8")
    for tag in ("[alpha]", "[beta]", "[gamma]"):
        assert tag in text
    assert "[[" not in text  # never double-prefixed by a caller that also tags


def test_window_is_filtered_to_this_project_and_says_so(mem):
    _day(mem, 0, "[09:00] [alpha] alpha work\n[10:00] [beta] beta work\n")
    memory.set_project("alpha")
    out = memory.read_recent_journal()
    assert "alpha work" in out and "beta work" not in out
    assert "entries for 'alpha' only" in out  # the omission is stated, never silent


def test_a_busy_other_project_no_longer_blanks_this_ones_window(mem):
    """The failure the filter exists for: two days of someone else's work push every
    entry for THIS project out of a window that is sized in whole days."""
    _day(mem, 0, "[09:00] [beta] " + "b" * 400 + "\n")
    _day(mem, 1, "[09:00] [beta] " + "b" * 400 + "\n")
    _day(mem, 2, "[09:00] [alpha] the thing I was actually doing\n")
    memory.set_project("alpha")
    assert "the thing I was actually doing" in memory.read_recent_journal()


def test_continuation_lines_follow_their_entry_not_the_line_before(mem):
    _day(mem, 0, "[09:00] [beta] beta headline\nbeta detail\n"
                 "[10:00] [alpha] alpha headline\nalpha detail\n")
    memory.set_project("alpha")
    out = memory.read_recent_journal()
    assert "alpha detail" in out and "beta detail" not in out


def test_untagged_legacy_entries_survive_the_filter(mem):
    """Every entry written before tagging existed is untagged; dropping them would
    silently delete the older half of the timeline."""
    _day(mem, 0, "[09:00] written before tagging existed\n[10:00] [beta] beta work\n")
    memory.set_project("alpha")
    out = memory.read_recent_journal()
    assert "before tagging existed" in out and "beta work" not in out


# ================= the pointer: code owns the address, the model the status ==========

def test_pointer_is_per_project(mem):
    memory.checkpoint("alpha", "alpha is at step one")
    memory.checkpoint("beta", "beta is at step two")
    assert "alpha is at step one" in memory.read_fact("active-alpha")
    assert "beta is at step two" in memory.read_fact("active-beta")


def test_the_status_reaches_the_always_on_index_as_one_line(mem):
    """The whole point of a pointer: the always-on cost is the index line, and the
    body it names costs nothing until someone recalls it."""
    memory.checkpoint("alpha", "spec drafted; next write the parser in luban/p.py")
    idx = memory.read_index()
    assert "- [active-alpha] spec drafted; next write the parser in luban/p.py" in idx


def test_address_refresh_never_re_dates_a_status_the_model_did_not_write(mem):
    """A pointer that nobody has updated must read as exactly that. Carrying one date
    for both would launder a week-old status as today's."""
    memory.checkpoint("alpha", "half way through the parser")
    stale = "2020-01-01"
    memory._fact_path("active-alpha").write_text(
        memory.read_fact("active-alpha").replace(f"status ({date.today()})",
                                                 f"status ({stale})"),
        encoding="utf-8")
    memory.checkpoint("alpha", "", session_id="sess-9")   # address only
    body = memory.read_fact("active-alpha")
    assert f"status ({stale}): half way through the parser" in body
    assert f"last session: {date.today()}" in body
    assert "sess-9" in body


def test_address_lands_with_no_status_at_all(mem):
    memory.checkpoint("alpha", "", session_id="sess-1")
    body = memory.read_fact("active-alpha")
    assert "status: not recorded" in body
    assert "~/.luban/sessions/sess-1.json" in body


def test_a_later_status_keeps_the_transcript_the_address_already_recorded(mem):
    memory.checkpoint("alpha", "", session_id="sess-1")
    memory.checkpoint("alpha", "now I know where I am")   # the model's tool call
    body = memory.read_fact("active-alpha")
    assert "sess-1" in body and "now I know where I am" in body


def test_checkpoint_tool_writes_the_pointer_for_the_current_project(mem, tmp_path):
    root = tmp_path / "alpha"
    root.mkdir()
    out = tools.run_tool("checkpoint", {"status": "at step three"}, _ctx(root))
    assert not out.is_error
    assert "at step three" in memory.read_fact("active-alpha")


# ================= /reflect stays a curator, and cannot undo the pointer ============

def test_reflect_is_shown_the_pointers_ring_fenced(mem):
    memory.remember("ordinary", "an ordinary fact", "body")
    memory.checkpoint("alpha", "at step one")
    text = memory.audit()
    assert "MAINTAINED BY LUBAN" in text
    fenced = text.split("MAINTAINED BY LUBAN", 1)[1]
    assert "active-alpha" in fenced          # shown — it spends the same budget
    assert "active-alpha" not in text.split("MAINTAINED BY LUBAN", 1)[0]  # not as raw material


def test_pointers_are_never_offered_as_duplicates_of_each_other(mem):
    """One per project, all the same shape by construction — a merge candidate list
    that pairs them up is asking the curator to destroy the mechanism."""
    memory.checkpoint("alpha", "same sentence, different project")
    memory.checkpoint("beta", "same sentence, different project")
    assert memory.duplicate_candidates() == []


def test_reflect_prompt_exempts_them_from_its_delete_rule(mem):
    """DELETE forgets what project files already record and what was true for one task.
    A continuity pointer is both, by design — so the rule has to name the exception."""
    assert "continuity pointer" in cli.REFLECT_PROMPT.lower()


def test_a_deleted_pointer_heals_itself_at_the_next_compact(mem, tmp_path):
    memory.checkpoint("alpha", "at step one")
    memory.forget("active-alpha")
    memory.checkpoint("alpha", "", session_id="s2")   # what /compact does
    assert memory.read_fact("active-alpha") is not None


# ================= withholding a tool is enforced, not declared =====================

def test_dispatch_refuses_a_tool_outside_the_allowlist(mem, tmp_path):
    ctx = replace(_ctx(tmp_path), only=frozenset({"journal"}))
    out = tools.run_tool("remember", {"name": "x", "description": "d", "body": "b"}, ctx)
    assert out.is_error and "not available" in out.content
    assert memory.read_fact("x") is None


def test_the_allowlist_is_a_general_gate_not_a_memory_one(mem, tmp_path):
    """It sits at run_tool, the choke point EVERY call passes through, so it applies to
    the whole surface — the flush turn is its first consumer, not its scope."""
    ctx = replace(_ctx(tmp_path), only=frozenset({"list_dir"}))
    assert tools.run_tool("grep", {"pattern": "x"}, ctx).is_error      # withheld
    assert not tools.run_tool("list_dir", {"path": "."}, ctx).is_error  # allowed


def test_no_allowlist_means_the_whole_dispatch(mem, tmp_path):
    out = tools.run_tool("remember", {"name": "x", "description": "d", "body": "b"},
                         _ctx(tmp_path))
    assert not out.is_error


def test_a_refused_call_is_audited_rather_than_dropped(mem, tmp_path):
    seen = []
    ctx = replace(_ctx(tmp_path), only=frozenset({"journal"}), audit=seen.append)
    tools.run_tool("remember", {"name": "x", "description": "d", "body": "b"}, ctx)
    assert seen and seen[0]["decision"] == "not_offered" and seen[0]["is_error"]


# ================= the journal's content rule ======================================

def test_hygiene_routes_journal_content_by_re_injection_cost(mem):
    h = memory._HYGIENE.lower()
    assert "pointer" in h and "every model call" in h
    assert "reversal" in h and "surprise" in h   # the two with no cheaper home


def test_hygiene_no_longer_sends_continuity_through_the_journal(mem):
    assert "active-<project>" in memory._HYGIENE
    assert "never infer 'where we left off' from it" in memory._HYGIENE
