from pathlib import Path

import pytest

from luban import memory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    return tmp_path


def test_scaffold_creates_files(mem):
    memory.ensure_scaffold()
    assert (mem / "SOUL.md").exists()
    assert (mem / "memory" / "MEMORY.md").exists()
    assert (mem / "memory" / "journal").is_dir()


def test_scaffold_never_overwrites(mem):
    (mem / "SOUL.md").write_text("mine", encoding="utf-8")
    memory.ensure_scaffold()
    assert (mem / "SOUL.md").read_text(encoding="utf-8") == "mine"


def test_read_soul_missing_is_empty(mem):
    assert memory.read_soul() == ""


def test_read_soul_capped(mem):
    (mem / "SOUL.md").write_text("x" * 5000, encoding="utf-8")
    out = memory.read_soul()
    assert len(out) < 5000 and out.endswith("[SOUL.md truncated]")


def test_read_soul_binary_never_crashes(mem):
    (mem / "SOUL.md").write_bytes(b"\xff\xfe caf\xe9")
    assert isinstance(memory.read_soul(), str)


def test_read_user_binary_never_crashes(mem):
    (mem / "USER.md").write_bytes(b"\xff\xfe user \x00 facts")
    assert isinstance(memory.read_user(), str)


def test_read_index(mem):
    memory.ensure_scaffold()
    (mem / "memory" / "MEMORY.md").write_text("- [a] b", encoding="utf-8")
    assert memory.read_index() == "- [a] b"


def test_recent_journal_today_and_yesterday_only(mem, monkeypatch):
    import datetime as dt
    jdir = mem / "memory" / "journal"
    jdir.mkdir(parents=True)
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    old = today - dt.timedelta(days=5)
    (jdir / f"{today.isoformat()}.md").write_text("[10:00] now", encoding="utf-8")
    (jdir / f"{yesterday.isoformat()}.md").write_text("[09:00] then", encoding="utf-8")
    (jdir / f"{old.isoformat()}.md").write_text("[08:00] ancient", encoding="utf-8")
    out = memory.read_recent_journal()
    assert "now" in out and "then" in out and "ancient" not in out


def test_recent_journal_tail_truncation_keeps_newest(mem):
    import datetime as dt
    jdir = mem / "memory" / "journal"
    jdir.mkdir(parents=True)
    body = "\n".join(f"[10:{i%60:02}] entry {i}" for i in range(400))
    (jdir / f"{dt.date.today().isoformat()}.md").write_text(body, encoding="utf-8")
    out = memory.read_recent_journal()
    assert len(out) <= memory.JOURNAL_MAX + 40
    assert "entry 399" in out and out.startswith("[journal truncated]")


def test_bootstrap_block_composition(mem):
    memory.ensure_scaffold()
    (mem / "SOUL.md").write_text("SOULTEXT", encoding="utf-8")
    (mem / "memory" / "MEMORY.md").write_text("- [f] INDEXLINE", encoding="utf-8")
    out = memory.bootstrap_block()
    assert "remember" in out  # hygiene preamble always present
    assert out.index("SOULTEXT") < out.index("INDEXLINE")


def test_bootstrap_block_skips_empty_sections(mem):
    out = memory.bootstrap_block()  # nothing scaffolded
    assert "SOUL.md" not in out and "Recent journal" not in out
    assert "remember" in out  # hygiene still there


def test_valid_slug():
    assert memory.valid_slug("prefers-plotly")
    assert memory.valid_slug("a1")
    assert not memory.valid_slug("../evil")
    assert not memory.valid_slug("Has Space")
    assert not memory.valid_slug("UPPER")
    assert not memory.valid_slug("")
    assert not memory.valid_slug("x" * 65)
    assert not memory.valid_slug("evil\n")


