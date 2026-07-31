"""E30 skill block scalars, E29 configurable caps + auto-compaction, E28 volatile
refresh, E27 length-biased ranking."""
import pytest

from luban import agent, cli, config as config_mod, memory
from luban.skills import _parse


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    return tmp_path


# ---------------- E30: YAML block scalars in skill frontmatter ----------------

def test_folded_block_scalar_description(mem):
    """`description: >` put the text on continuation lines; taking only the first line
    left the catalog entry as a bare '>' — no trigger text at all, and it happened to
    exactly the richest skills (the ones long enough to need folding)."""
    desc, _ = _parse("---\ndescription: >\n  Methodology for quant research:\n"
                     "  plan-then-code, TDD, verify before scaffold.\nname: q\n---\nbody")
    assert desc == ("Methodology for quant research: plan-then-code, TDD, "
                    "verify before scaffold.")


def test_literal_block_scalar_keeps_line_breaks(mem):
    desc, _ = _parse("---\ndescription: |\n  Line one.\n  Line two.\n---\nbody")
    assert desc == "Line one.\nLine two."


@pytest.mark.parametrize("indicator", [">", "|", ">-", "|-", ">+", "|+"])
def test_every_block_indicator_is_consumed(indicator):
    desc, _ = _parse(f"---\ndescription: {indicator}\n  Real text here.\n---\nbody")
    assert desc.strip() == "Real text here."
    assert desc.strip() not in (">", "|")


def test_block_scalar_stops_at_the_next_key():
    desc, _ = _parse("---\ndescription: >\n  Folded text.\nname: other\n---\nbody")
    assert desc == "Folded text." and "other" not in desc


def test_plain_single_line_description_unchanged():
    desc, _ = _parse("---\ndescription: A one liner\n---\nbody")
    assert desc == "A one liner"


# ------------- E29: ONE budget, no per-file caps, no config knobs -------------

def test_there_is_exactly_one_always_on_budget():
    """Five per-file caps (and the four config keys I added for them) were the wrong
    shape: a knob is an admission the right value is unknown, handed to the user."""
    for gone in ("SOUL_MAX", "USER_MAX", "INDEX_MAX", "JOURNAL_MAX"):
        assert not hasattr(memory, gone)
    assert memory.ALWAYS_ON_BUDGET > 0


def test_no_cap_config_keys_remain():
    cfg = config_mod.Config(platform="mac")
    assert [k for k in vars(cfg) if k.endswith("_max")] == []


def test_a_real_profile_is_never_truncated(mem):
    """The field case: a 6,810-char profile lost 2,810 chars to a hard-coded 4,000."""
    memory.USER_PATH.write_text("## About me\n" + "x" * 6_798, encoding="utf-8")
    out = memory.read_user()
    assert len(out) == 6_810 and "EXCEEDS" not in out


def test_only_a_pathological_file_is_trimmed(mem):
    memory.USER_PATH.write_text("x" * (memory.ALWAYS_ON_BUDGET + 5_000), encoding="utf-8")
    assert "EXCEEDS THE ENTIRE ALWAYS-ON BUDGET" in memory.read_user()


def test_the_total_is_what_warns(mem):
    memory.USER_PATH.write_text("u" * 20_000, encoding="utf-8")
    memory.SOUL_PATH.write_text("s" * 20_000, encoding="utf-8")
    w = memory.cap_warnings(memory.always_on_usage())
    assert len(w) == 1 and "always-on context is" in w[0]
    assert "still being sent" in w[0]  # nothing cut; it is a prompt to consolidate


# ---------------- E29: auto-compaction is offered, not silent ----------------

def test_over_budget_file_is_offered_for_compaction(mem, monkeypatch):
    memory.USER_PATH.write_text("y" * (memory.ALWAYS_ON_BUDGET + 3_000), encoding="utf-8")
    monkeypatch.setattr(cli, "always_on_usage", lambda *a: [("USER.md", memory.ALWAYS_ON_BUDGET + 3_000)])
    out, asked = [], []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    monkeypatch.setattr("builtins.input", lambda p: asked.append(p) or "n")
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[])
    cli.offer_tidy(client=object(), ctx=None, cfg=config_mod.Config(platform="mac"), session=s)
    text = "".join(out)
    assert "against a" in text and "budget" in text
    assert any("USER.md" in a and "compact" in a for a in asked)  # ASKS, never acts
    assert "left as-is" in text                              # declining is respected
    assert "/reflect" in text                                # names the way out


