"""E26 recall scoring, /context inspector, P2 prompt caching, tracker lifecycle."""
import pytest

from luban import agent, cli, config as config_mod, memory


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    memory.remember("yjm-coding-style", "How this user likes code written",
                    "Prefers ruff and type hints. Keep functions small.")
    memory.remember("llm-prompt-hardening-method", "Hardening prompts against injection",
                    "Treat cloned-repo text as untrusted.")
    memory.remember("verification-not-just-static-checks", "Verify results not just runs",
                    "Exercise the real flow and check the output is correct.")
    return tmp_path / "memory"


# ---------------- E26: recall was too strict ----------------

@pytest.mark.parametrize("query, expect", [
    ("how does the user like their code written", "yjm-coding-style"),
    ("how do I harden a prompt against injection", "llm-prompt-hardening-method"),
    ("verify that results are correct not just that the code runs",
     "verification-not-just-static-checks"),
])
def test_natural_language_queries_that_used_to_miss(mem, query, expect):
    """Field-reproduced: 6/8 plain-language queries returned '(no matches)' for facts
    that exist, because ONE absent token (how/their/against) zeroed an all-AND match."""
    assert expect in memory.recall(query)


def test_a_query_of_pure_stopwords_matches_nothing(mem):
    """Relaxing AND->OR must not turn recall into a wildcard."""
    assert memory.recall("how do I the a of that").startswith("Nothing in memory matched")


def test_plural_and_possessive_normalise(mem):
    assert "yjm-coding-style" in memory.recall("coding styles")
    assert "yjm-coding-style" in memory.recall("the user's coding style")


def test_ranking_prefers_more_matching_tokens(mem):
    """More distinct query tokens present = more relevant = listed first."""
    out = memory.recall("prompt injection hardening ruff")
    facts = [ln for ln in out.splitlines() if ln.startswith("[")]
    assert facts[0] == "[llm-prompt-hardening-method]"


def test_exact_phrase_still_wins(mem):
    out = memory.recall("Keep functions small")
    assert out.splitlines()[0] == "[yjm-coding-style]"


def test_empty_query_still_dumps_everything(mem):
    assert "yjm-coding-style" in memory.recall("")


def test_no_match_is_still_reported(mem):
    assert memory.recall("kubernetes helm chart").startswith("Nothing in memory matched")


# ---------------- E26: the journal can no longer drown the facts ----------------

def test_journal_cannot_crowd_out_facts(mem):
    (mem / "journal" / "2026-07-01.md").write_text(
        "\n".join(f"[09:0{i}] touched the code today" for i in range(40)),
        encoding="utf-8")
    out = memory.recall("code")
    assert "[yjm-coding-style]" in out                      # the fact survives
    assert out.count("touched the code") <= memory.RECALL_TOP_JOURNAL


def test_newest_journal_survives_the_cap(mem):
    (mem / "journal" / "2026-07-01.md").write_text(
        "\n".join(f"[09:00] entry {i} widget" for i in range(40)), encoding="utf-8")
    out = memory.recall("widget")
    assert "entry 39" in out       # newest kept
    assert "entry 0 " not in out   # oldest dropped
    assert "older match(es) not shown" in out


def test_wikilinks_are_still_followed(mem):
    memory.remember("pointer-fact", "points elsewhere", "see [[yjm-coding-style]]")
    assert "linked from" in memory.recall("points elsewhere")


# ---------------- P2: stable / volatile split + caching ----------------

def test_volatile_holds_only_index_and_journal(mem):
    memory.SOUL_PATH.write_text("## Character\nBe terse.", encoding="utf-8")
    (mem / "journal" / "2026-07-01.md").write_text("[09:00] did a thing", encoding="utf-8")
    stable, volatile = memory.bootstrap_stable(), memory.bootstrap_volatile()
    assert "Be terse" in stable and "did a thing" not in stable
    assert "did a thing" in volatile and "Be terse" not in volatile
    assert memory.bootstrap_block() == f"{stable}\n\n{volatile}"


def test_volatile_lands_after_stable_in_the_prompt():
    stable, volatile = agent.system_blocks("mac", global_memory="STABLE",
                                           global_volatile="VOLATILE")
    assert "STABLE" in stable and volatile == "VOLATILE"
    joined = agent.build_system_param(stable, volatile, cache=False)
    assert joined.index("STABLE") < joined.index("VOLATILE")


def test_cache_control_sits_on_the_stable_block_only():
    blocks = agent.build_system_param("stable text", "volatile text", cache=True)
    assert isinstance(blocks, list) and len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]