def test_remember_creates_fact_and_index(mem):
    msg = memory.remember("prefers-plotly", "plotting default", "Use plotly express.")
    assert "prefers-plotly" in msg
    fact = (mem / "memory" / "prefers-plotly.md").read_text(encoding="utf-8")
    assert fact.startswith("description: plotting default")
    assert "Use plotly express." in fact
    index = (mem / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "- [prefers-plotly] plotting default" in index


def test_remember_update_replaces_not_duplicates(mem):
    memory.remember("fact-a", "old desc", "old")
    memory.remember("fact-a", "new desc", "new")
    index = (mem / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert index.count("fact-a") == 1 and "new desc" in index and "old desc" not in index


def test_remember_invalid_slug(mem):
    msg = memory.remember("../evil", "d", "b")
    assert msg.startswith("Invalid") and not (mem / "evil.md").exists()


def test_read_fact(mem):
    memory.remember("f1", "d", "body text")
    assert "body text" in memory.read_fact("f1")
    assert memory.read_fact("nope") is None
    assert memory.read_fact("../evil") is None


def test_forget_removes_fact_and_index_line(mem):
    memory.remember("f1", "d1", "b1")
    memory.remember("f2", "d2", "b2")
    msg = memory.forget("f1")
    assert "f1" in msg
    assert not (mem / "memory" / "f1.md").exists()
    index = (mem / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "f1" not in index and "f2" in index


def test_forget_missing(mem):
    assert memory.forget("ghost").startswith("No memory")


def test_recall_hits_fact_name_body_and_journal(mem):
    memory.remember("plotly-pref", "plotting", "Always use plotly.")
    memory.journal_append("debugged the SQL wrapper")
    out = memory.recall("plotly")
    assert "Always use plotly." in out
    out2 = memory.recall("sql wrapper")
    assert "debugged the SQL wrapper" in out2
    assert memory.recall("zzz-nothing") == "(no matches)"


def test_recall_capped(mem):
    memory.remember("big", "big fact", "y" * 20000)
    out = memory.recall("big")
    assert len(out) <= memory.RECALL_MAX + 40 and "[recall truncated]" in out


def test_journal_append_creates_and_stamps(mem):
    import datetime as dt
    memory.journal_append("did a thing")
    path = mem / "memory" / "journal" / f"{dt.date.today().isoformat()}.md"
    line = path.read_text(encoding="utf-8")
    assert "did a thing" in line and line.startswith("[")


def test_journal_append_never_raises(mem):
    # Make the journal dir path unusable: a FILE where the dir should be.
    (mem / "memory").mkdir(exist_ok=True)
    (mem / "memory" / "journal").write_text("not a dir", encoding="utf-8")
    memory.journal_append("must not raise")  # swallowed


def test_scaffold_creates_enhancements_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / ".luban" / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / ".luban" / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / ".luban" / "memory")
    memory.ensure_scaffold()
    tracker = tmp_path / ".luban" / "memory" / "enhancements.md"
    assert tracker.exists()
    text = tracker.read_text(encoding="utf-8")
    assert text.startswith("description: Self-improvement tracker")
    assert "## Open" in text and "## Resolved" in text
    index = (tmp_path / ".luban" / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "- [enhancements] Self-improvement tracker" in index


def test_scaffold_never_overwrites_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / ".luban" / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / ".luban" / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / ".luban" / "memory")
    memory.ensure_scaffold()
    tracker = tmp_path / ".luban" / "memory" / "enhancements.md"
    tracker.write_text("MY FIELD NOTES", encoding="utf-8")
    memory.ensure_scaffold()
    assert tracker.read_text(encoding="utf-8") == "MY FIELD NOTES"


def test_user_md_scaffolded_and_read(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / ".luban" / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / ".luban" / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / ".luban" / "memory")
    memory.ensure_scaffold()
    user = tmp_path / ".luban" / "USER.md"
    assert user.exists()
    assert memory.read_user() == memory._USER_TEMPLATE.strip()


def test_scaffold_never_overwrites_user_md(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / ".luban" / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / ".luban" / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / ".luban" / "memory")
    memory.ensure_scaffold()
    (tmp_path / ".luban" / "USER.md").write_text("MY FACTS", encoding="utf-8")
    memory.ensure_scaffold()
    assert (tmp_path / ".luban" / "USER.md").read_text(encoding="utf-8") == "MY FACTS"


def test_read_user_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    (tmp_path / "USER.md").write_text("x" * (memory.USER_MAX + 500), encoding="utf-8")
    out = memory.read_user()
    assert out.endswith("[USER.md truncated]")
    assert len(out) <= memory.USER_MAX + len("\n[USER.md truncated]")


def test_read_user_missing_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "absent.md")
    assert memory.read_user() == ""


def test_bootstrap_includes_user_section(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    (tmp_path / "SOUL.md").write_text("I ask before installing.", encoding="utf-8")
    (tmp_path / "USER.md").write_text("Name: Sam. Prefers Plotly.", encoding="utf-8")
    block = memory.bootstrap_block()
    assert "Who you are working with (USER.md):" in block
    assert "Name: Sam. Prefers Plotly." in block
    # USER section comes after the SOUL section
    assert block.index("SOUL.md):") < block.index("USER.md):")


def test_templates_have_no_hash_comment_lines(tmp_path):
    # F3: guidance must be HTML comments, not '#'-prefixed lines that render as H1.
    for tmpl in (memory._SOUL_TEMPLATE, memory._USER_TEMPLATE):
        for line in tmpl.splitlines():
            s = line.strip()
            if s.startswith("#") and not s.startswith("##"):
                raise AssertionError(f"single-# line renders as H1: {line!r}")


def test_soul_template_no_longer_has_user_section(tmp_path):
    # F1: personal facts moved to USER.md; SOUL is character/behavior only.
    assert "Who I'm working with" not in memory._SOUL_TEMPLATE
    assert "## How I should work" in memory._SOUL_TEMPLATE