def test_a_file_within_budget_is_not_nagged(mem, monkeypatch):
    memory.USER_PATH.write_text("## About me\nshort.", encoding="utf-8")
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail("should not ask"))
    monkeypatch.setattr(cli, "always_on_usage", lambda *a: [("USER.md", 50)])
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[])
    cli.offer_tidy(client=object(), ctx=None, cfg=config_mod.Config(platform="mac"), session=s)
    assert out == []


def test_tidy_prompt_compacts_rather_than_deletes():
    assert "compaction, not deletion" in cli.TIDY_PROMPT
    assert "Keep every distinct instruction" in cli.TIDY_PROMPT
    assert "confirm" in cli.TIDY_PROMPT


# ---------------- E28: the volatile block refreshes per model call ----------------

def test_volatile_is_re_rendered_every_call(monkeypatch):
    """It was captured once per user turn, so a fact saved mid-turn stayed invisible —
    and the model was then told a fact it had just written did not exist."""
    seen, state = [], {"n": 0}

    def fresh():
        state["n"] += 1
        return f"INDEX v{state['n']}"

    def fake(client, *, system, **kw):
        seen.append(system if isinstance(system, str) else system[-1]["text"])
        return type("M", (), {"stop_reason": "end_turn", "content": []})()

    monkeypatch.setattr(agent.client_mod, "create_turn", fake)
    cfg = agent.AgentConfig("m", 100, stream=False, volatile_fn=fresh, tools=[],
                            cache_prompt=False)
    agent._run_model_turn(None, cfg, [], lambda t: None, None)
    agent._run_model_turn(None, cfg, [], lambda t: None, None)
    assert "INDEX v1" in seen[0] and "INDEX v2" in seen[1]  # not the same snapshot


def test_without_volatile_fn_the_static_string_is_used(monkeypatch):
    seen = []
    monkeypatch.setattr(agent.client_mod, "create_turn",
                        lambda client, *, system, **kw: seen.append(system) or
                        type("M", (), {"stop_reason": "end_turn", "content": []})())
    cfg = agent.AgentConfig("m", 100, stream=False, global_volatile="STATIC", tools=[],
                            cache_prompt=False)
    agent._run_model_turn(None, cfg, [], lambda t: None, None)
    assert "STATIC" in seen[0]


# ---------------- E27: ranking is no longer length-biased ----------------

def test_a_huge_generic_fact_no_longer_outranks_the_right_one(mem):
    memory.remember("enhancements", "tracker of issues",
                    "tracker " * 900 + " coding style ruff type hints written")
    memory.remember("yjm-coding-style", "how the user likes code written",
                    "Prefers ruff and type hints.")
    out = memory.recall("how does the user like their code written")
    first = next(l for l in out.splitlines() if l.startswith("["))
    assert first == "[yjm-coding-style]"


def test_one_oversized_fact_cannot_eat_the_whole_budget(mem):
    memory.remember("huge", "big widget doc", "widget " * 3000)
    memory.remember("small", "small widget note", "widget, briefly.")
    out = memory.recall("widget")
    assert "[small]" in out                      # the second hit survives
    assert "[trimmed —" in out                   # the oversized one was capped
    assert "recall('huge')" in out               # and says how to read it whole


def test_fetch_by_slug_is_never_trimmed(mem):
    memory.remember("huge", "big doc", "z" * 5000)
    out = memory.recall("huge")                  # exact slug = fetch, not search
    assert "[trimmed —" not in out and out.count("z") == 5000


def test_coverage_beats_repetition(mem):
    """Matching MORE of the query should win over repeating ONE word."""
    memory.remember("repeats", "one word many times", "ruff " * 200)
    memory.remember("covers", "all the terms", "ruff type hints plotly")
    out = memory.recall("ruff type hints plotly")
    first = next(l for l in out.splitlines() if l.startswith("["))
    assert first == "[covers]"


