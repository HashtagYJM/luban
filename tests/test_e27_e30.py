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


# ---------------- E29: caps are configurable ----------------

def test_caps_are_config_overridable(mem, monkeypatch):
    monkeypatch.setattr(memory, "USER_MAX", 10_000)
    memory.apply_caps(user=25_000)
    assert memory.USER_MAX == 25_000


def test_zero_means_keep_the_default(mem, monkeypatch):
    monkeypatch.setattr(memory, "USER_MAX", 10_000)
    memory.apply_caps(user=0)
    assert memory.USER_MAX == 10_000


def test_config_exposes_the_cap_keys(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\nuser_max = 20000\nindex_max = 15000\n', encoding="utf-8")
    cfg = config_mod.load_config(p)
    assert cfg.user_max == 20_000 and cfg.index_max == 15_000
    assert config_mod.Config(platform="mac").user_max == 0  # 0 = derived default


def test_a_real_profile_now_fits(mem):
    """The field case: a 6,810-char profile lost 2,810 chars to a hard-coded 4,000."""
    memory.USER_PATH.write_text("## About me\n" + "x" * 6_798, encoding="utf-8")
    assert len(memory.USER_PATH.read_text()) == 6_810
    assert "truncated" not in memory.read_user()


# ---------------- E29: auto-compaction is offered, not silent ----------------

def test_over_budget_file_is_offered_for_compaction(mem, monkeypatch):
    memory.USER_PATH.write_text("y" * (memory.USER_MAX + 3_000), encoding="utf-8")
    out, asked = [], []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    monkeypatch.setattr("builtins.input", lambda p: asked.append(p) or "n")
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[])
    cli.offer_tidy(client=object(), ctx=None, cfg=config_mod.Config(platform="mac"), session=s)
    text = "".join(out)
    assert "NOT reaching the model" in text
    assert any("compact USER.md now?" in a for a in asked)   # ASKS, never acts alone
    assert "left as-is" in text                              # declining is respected
    assert "user_max" in text                                # names the other way out


def test_a_file_within_budget_is_not_nagged(mem, monkeypatch):
    memory.USER_PATH.write_text("## About me\nshort.", encoding="utf-8")
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail("should not ask"))
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
