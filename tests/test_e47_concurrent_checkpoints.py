"""E47 — one continuity pointer per project, last writer wins, silently.

Two sessions working different strands of the same project both checkpoint. The second
write replaced the first's next step and nothing anywhere said so: the session that
checkpointed last defined the project's recorded state, and a `/resume` reader could not
tell a second workstream had ever existed. Both sessions were doing exactly what the
tool documents.

These tests state the invariant: a status belongs to the session that wrote it, and a
status written by a DIFFERENT session is displaced visibly, not silently.
"""
from datetime import date

import pytest

from luban import memory, tools


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(memory, "_project", "")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    return tmp_path / "memory"


def _ctx(root, session_id=""):
    return tools.ToolContext(
        project_root=root,
        confirm=lambda p: True,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
        session_id=session_id,
    )


# ------------------------------------------------------- (a) the status is stamped ----

def test_a_status_names_the_session_that_wrote_it(mem):
    memory.checkpoint("alpha", "next is the ALLOW rule", writer="s-import")
    body = memory.read_fact("active-alpha")
    assert "s-import" in body
    assert "next is the ALLOW rule" in body


def test_the_checkpoint_tool_stamps_the_running_session(mem, tmp_path):
    root = tmp_path / "alpha"
    root.mkdir()
    out = tools.run_tool("checkpoint", {"status": "at step three"},
                         _ctx(root, session_id="s-erp"))
    assert not out.is_error
    assert "s-erp" in memory.read_fact("active-alpha")


# ---------------------------------------------- (b) a different writer is not lost ----

def test_another_sessions_next_step_survives_the_overwrite(mem):
    memory.checkpoint("alpha", "phases 0-2 closed, next the ALLOW rule", writer="s-import")
    memory.checkpoint("alpha", "vendor reconciliation, next the pie chart", writer="s-erp")
    body = memory.read_fact("active-alpha")
    assert "vendor reconciliation" in body           # the newest is the status
    assert "phases 0-2 closed" in body               # the displaced one is still readable
    assert "s-import" in body and "s-erp" in body


def test_the_writer_is_told_it_displaced_another_session(mem, tmp_path):
    root = tmp_path / "alpha"
    root.mkdir()
    memory.checkpoint("alpha", "phases 0-2 closed", writer="s-import")
    out = tools.run_tool("checkpoint", {"status": "vendor reconciliation"},
                         _ctx(root, session_id="s-erp"))
    assert not out.is_error
    assert "s-import" in out.content, "a silent displacement is the whole defect"


def test_the_same_session_checkpointing_again_just_replaces_its_own(mem):
    memory.checkpoint("alpha", "first thought", writer="s-import")
    memory.checkpoint("alpha", "second thought", writer="s-import")
    body = memory.read_fact("active-alpha")
    assert "second thought" in body
    assert "first thought" not in body


def test_the_pointer_holds_at_most_two_workstreams(mem):
    """A pointer is one always-on line per project. It records that another workstream
    exists; it is not a log of every session that ever touched the project."""
    memory.checkpoint("alpha", "one", writer="s1")
    memory.checkpoint("alpha", "two", writer="s2")
    memory.checkpoint("alpha", "three", writer="s3")
    body = memory.read_fact("active-alpha")
    assert "three" in body and "two" in body
    assert "one" not in body


def test_a_writer_reclaiming_the_pointer_drops_its_own_stale_line(mem):
    memory.checkpoint("alpha", "import: step one", writer="s-import")
    memory.checkpoint("alpha", "erp: step one", writer="s-erp")
    memory.checkpoint("alpha", "import: step two", writer="s-import")
    body = memory.read_fact("active-alpha")
    assert "import: step two" in body and "erp: step one" in body
    assert "import: step one" not in body


def test_the_index_line_says_another_session_is_in_the_pointer(mem):
    memory.checkpoint("alpha", "import next step", writer="s-import")
    memory.checkpoint("alpha", "erp next step", writer="s-erp")
    idx = memory.read_index()
    assert "erp next step" in idx
    assert "other session" in idx


# ------------------------------------------------------------ (c) nothing regresses ----

def test_an_unattributed_status_behaves_exactly_as_before(mem):
    """luban's own address refresh and any pre-upgrade pointer carry no writer. Without
    two known and different writers there is no displacement to report, so the pointer
    keeps its old single-status shape rather than growing a line on every upgrade."""
    memory.checkpoint("alpha", "first")
    memory.checkpoint("alpha", "second")
    body = memory.read_fact("active-alpha")
    assert "second" in body and "first" not in body
    assert "also" not in body


def test_the_address_refresh_still_leaves_the_status_and_its_writer_alone(mem):
    memory.checkpoint("alpha", "next the ALLOW rule", writer="s-import")
    memory.checkpoint("alpha", "", session_id="s-import")   # what /compact does
    body = memory.read_fact("active-alpha")
    assert "next the ALLOW rule" in body
    assert "s-import" in body
    assert f"last session: {date.today()}" in body