# ---------------- root-cause completions (from auditing my own fixes) ----------------

def test_any_broken_frontmatter_is_loud_not_just_block_scalars(capsys):
    """I fixed ONE YAML gap (block scalars) and left the class silent. We hand-roll
    YAML, so there will be another gap — the failure has to be visible."""
    from luban.skills import _parse
    _parse("---\nname: x\n---\nsome body text")
    assert "no usable `description:`" in capsys.readouterr().err


def test_a_maintained_document_does_not_compete_in_the_fact_lane(mem):
    """The tracker is a large hand-edited DOCUMENT filed as an atomic fact, and it is
    self-referential — it quotes past queries, so it wins searches about problems it
    recorded. Normalising scores masked that; the category error was the real cause."""
    memory.remember("enhancements", "tracker", "E27 recall ranking coding style problem")
    memory.remember("yjm-coding-style", "how code is written", "Prefers ruff.")
    facts = [l for l in memory.recall("coding style").splitlines() if l.startswith("[")]
    assert facts == ["[yjm-coding-style]"]


def test_a_document_is_still_reachable_by_name(mem):
    memory.remember("enhancements", "tracker", "the open items")
    assert memory.recall("enhancements").startswith("[enhancements]")


# --- v0.5.18 follow-up: a SHARED budget needs a remedy for EVERY contributor ----------
# offer_tidy used to consider only SOUL.md and USER.md, then pick the biggest of those
# two. So a 30,000-char fact index made it offer to compact an innocent 2,000-char
# USER.md, and the project memory file was never offered at all.

def _tidy(monkeypatch, usage, answer="n"):
    out, asked = [], []
    monkeypatch.setattr(cli, "always_on_usage", lambda *a: usage)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    monkeypatch.setattr("builtins.input", lambda p: asked.append(p) or answer)
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[])
    cli.offer_tidy(client=object(), ctx=None, cfg=config_mod.Config(platform="mac"),
                   session=s)
    return "".join(out), asked


def test_bloated_index_is_not_blamed_on_an_innocent_user_md(mem, monkeypatch):
    """The index is the culprit; USER.md must NOT be the file put up for compaction."""
    text, asked = _tidy(monkeypatch, [("USER.md", 2_000), ("SOUL.md", 1_000),
                                      ("memory index", 40_000)])
    assert "memory index" in text and "/reflect" in text
    assert not asked, f"offered a compaction prompt for the wrong file: {asked}"


def test_project_memory_file_is_named_but_never_rewritten(mem, monkeypatch, tmp_path):
    """A repo file may be shared by a team — luban reports it and does NOT offer to
    rewrite it. Explicit user decision: do not make the project file compactable."""
    (tmp_path / "LUBAN.md").write_text("z" * 40_000, encoding="utf-8")
    out, asked = [], []
    monkeypatch.setattr(cli, "always_on_usage", lambda *a: [("LUBAN.md", 40_000)])
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    monkeypatch.setattr("builtins.input", lambda p: asked.append(p) or "n")
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[])
    cli.offer_tidy(client=object(), ctx=None, cfg=config_mod.Config(platform="mac"),
                   session=s, project_root=tmp_path)
    text = "".join(out)
    assert "LUBAN.md" in text and "40,000" in text      # named, with its size
    assert not asked, f"must not offer to rewrite a repo file: {asked}"


def test_project_memory_is_never_a_compact_target(mem, tmp_path):
    cfg = config_mod.Config(platform="mac")
    for name in ("LUBAN.md", "CLAUDE.md", "AGENTS.md"):
        kind, _p = cli.always_on_remedy(name, tmp_path, cfg)
        assert kind == "advise", f"{name} must not be compactable, got {kind}"


def test_self_limiting_journal_does_not_stall_the_offer(mem, monkeypatch):
    """The journal auto-decays, so it is skipped for the next ACTIONABLE contributor."""
    memory.USER_PATH.write_text("y" * 20_000, encoding="utf-8")
    text, asked = _tidy(monkeypatch, [("journal", 25_000), ("USER.md", 20_000)])
    assert any("USER.md" in a for a in asked), "journal at the top blocked the offer"