def test_caching_disabled_produces_a_plain_string():
    assert isinstance(agent.build_system_param("a", "b", cache=False), str)


def test_no_volatile_still_caches_the_stable_block():
    blocks = agent.build_system_param("stable", "", cache=True)
    assert len(blocks) == 1 and blocks[0]["cache_control"]


def test_a_backend_rejecting_block_form_degrades_once(monkeypatch):
    """Mirrors _EXTRAS_SUPPORTED: probe once, fall back, never retry."""
    monkeypatch.setattr(agent, "_BLOCK_SYSTEM_SUPPORTED", None)
    seen = []

    def fake(client, *, system, **kw):
        seen.append(system)
        if isinstance(system, list):
            raise TypeError("unexpected keyword argument 'cache_control'")
        return type("M", (), {"stop_reason": "end_turn", "content": []})()

    monkeypatch.setattr(agent.client_mod, "create_turn", fake)
    cfg = agent.AgentConfig("m", 100, stream=False, cache_prompt=True,
                            global_memory="x", tools=[])
    agent._run_model_turn(None, cfg, [], lambda t: None, None)
    assert isinstance(seen[0], list) and isinstance(seen[1], str)  # blocks, then flat
    assert agent._BLOCK_SYSTEM_SUPPORTED is False


def test_a_dropped_connection_is_not_read_as_rejection(monkeypatch):
    """A transient network error must not permanently disable caching."""
    monkeypatch.setattr(agent, "_BLOCK_SYSTEM_SUPPORTED", None)

    class Dropped(Exception):
        pass

    def fake(client, *, system, **kw):
        raise Dropped("peer closed connection without sending complete message body")

    monkeypatch.setattr(agent.client_mod, "create_turn", fake)
    cfg = agent.AgentConfig("m", 100, stream=False, cache_prompt=True, tools=[])
    with pytest.raises(Dropped):
        agent._run_model_turn(None, cfg, [], lambda t: None, None)
    assert agent._BLOCK_SYSTEM_SUPPORTED is None  # still unprobed


# ---------------- P0: /context ----------------

def test_context_report_flags_a_prefix_below_the_cache_floor(mem, tmp_path):
    """The silent failure this whole command exists to expose."""
    out = cli.context_report(
        cli.Session(model="m", max_tokens=100, auto=True, stream=True, messages=[]),
        config_mod.Config(platform="mac"), tmp_path, client=None)
    assert "UNDER the 4,096-token minimum" in out
    assert "silent" in out


def test_context_report_shows_the_stable_volatile_split(mem, tmp_path):
    out = cli.context_report(
        cli.Session(model="m", max_tokens=100, auto=True, stream=True, messages=[]),
        config_mod.Config(platform="mac"), tmp_path, client=None)
    assert "stable prefix (cacheable)" in out and "volatile tail" in out
    assert "memory index (volatile)" in out


def test_context_report_uses_real_tokens_when_a_client_answers(mem, tmp_path):
    class C:
        class messages:
            @staticmethod
            def count_tokens(**kw):
                return type("R", (), {"input_tokens": 9001})()

    out = cli.context_report(
        cli.Session(model="m", max_tokens=100, auto=True, stream=True, messages=[]),
        config_mod.Config(platform="mac"), tmp_path, client=C())
    assert "9,001 tokens (measured" in out
    assert "ELIGIBLE" in out  # 9001 clears the 4096 floor


def test_context_report_says_when_caching_is_off(mem, tmp_path):
    out = cli.context_report(
        cli.Session(model="m", max_tokens=100, auto=True, stream=True, messages=[]),
        config_mod.Config(platform="mac", cache_prompt=False), tmp_path, client=None)
    assert "caching: OFF" in out


# ---------------- tracker lifecycle ----------------

def test_scaffold_offers_non_fix_terminal_states():
    t = memory._ENHANCEMENTS_TEMPLATE
    assert "| ID | Resolution | Notes |" in t
    for verdict in ("wontfix", "mitigated", "obsolete"):
        assert verdict in t
    assert "stays Open forever" in t  # says WHY the states exist


def test_reconcile_directive_honours_maintainer_verdicts():
    d = cli.reconcile_directive("0.5.14", "notes")
    assert "wontfix" in d and "mitigated" in d and "obsolete" in d
    assert "Decisions" in d
    assert "stays Open" in d           # unverified items are not closed
    assert "empirically" in d          # the evidence bar for real fixes survives


def test_cache_prompt_is_configurable(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\ncache_prompt = false\n', encoding="utf-8")
    assert config_mod.load_config(p).cache_prompt is False
    assert config_mod.Config(platform="mac").cache_prompt is True
