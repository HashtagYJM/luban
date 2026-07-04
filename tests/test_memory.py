from pathlib import Path

import pytest

from luban import memory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
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