def test_every_contributor_is_listed_so_the_user_can_see_the_real_culprit(mem, monkeypatch):
    text, _ = _tidy(monkeypatch, [("USER.md", 2_000), ("memory index", 40_000),
                                  ("journal", 500)])
    for label in ("USER.md", "memory index", "journal"):
        assert label in text


def test_reflect_ledger_includes_the_project_memory_file(mem, tmp_path):
    """A ledger that omits a contributor under-reports the total it is policing."""
    ledger = memory.always_on_budget([("LUBAN.md", 9_000)])
    assert "LUBAN.md" in ledger and "9,000" in ledger


def test_policy_every_contributor_is_bounded_or_has_a_working_remedy(mem, tmp_path):
    """THE class-level guard (E31).

    E31 happened because the journal had NEITHER a size bound NOR a remedy that could
    shrink it, and nothing detected that combination. An earlier draft of this test asserted
    "render a worst case, assert the whole block fits ALWAYS_ON_BUDGET" — which is
    UNSATISFIABLE, because an oversized USER.md is deliberately never cut, so no journal
    bound can make the total fit. Bounding the block and never cutting authored prose are
    mutually exclusive, and the design chose the latter.

    So the invariant is per-contributor: each one is either windowed to a declared
    allowance, or has a remedy that can actually reduce it. Unbounded AND remedy "none"
    fails — which is precisely the state the journal was in. Add a new always-on component
    without declaring how it shrinks and this fails, with nobody having to extend it.
    """
    cfg = config_mod.Config(platform="mac")
    reduces = {"compact", "reflect", "advise"}   # something can act on it
    bounded = {"windowed"}                       # it cannot grow without limit
    for label, _size in cli.always_on_usage(tmp_path, cfg) + [("LUBAN.md", 1)]:
        kind, _path = cli.always_on_remedy(label, tmp_path, cfg)
        assert kind in reduces | bounded, (
            f"{label!r} is an always-on contributor with remedy {kind!r} — it can grow "
            "without bound and nothing can shrink it. That is the E31 defect class.")


# ---------------- E31: the journal is bounded, and says so ----------------

def _write_day(day: str, entries: int, pad: int = 200) -> int:
    d = memory.MEMORY_DIR / "journal"
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f"[09:{i:02d}] entry {i} {'x' * pad}\n" for i in range(entries))
    (d / f"{day}.md").write_text(body, encoding="utf-8")
    return len(body)


def test_journal_over_allowance_is_cut_on_a_day_boundary(mem):
    _write_day("2026-07-20", 40)          # oldest
    _write_day("2026-07-21", 40)          # newest
    out = memory.read_recent_journal()
    assert len(out) <= memory._journal_allowance() + 300      # + the notice line
    assert "2026-07-21" in out, "the NEWEST day must survive"
    assert "2026-07-20" not in out, "the OLDEST day must roll off"


def test_a_single_oversized_day_yields_its_newest_entries_not_nothing(mem):
    _write_day("2026-07-23", 300)         # one day far bigger than the whole allowance
    out = memory.read_recent_journal()
    assert out, "an oversized day must not produce an empty journal"
    assert len(out) <= memory._journal_allowance() + 300
    assert "entry 299" in out, "the NEWEST entries are the ones to keep"
    assert "entry 0" not in out
    # cut on an ENTRY boundary — no fragment of a line
    for line in out.splitlines():
        if line.startswith("[09:"):
            assert line.rstrip().endswith("x"), f"cut mid-entry: {line[-40:]!r}"


def test_the_omission_is_stated_never_silent(mem):
    _write_day("2026-07-20", 40)
    _write_day("2026-07-21", 40)
    assert "journal:" in memory.read_recent_journal()      # says what it left out
    assert "~/.luban/memory/journal/" in memory.read_recent_journal()  # and where it lives


def test_a_small_journal_is_untouched_and_unannotated(mem):
    _write_day("2026-07-21", 2)
    out = memory.read_recent_journal()
    assert "entry 0" in out and "entry 1" in out
    assert "journal:" not in out, "no notice when nothing was omitted"


