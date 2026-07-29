"""Memory as a curated store, not a search problem.

The model already receives an index of EVERY fact on every turn, so recall is a FETCH.
Matching exists only for exploration, and must fail honestly — '(no matches)' read as
'that fact does not exist' is what made the model save duplicates in the first place.
"""
import pytest

from luban import cli, memory


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    return tmp_path / "memory"


# ---------------- fetch-by-name is the primary path ----------------

def test_an_exact_slug_reads_that_fact(mem):
    memory.remember("prefers-plotly", "charting preference", "Use plotly, not matplotlib.")
    memory.remember("company-python-env", "work env", "conda + pip + python 3.11")
    out = memory.recall("prefers-plotly")
    assert out.startswith("[prefers-plotly]") and "plotly, not matplotlib" in out
    assert "company-python-env" not in out  # a fetch returns ONE fact, not a ranked list


def test_slug_fetch_beats_a_fact_that_merely_mentions_it(mem):
    """The old scorer could rank a chatty fact above the one you named."""
    memory.remember("ruff-usage", "linting", "Use ruff.")
    memory.remember("noisy", "mentions everything",
                    "ruff-usage ruff-usage ruff-usage ruff ruff ruff")
    assert memory.recall("ruff-usage").startswith("[ruff-usage]")


def test_slug_fetch_still_follows_wikilinks(mem):
    memory.remember("target", "the detail", "the real content")
    memory.remember("pointer", "a pointer", "details live at [[target]]")
    out = memory.recall("pointer")
    assert "[target]" in out and "linked from" in out


def test_the_recall_tool_tells_the_model_to_pass_a_slug():
    from luban import tools
    t = next(x for x in tools.TOOLS if x["name"] == "recall")
    assert "exact slug" in t["input_schema"]["properties"]["query"]["description"]
    assert "index" in t["description"]  # points at the catalog it already has


# ---------------- failure must not read as absence ----------------

def test_a_miss_does_not_imply_the_fact_is_absent(mem):
    memory.remember("a-fact", "something", "content")
    out = memory.recall("kubernetes helm chart")
    assert "does NOT mean the fact does not exist" in out
    assert "index" in out                       # points at the real catalog
    assert "Do not save a new fact" in out      # blocks the duplicate-creating reflex
    assert out != "(no matches)"


# ---------------- audit(): the curator sees everything ----------------

def test_audit_returns_the_whole_store(mem):
    for i in range(5):
        memory.remember(f"fact-{i}", f"desc {i}", f"body number {i}")
    out = memory.audit()
    for i in range(5):
        assert f"body number {i}" in out        # every BODY, not just the index
    assert "5 facts" in out


def test_audit_is_not_capped_like_recall(mem):
    """recall caps at RECALL_MAX, which showed /reflect a fraction of the store."""
    big = "x" * 3000
    for i in range(6):
        memory.remember(f"fact-{i}", f"desc {i}", big)
    assert len(memory.audit()) > memory.RECALL_MAX


def test_audit_flags_duplicate_candidates(mem):
    memory.remember("coding-style", "how the user likes code written",
                    "prefers ruff and type hints on every function")
    memory.remember("user-code-preferences", "how the user likes code written",
                    "prefers ruff and type hints on every function")
    memory.remember("unrelated", "the weather", "it rains in April")
    out = memory.audit()
    assert "POSSIBLE DUPLICATES" in out
    assert "coding-style" in out and "user-code-preferences" in out
    pairs = memory.duplicate_candidates()
    assert not any("unrelated" in (a, b) for a, b, _ in pairs)


def test_audit_on_an_empty_store(mem):
    assert "empty" in memory.audit()


# ---------------- the cap becomes a forcing function ----------------

def test_an_over_budget_index_tells_the_MODEL_to_consolidate(mem):
    for i in range(80):
        memory.remember(f"fact-{i:03d}-{'z' * 25}", "a fairly long description " * 3, "b")
    volatile = memory.bootstrap_volatile()
    assert "over its always-on budget" in volatile
    assert "/reflect" in volatile          # names the remedy
    assert "degrades how well you follow" in volatile  # says WHY, not just that


def test_a_healthy_store_adds_no_nagging(mem):
    memory.remember("one", "a fact", "body")
    assert "over its always-on budget" not in memory.bootstrap_volatile()


# ---------------- the reflect procedure ----------------

def test_reflect_prompt_is_a_procedure_with_graduation():
    p = cli.REFLECT_PROMPT
    for step in ("SURVEY", "MERGE", "RESOLVE", "DELETE", "GRADUATE", "TIGHTEN", "REPORT"):
        assert step in p
    assert "USER.md" in p
    assert "cannot know to recall it before you act" in p   # why graduation exists
    assert "transcript" in p.lower()                        # deleting is safe
    assert "diff and confirm" in p or "confirms" in p       # user still approves


def test_reflect_prompt_carries_the_store_not_a_recall_instruction():
    """The curator is handed the corpus; it must not be told to go fishing with recall."""
    assert "COMPLETE fact store" in cli.REFLECT_PROMPT
    assert "you do not need recall" in cli.REFLECT_PROMPT.lower()


def test_ordinary_turns_never_carry_the_audit_payload(mem):
    """audit() is for the isolated /reflect turn only — it must not leak into always-on."""
    for i in range(5):
        memory.remember(f"fact-{i}", f"desc {i}", "a distinctive body string")
    block = memory.bootstrap_block()
    assert "a distinctive body string" not in block
    assert "THE COMPLETE FACT STORE" not in block


# (the leak-guard word-boundary test lives in tests/test_no_leak.py — that file is
#  self-excluded from the guard, so it can hold a literal forbidden token as a fixture)


# ---------------- graduation must not make USER.md the new dumping ground ----------

def test_graduation_is_bounded_by_the_always_on_budget():
    """The fix for fact-store rot must not open a rot pathway into USER.md — which is
    WORSE, because it head-truncates instead of degrading gracefully."""
    p = cli.REFLECT_PROMPT
    assert "NOT A DUMPING GROUND" in p
    assert "TRADE, not an append" in p
    assert "every line costs forever" in p
    assert "tighten or drop weaker lines" in p     # you must pay for what you add
    assert "Promoting nothing is a fine outcome" in p


def test_audit_shows_always_on_headroom(mem):
    memory.USER_PATH.write_text("## About me\n" + "x" * 500, encoding="utf-8")
    out = memory.audit()
    assert "ALWAYS-ON FILES" in out
    assert "USER.md" in out and "free" in out
    assert "do NOT degrade gracefully" in out


def test_audit_flags_an_over_budget_user_md(mem):
    memory.USER_PATH.write_text("y" * (memory.USER_MAX + 500), encoding="utf-8")
    assert "OVER BUDGET" in memory.always_on_budget()


def test_budget_report_survives_missing_files(mem):
    out = memory.always_on_budget()
    assert "USER.md  0" in out and "SOUL.md  0" in out
