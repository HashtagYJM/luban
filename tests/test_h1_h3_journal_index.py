"""Post-v0.5.13 hotfix: H1 journal warning is noise + inverted, H2 index must never
drop a slug, H3 journal window blanks after a gap. Plus /resume <n|id>."""
from datetime import date, timedelta

import pytest

from luban import cli, config as config_mod, memory, sessions as sessions_mod


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    return tmp_path / "memory"


# ================= H1: the journal must never trigger a cap warning =================

def test_over_cap_journal_emits_no_warning(mem):
    """It's tail-biased and lossless — and the head-biased wording ('the LAST n chars
    are dropped') is the exact opposite of what happens to a journal."""
    (mem / "journal" / f"{date.today()}.md").write_text("j" * 5000, encoding="utf-8")
    assert memory.cap_warnings(memory.always_on_usage()) == []


def test_over_cap_user_md_still_warns(mem):
    memory.USER_PATH.write_text("## About me\n" + "u" * 5000, encoding="utf-8")
    warns = memory.cap_warnings(memory.always_on_usage())
    assert len(warns) == 1 and "USER.md" in warns[0]


def test_journal_still_shown_in_usage_for_config(mem):
    (mem / "journal" / f"{date.today()}.md").write_text("j" * 5000, encoding="utf-8")
    entry = next(u for u in memory.always_on_usage() if u[0] == "journal")
    label, size, cap, warnable = entry
    assert size == 5000 + len(f"## {date.today()}\n")  # displayed…
    assert warnable is False  # …but never warned about


def test_journal_truncation_keeps_the_NEWEST(mem):
    """Proves the warning text would have been inverted: the newest survives."""
    (mem / "journal" / f"{date.today()}.md").write_text(
        "OLDEST" + "x" * 4000 + "NEWEST", encoding="utf-8")
    out = memory.read_recent_journal()
    assert "NEWEST" in out and "OLDEST" not in out


# ================= H2: the index sheds descriptions, never slugs =================

def _many_facts(n):
    for i in range(n):
        memory.remember(f"fact-{i:03d}-{'z' * 20}", "a fairly long description " * 3, "body")


def test_index_over_budget_keeps_every_slug(mem):
    _many_facts(80)  # blows past INDEX_MAX with descriptions
    raw = (mem / "MEMORY.md").read_text(encoding="utf-8")
    assert len(raw) > memory.INDEX_MAX
    idx = memory.read_index()
    assert len(idx) <= memory.INDEX_MAX
    for i in range(80):
        assert f"fact-{i:03d}" in idx  # not one slug lost
    assert "descriptions trimmed" in idx
    assert memory.index_slugs_dropped() == 0


def test_late_alphabet_fact_survives_and_is_recallable(mem):
    """The old head-truncation cut late-alphabet slugs first — 'zzz-*' vanished."""
    _many_facts(80)
    memory.remember("zzz-last-in-alphabet", "the canary", "it survived")
    idx = memory.read_index()
    assert "zzz-last-in-alphabet" in idx           # still in the catalog
    assert "zzz-last-in-alphabet" in memory.recall("canary")  # and still recallable


def test_index_within_budget_keeps_descriptions(mem):
    memory.remember("small", "a short description", "body")
    idx = memory.read_index()
    assert "a short description" in idx and "descriptions trimmed" not in idx


# ================= H3: journal window survives a gap (weekend) =================

def test_journal_survives_a_weekend_gap(mem):
    """Work Friday, come back Monday: today+yesterday are both empty, but the
    journal must still show Friday's entries."""
    friday = date.today() - timedelta(days=4)
    (mem / "journal" / f"{friday}.md").write_text("[09:00] shipped the thing", encoding="utf-8")
    out = memory.read_recent_journal()
    assert "shipped the thing" in out and str(friday) in out


def test_journal_picks_the_two_most_recent_nonempty_days(mem):
    for i, day in enumerate([date.today() - timedelta(days=d) for d in (10, 5, 2)]):
        (mem / "journal" / f"{day}.md").write_text(f"entry {i}", encoding="utf-8")
    out = memory.read_recent_journal()
    assert "entry 2" in out and "entry 1" in out  # two newest non-empty
    assert "entry 0" not in out                   # the oldest drops out
    assert out.index("entry 1") < out.index("entry 2")  # chronological order


def test_empty_journal_days_are_skipped(mem):
    (mem / "journal" / f"{date.today()}.md").write_text("", encoding="utf-8")  # blank
    old = date.today() - timedelta(days=3)
    (mem / "journal" / f"{old}.md").write_text("real content", encoding="utf-8")
    assert "real content" in memory.read_recent_journal()


# ================= /resume <n|id> =================

@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_mod, "SESSIONS_DIR", tmp_path / "sessions")
    return tmp_path


def _save(project, title, sid):
    sessions_mod.save({"id": sid, "project": project, "created": "2026-07-14T09:00:00",
                       "model": "m", "title": title,
                       "messages": [{"role": "user", "content": title}]})


def test_resume_by_session_id(store, monkeypatch):
    proj = str(store / "p")
    _save(proj, "thread A", "sid-a")
    _save(proj, "thread B", "sid-b")
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[],
                    project=proj, title="")
    cli.handle_command("/resume sid-a", s)  # NOT the latest — pick it explicitly
    assert s.session_id == "sid-a"


def test_resume_by_number_from_sessions_list(store, monkeypatch):
    proj = str(store / "p")
    _save(proj, "thread A", "sid-a")
    _save(proj, "thread B", "sid-b")
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[],
                    project=proj, title="")
    heads = sessions_mod.list_sessions(proj)
    want = heads[1]["id"]  # the second entry as /sessions numbers it
    cli.handle_command("/resume 2", s)
    assert s.session_id == want


def test_resume_unknown_id_is_reported(store, monkeypatch):
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[],
                    project=str(store / "p"), title="")
    cli.handle_command("/resume nope", s)
    assert "no session matching" in "".join(out)
    assert s.session_id == ""
