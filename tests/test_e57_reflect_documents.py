"""E57: /reflect keeps tidying facts, USER.md and SOUL.md, and cannot touch a maintained
document or a project file. A routine pass under auto mode shortened the tracker into a
project file, rewrote the project's pointers to it, then forgot the tracker itself.

Driven through the real reflect entry point with a scripted model, in a synthetic home.
"""
import pytest

from luban import cli, config as config_mod, memory, tools
from tests.conftest import FakeBlock, FakeClient, FakeMessage

TRACKER = """description: Self-improvement tracker
document: true

# Tracker

## Open

| ID | Sev | Area | Status | Issue -> suggested fix |
|----|-----|------|--------|------------------------|
| E3 | high | tools | OPEN | widget output truncated -> raise the cap |
| E4 | low | ui | SHARED | spinner flickers -> debounce it |

### E3: widget output truncated
Seen twice with the sample widget.

## Resolved

| ID | Issue | Resolution | Verification |
|----|-------|------------|--------------|
| E1 | slow start | 0.1.0 | timed a cold start |
| E2 | stray prompt | wontfix | by design |
"""

RUNBOOK = """description: Release runbook for the sample project
document: true

1. build
2. test
3. publish
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setattr(memory, "MEMORY_DIR", h / "memory")
    monkeypatch.setattr(memory, "USER_PATH", h / "USER.md")
    monkeypatch.setattr(memory, "SOUL_PATH", h / "SOUL.md")
    monkeypatch.setattr(tools, "LUBAN_HOME", h)
    (h / "memory" / "journal").mkdir(parents=True)
    return h


def _call(i, tool, **inp):
    return FakeBlock("tool_use", id=f"t{i}", name=tool, input=inp)


def _results(fc):
    """tool_use_id -> (content, is_error), from what the model was sent back."""
    out = {}
    for call in fc.messages.calls:
        for m in call["messages"]:
            if isinstance(m.get("content"), list):
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        content = b["content"]
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content)
                        out[b["tool_use_id"]] = (content, b.get("is_error", False))
    return out


def test_reflect_curates_facts_and_leaves_documents_and_project_files_alone(
        home, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ui, "print_text", lambda *a, **k: None)
    monkeypatch.setattr(cli.ui, "print_thinking", lambda *a, **k: None, raising=False)
    mem = home / "memory"
    (mem / "enhancements.md").write_text(TRACKER, encoding="utf-8")
    (mem / "release-runbook.md").write_text(RUNBOOK, encoding="utf-8")
    memory.remember("pref-tests-a", "user wants tests first", "Write the test first.")
    memory.remember("pref-tests-b", "user likes tests before code", "Tests before code.")
    (home / "USER.md").write_text("## About me\nA developer. A developer.\n", encoding="utf-8")
    # the tracker's rows also appear in the timeline, which is what invited a DELETE
    (mem / "journal" / "2026-01-01.md").write_text(
        "[10:00] filed E3 widget output truncated in the tracker\n", encoding="utf-8")
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    (proj / "LUBAN.md").write_text("Tracker: ~/.luban/memory/enhancements.md\n",
                                   encoding="utf-8")
    (proj / "docs" / "PROGRESS.md").write_text(
        "Open items live in ~/.luban/memory/enhancements.md\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in (
        mem / "enhancements.md", mem / "release-runbook.md",
        proj / "LUBAN.md", proj / "docs" / "PROGRESS.md")}

    script = [
        _call(1, "remember", name="pref-tests-a", description="user wants tests first",
              body="Write the test before the code."),
        _call(2, "forget", name="pref-tests-b"),
        _call(3, "edit_file", path="~/.luban/USER.md",
              old_string="A developer. A developer.", new_string="A developer."),
        _call(4, "edit_file", path="LUBAN.md", old_string="Tracker: ~/.luban/memory/"
              "enhancements.md", new_string="Tracker: docs/reference/tracker.md"),
        _call(5, "write_file", path="docs/reference/tracker.md", content="| E3 |\n"),
        _call(6, "forget", name="enhancements"),
        _call(7, "edit_file", path="~/.luban/memory/enhancements.md",
              old_string="| E4 | low | ui | SHARED | spinner flickers -> debounce it |\n",
              new_string=""),
        _call(8, "forget", name="release-runbook"),
        _call(9, "remember", name="release-runbook", description="x", body="y"),
        _call(10, "run_command", command="echo hi > LUBAN.md"),
    ]
    fc = FakeClient([
        FakeMessage(script, "tool_use"),
        FakeMessage([FakeBlock("text", text="Merged. Suggested edits: LUBAN.md ...")],
                    "end_turn"),
    ])
    session = cli.Session(model="m", max_tokens=1000, auto=True, stream=False,
                          project=str(proj))
    cfg = config_mod.Config(platform="mac", memory_enabled=True, auto=True)
    ctx = cli.build_tool_context(session, proj, cfg)
    cli.reflect_session(session, fc, ctx, cfg, proj)

    res = _results(fc)
    assert set(res) == {f"t{i}" for i in range(1, 11)}
    for ok in ("t1", "t2", "t3"):
        assert not res[ok][1], res[ok]
    for scoped in ("t4", "t5", "t7", "t10"):
        assert res[scoped][1] and "outside /reflect's scope" in res[scoped][0], res[scoped]
        assert "Suggested edits" in res[scoped][0]
    for doc in ("t6", "t8", "t9"):
        assert res[doc][1] and "maintained document" in res[doc][0], res[doc]

    for path, data in before.items():
        assert path.read_bytes() == data, path
    assert not (proj / "docs" / "reference").exists()
    assert "Write the test before the code." in memory.read_fact("pref-tests-a")
    assert memory.read_fact("pref-tests-b") is None
    kept = list((mem / ".forgotten").glob("*-pref-tests-b.md"))
    assert len(kept) == 1 and "Tests before code." in kept[0].read_text(encoding="utf-8")
    assert (home / "USER.md").read_text(encoding="utf-8") == "## About me\nA developer.\n"

    # The curator saw the documents by name, not their bodies.
    prompt = fc.messages.calls[0]["messages"][0]["content"]
    if isinstance(prompt, list):
        prompt = " ".join(b.get("text", "") for b in prompt if isinstance(b, dict))
    assert "MAINTAINED DOCUMENTS" in prompt and "[release-runbook]" in prompt
    assert "spinner flickers" not in prompt and "3. publish" not in prompt

    # Startup and the upgrade reconcile still reach the populated tracker.
    assert memory.ensure_scaffold() == ""
    assert (mem / "enhancements.md").read_bytes() == before[mem / "enhancements.md"]
    assert "~/.luban/memory/enhancements.md" in cli.reconcile_directive("0.0.1", "notes")
    assert tools.resolve_tool_path(proj, "~/.luban/memory/enhancements.md") == \
        (mem / "enhancements.md").resolve()


# ---------------- the tracker guard, in any turn ----------------

def _ordinary_ctx(proj):
    return tools.ToolContext(project_root=proj, confirm=lambda p: True,
                             render_diff=lambda p, o, n: None,
                             render_command=lambda c: None)


def _seed_tracker(home):
    path = home / "memory" / "enhancements.md"
    path.write_text(TRACKER, encoding="utf-8")
    return path


def test_guard_refuses_a_write_that_drops_an_id(home, tmp_path):
    path = _seed_tracker(home)
    out = tools.run_tool("edit_file", {
        "path": "~/.luban/memory/enhancements.md",
        "old_string": "| E4 | low | ui | SHARED | spinner flickers -> debounce it |\n",
        "new_string": ""}, _ordinary_ctx(tmp_path))
    assert out.is_error and "E4" in out.content
    assert path.read_text(encoding="utf-8") == TRACKER


def test_guard_refuses_a_shortened_copy_and_names_what_is_lost(home, tmp_path):
    _seed_tracker(home)
    out = tools.run_tool("write_file", {
        "path": "~/.luban/memory/enhancements.md",
        "content": "# Tracker\n\n## Open\n\n| ID | Issue |\n|--|--|\n| E3 | x |\n"},
        _ordinary_ctx(tmp_path))
    assert out.is_error
    for lost in ("## Resolved", "| ID | Sev | Area", "| ID | Issue | Resolution",
                 "E1", "E2", "E4"):
        assert lost in out.content, lost


def test_moving_a_row_from_open_to_resolved_is_allowed(home, tmp_path):
    path = _seed_tracker(home)
    row = "| E4 | low | ui | SHARED | spinner flickers -> debounce it |\n"
    moved = TRACKER.replace(row, "").replace(
        "| E2 | stray prompt | wontfix | by design |\n",
        "| E2 | stray prompt | wontfix | by design |\n"
        "| E4 | spinner flickers | 0.2.0 | watched it for a minute |\n")
    out = tools.run_tool("write_file", {"path": "~/.luban/memory/enhancements.md",
                                        "content": moved}, _ordinary_ctx(tmp_path))
    assert not out.is_error, out.content
    assert path.read_text(encoding="utf-8") == moved


def test_ordinary_turn_can_still_edit_the_tracker(home, tmp_path):
    path = _seed_tracker(home)
    out = tools.run_tool("edit_file", {
        "path": "~/.luban/memory/enhancements.md",
        "old_string": "| E4 | low | ui | SHARED |", "new_string": "| E4 | low | ui | OPEN |"},
        _ordinary_ctx(tmp_path))
    assert not out.is_error, out.content
    assert "| E4 | low | ui | OPEN |" in path.read_text(encoding="utf-8")


def test_the_guard_checks_only_what_the_file_already_has(home, tmp_path):
    path = home / "memory" / "enhancements.md"
    path.write_text("description: tracker\n\nfree-form notes, no tables\n", encoding="utf-8")
    out = tools.run_tool("write_file", {"path": "~/.luban/memory/enhancements.md",
                                        "content": "description: tracker\n\nrewritten\n"},
                         _ordinary_ctx(tmp_path))
    assert not out.is_error, out.content


# ---------------- audit, remember/forget, forget's copy, startup ----------------

def test_audit_ring_fences_documents(home):
    mem = home / "memory"
    (mem / "enhancements.md").write_text(TRACKER, encoding="utf-8")
    (mem / "release-runbook.md").write_text(RUNBOOK, encoding="utf-8")
    memory.remember("widget-colour", "widgets are blue", "Widgets are blue.")
    memory.remember("widget-color", "the widget is blue", "The widget is blue.")
    out = memory.audit()
    fact_body = out.split("MAINTAINED DOCUMENTS")[0]
    assert "spinner flickers" not in out and "3. publish" not in out
    assert "[enhancements]" not in fact_body and "[release-runbook]" not in fact_body
    docs = out.split("MAINTAINED DOCUMENTS")[1]
    assert f"[enhancements] {len(TRACKER):,} chars" in docs
    assert "never merge, shorten, move or forget" in out
    assert [s for s, _ in memory.description_index()] == ["widget-color", "widget-colour"]
    assert all("enhancements" not in (a, b) and "release-runbook" not in (a, b)
               for a, b, _ in memory.duplicate_candidates(0.0))


def test_remember_and_forget_refuse_a_document_in_any_turn(home, tmp_path):
    mem = home / "memory"
    (mem / "release-runbook.md").write_text(RUNBOOK, encoding="utf-8")
    ctx = _ordinary_ctx(tmp_path)
    for name, inp in (("forget", {"name": "enhancements"}),
                      ("remember", {"name": "enhancements", "description": "d", "body": "b"}),
                      ("forget", {"name": "release-runbook"})):
        out = tools.run_tool(name, inp, ctx)
        assert out.is_error and "edit_file" in out.content, out.content
    assert (mem / "release-runbook.md").read_text(encoding="utf-8") == RUNBOOK
    assert not (mem / "enhancements.md").exists()


def test_forget_keeps_a_copy_and_never_reads_it_back(home):
    memory.remember("old-note", "an old note", "first")
    memory.forget("old-note")
    memory.remember("old-note", "an old note", "second")
    memory.forget("old-note")
    kept = sorted((home / "memory" / ".forgotten").glob("*-old-note.md"))
    assert len(kept) == 2
    assert {p.read_text(encoding="utf-8").split("\n\n")[1].strip() for p in kept} == {
        "first", "second"}
    assert memory.fact_files() == []
    assert "old-note" not in memory.read_index()
    assert memory.recall("old note") == memory.NO_MATCH


def test_startup_names_the_last_copy_of_a_missing_tracker(home):
    kept = home / "memory" / ".forgotten"
    kept.mkdir(parents=True)
    (kept / "20260101-090000-enhancements.md").write_text("older", encoding="utf-8")
    (kept / "20260102-090000-enhancements.md").write_text("newer", encoding="utf-8")
    (kept / "20260103-090000-my-enhancements.md").write_text("another fact", encoding="utf-8")
    note = memory.ensure_scaffold()
    assert "missing" in note and "20260102-090000-enhancements.md" in note
    assert "\n" not in note
    assert (home / "memory" / "enhancements.md").read_text(encoding="utf-8") == \
        memory._ENHANCEMENTS_TEMPLATE
    assert memory.ensure_scaffold() == ""  # present now: nothing to say


def test_new_template_passes_its_own_guard_and_has_the_format():
    t = memory._ENHANCEMENTS_TEMPLATE
    assert t.startswith("description: ") and "\ndocument: true\n" in t
    assert "OPEN -> SHARED -> CLOSED" in t
    assert "| ID | Sev | Area | Status | Issue -> suggested fix |" in t
    assert "| ID | Issue | Resolution | Verification |" in t
    assert "### E<n>: title" in t
    assert memory.tracker_losses(t, t) == []