def test_reading_never_writes_to_the_day_files(mem):
    """'Trimming is lossless' is the entire justification for windowing — verify it."""
    n = _write_day("2026-07-23", 300)
    before = (memory.MEMORY_DIR / "journal" / "2026-07-23.md").read_bytes()
    memory.read_recent_journal()
    memory.bootstrap_volatile()
    after = (memory.MEMORY_DIR / "journal" / "2026-07-23.md").read_bytes()
    assert before == after and len(after) == n


def test_the_ledger_reports_what_is_actually_sent(mem):
    """always_on_usage must report the BOUNDED journal, not a file that never went."""
    _write_day("2026-07-23", 300)
    journal_row = dict(memory.always_on_usage())["journal"]
    assert journal_row == len(memory.read_recent_journal())
    assert journal_row <= memory._journal_allowance() + 300


# ---------------- descriptions are trigger text: never truncated ----------------

def test_skill_descriptions_reach_the_prompt_intact(tmp_path):
    """The description IS the trigger text the model matches a task against.

    Two caps used to cut it — 240 for frontmatter, 80 for a plain .md — and a field
    measurement found 943/886/785-char descriptions reduced to ~235, discarding three
    quarters of the trigger text on the richest skills. v0.5.19 made one of them WARN
    rather than removing it, which produced a warning per skill per command (18 lines on a
    real install). Decorating a bad bound is not fixing it. The catalog is bounded by the
    one shared always-on budget, like every other contributor.
    """
    from luban import skills as skills_mod
    long_desc = " ".join(f"word{i:03d}" for i in range(190))          # ~1,330 chars
    d = tmp_path / "skills" / "rich"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: rich\ndescription: {long_desc}\n---\nbody",
                                encoding="utf-8")
    desc, _ = skills_mod._parse((d / "SKILL.md").read_text(encoding="utf-8"))
    assert desc == long_desc, "frontmatter description must reach the prompt whole"

    plain = "Use this whenever the user wants quarterly attribution reconciled against " \
            "the custodian file and the monthly commentary pack produced end to end."
    desc2, _ = skills_mod._parse(plain + "\n\nbody")
    assert desc2 == plain, "plain .md description must reach the prompt whole"


def test_no_hidden_description_cap_remains(tmp_path, capsys):
    from luban import skills as skills_mod
    assert not hasattr(skills_mod, "_FRONT_DESC_MAX")
    assert not hasattr(skills_mod, "_DESC_MAX")
    skills_mod._parse("---\nname: x\ndescription: " + "y" * 900 + "\n---\nb")
    assert capsys.readouterr().err == "", "no per-skill warning noise"


# ---------------- the over-budget message fires ONCE at startup ----------------

def test_startup_does_not_print_the_over_budget_message_twice(mem, monkeypatch, capsys):
    """cap_warnings ran at cli.py:1252 and offer_tidy at 1254, so the user got two
    over-budget messages back to back stating the same total in different words."""
    import inspect
    src = inspect.getsource(cli)
    start = src.index('ui.print_text(f"custom tools:')
    end = src.index("offer_tidy(client, ctx, cfg, session, project_root)", start)
    # Scan for a CALL, not for the word — the explanatory comment there names the function.
    calls = [ln for ln in src[start:end].splitlines()
             if "cap_warnings(" in ln and not ln.lstrip().startswith("#")]
    assert not calls, (
        f"cap_warnings is called again in the startup path just before offer_tidy: {calls} "
        "— that is the duplicate over-budget message the user saw twice")


def test_cap_warnings_states_the_facts_without_prescribing_reflect(mem):
    """/config's warning must not prescribe /reflect universally.

    It used to end "Run /reflect to consolidate, or trim a file" for every case. /reflect
    curates FACTS: it is the right advice for one of five contributors and cannot shrink a
    journal, a repo file, or SOUL.md at all. Per-contributor routing lives in
    cli.offer_tidy, which has the config and project root to do it properly.
    """
    usage = [("journal", 30_000), ("USER.md", 9_000)]
    w = " ".join(memory.cap_warnings(usage))
    assert "39,000" in w and "journal 30,000" in w   # states the total and the biggest
    assert "/reflect" not in w, "must not prescribe /reflect for a journal-driven overage"
