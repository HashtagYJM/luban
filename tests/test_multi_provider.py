"""Cross-provider /model: one Anthropic-shaped surface over two backends.

The driver is capability, not cost — the target model is the MORE expensive one. So these
tests are about fidelity: the Anthropic path must be untouched, and the OpenAI path must
either translate a thing faithfully or say out loud that it cannot.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from luban import (cli, client as client_mod, config as config_mod, memory,
                   usage as usage_mod)
from luban.providers import openai as oa


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """Keep /context off the real ~/.luban."""
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    return tmp_path / "memory"


# ------------------------------------------------------------------ fake backend ----

def _resp(output, *, status="completed", usage=None, incomplete=None):
    return SimpleNamespace(output=output, status=status, usage=usage,
                           incomplete_details=incomplete)


class FakeOpenAI:
    """Records the Responses requests it is handed and returns scripted results."""

    def __init__(self, *scripted):
        self.requests = []
        self._scripted = list(scripted)
        outer = self

        class _Responses:
            @staticmethod
            def create(**req):
                outer.requests.append(req)
                return outer._scripted.pop(0)

        class _Models:
            @staticmethod
            def list():
                return [SimpleNamespace(id="gpt-5.6")]

        self.responses = _Responses()
        self.models = _Models()


class FakeAnthropic:
    def __init__(self):
        self.calls = []
        outer = self

        class _Messages:
            @staticmethod
            def create(**kw):
                outer.calls.append(kw)
                return SimpleNamespace(content=[], stop_reason="end_turn", usage=None)

        class _Models:
            @staticmethod
            def list():
                return [SimpleNamespace(id="claude-opus-4-8")]

        self.messages = _Messages()
        self.models = _Models()


def _facade(openai_client=None):
    anthropic = FakeAnthropic()
    return anthropic, client_mod.Facade(
        anthropic, oa.OpenAIAdapter(openai_client or FakeOpenAI()))


# ---------------- 1: an Anthropic-shaped request survives A -> O -> A ----------------

def test_a_turn_round_trips_with_content_tool_calls_and_stop_reason():
    fake = FakeOpenAI(_resp([
        {"type": "message", "content": [{"type": "output_text", "text": "on it"}]},
        {"type": "function_call", "call_id": "call_1", "name": "read_file",
         "arguments": '{"path": "a.py"}'},
    ]))
    msg = oa.OpenAIAdapter(fake).messages.create(
        model="gpt-5.6", max_tokens=4096,
        system=[{"type": "text", "text": "be terse",
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "read a.py"}],
        tools=[{"name": "read_file", "description": "read it",
                "input_schema": {"type": "object", "properties": {}}}])

    req = fake.requests[0]
    assert req["instructions"] == "be terse"          # blocks flattened, cache_control gone
    assert req["max_output_tokens"] == 4096
    assert req["tools"] == [{"type": "function", "name": "read_file",
                             "description": "read it",
                             "parameters": {"type": "object", "properties": {}}}]
    assert req["input"] == [{"role": "user",
                             "content": [{"type": "input_text", "text": "read a.py"}]}]

    kinds = [(b.type, getattr(b, "text", "") or b.name) for b in msg.content]
    assert kinds == [("text", "on it"), ("tool_use", "read_file")]
    assert msg.content[1].id == "call_1"
    assert msg.content[1].input == {"path": "a.py"}
    assert msg.stop_reason == "tool_use"   # a function call outranks status=completed


def test_a_tool_result_becomes_a_function_call_output_keyed_by_call_id():
    fake = FakeOpenAI(_resp([]))
    oa.OpenAIAdapter(fake).messages.create(
        model="gpt-5.6", max_tokens=10, system="",
        messages=[
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_9", "name": "read_file", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_9", "content": "file body"}]},
        ], tools=[])
    items = fake.requests[0]["input"]
    assert items[0]["type"] == "function_call" and items[0]["call_id"] == "call_9"
    assert items[1] == {"type": "function_call_output", "call_id": "call_9",
                        "output": "file body"}


def test_hitting_the_output_ceiling_maps_to_max_tokens():
    fake = FakeOpenAI(_resp([], status="incomplete",
                            incomplete=SimpleNamespace(reason="max_output_tokens")))
    msg = oa.OpenAIAdapter(fake).messages.create(
        model="gpt-5.6", max_tokens=10, system="", messages=[], tools=[])
    assert msg.stop_reason == "max_tokens"  # agent.py retries smaller on exactly this


# ---------------- 2: usage mapping, proved past the names that align ----------------

def test_cached_tokens_are_mapped_and_not_double_counted():
    """input_tokens/output_tokens align by NAME across both providers, so the ledger
    populates and Ledger.blind stays False whether or not the rest was mapped. Neither
    is evidence. What matters: OpenAI's input_tokens INCLUDES cached tokens and
    Anthropic's excludes them, so a straight name-for-name map would count every cached
    token twice in context_tokens."""
    fake = FakeOpenAI(_resp([], usage=SimpleNamespace(
        input_tokens=10_000, output_tokens=500,
        input_tokens_details=SimpleNamespace(cached_tokens=8_000),
        output_tokens_details=SimpleNamespace(reasoning_tokens=400))))
    msg = oa.OpenAIAdapter(fake).messages.create(
        model="gpt-5.6", max_tokens=10, system="", messages=[], tools=[])

    u = usage_mod.from_response(msg)
    assert u.cache_read_input_tokens == 8_000
    assert u.input_tokens == 2_000, "cached tokens must be subtracted, not added on top"
    assert u.context_tokens == 10_000, "context must equal what the provider reported"
    assert u.cache_creation_input_tokens == 0  # no billed write on this provider


def test_a_ledger_fed_from_this_provider_is_not_blind():
    fake = FakeOpenAI(_resp([], usage=SimpleNamespace(
        input_tokens=100, output_tokens=10,
        input_tokens_details=None, output_tokens_details=None)))
    msg = oa.OpenAIAdapter(fake).messages.create(
        model="gpt-5.6", max_tokens=10, system="", messages=[], tools=[])
    led = usage_mod.Ledger()
    led.add(usage_mod.from_response(msg), "gpt-5.6")
    assert not led.blind


# ---------------- 3: reasoning tokens get their own line ----------------

def test_reasoning_tokens_are_reported_separately_and_never_added_to_output():
    """At high effort these dominate the bill. They are already inside output_tokens, so
    the line exists to explain the size, not to increase it."""
    led = usage_mod.Ledger()
    led.add(usage_mod.Usage(input_tokens=100, output_tokens=1_000,
                            reasoning_tokens=900), "gpt-5.6")
    out = usage_mod.report(led, 200_000, "gpt-5.6")
    assert "reasoning" in out and "900" in out
    assert led.output_tokens == 1_000, "reasoning must not inflate the output total"


# ---------------- 4: effort is translated, never dropped ----------------

def test_effort_reaches_the_request_as_reasoning_effort():
    """The setting the user cares most about. Dropping it would be silent."""
    fake = FakeOpenAI(_resp([]))
    oa.OpenAIAdapter(fake).messages.create(
        model="gpt-5.6", max_tokens=10, system="", messages=[], tools=[],
        output_config={"effort": "high"},
        thinking={"type": "adaptive", "display": "omitted"})
    req = fake.requests[0]
    assert req["reasoning"]["effort"] == "high"
    assert "thinking" not in req  # Anthropic's block has no counterpart; dropped, not faked


def test_the_whole_thinking_extras_path_survives_create_turn():
    """End to end through client.create_turn, which is what actually builds extras."""
    fake = FakeOpenAI(_resp([]))
    client_mod.create_turn(oa.OpenAIAdapter(fake), model="gpt-5.6", max_tokens=10,
                           system="", messages=[], tools=[], thinking=True, effort="high")
    assert fake.requests[0]["reasoning"]["effort"] == "high"


# ---------------- 5: stateless multi-turn via encrypted reasoning ----------------

def test_a_reasoning_item_survives_the_round_trip_and_replays():
    fake = FakeOpenAI(
        _resp([{"type": "reasoning", "id": "rs_1", "encrypted_content": "SEALED",
                "summary": [{"type": "summary_text", "text": "weighing options"}]},
               {"type": "message", "content": [{"type": "output_text", "text": "done"}]}]),
        _resp([]))
    adapter = oa.OpenAIAdapter(fake)
    msg = adapter.messages.create(model="gpt-5.6", max_tokens=10, system="",
                                  messages=[], tools=[], output_config={"effort": "high"})

    blocks = client_mod.message_to_blocks(msg)
    assert blocks[0] == {"type": "thinking", "thinking": "weighing options",
                         "signature": "SEALED", "id": "rs_1"}

    adapter.messages.create(model="gpt-5.6", max_tokens=10, system="", tools=[],
                            messages=[{"role": "assistant", "content": blocks}],
                            output_config={"effort": "high"})
    req = fake.requests[1]
    assert {"type": "reasoning", "summary": [], "encrypted_content": "SEALED",
            "id": "rs_1"} in req["input"]
    assert req["store"] is False
    assert req["reasoning"]["context"] == "all_turns"


def test_an_unreplayable_reasoning_item_is_dropped_not_sent_broken():
    """No encrypted_content means it cannot be replayed — the same rule luban already
    applies to unsigned Anthropic thinking."""
    fake = FakeOpenAI(_resp([{"type": "reasoning", "id": "rs_2", "summary": []}]))
    msg = oa.OpenAIAdapter(fake).messages.create(
        model="gpt-5.6", max_tokens=10, system="", messages=[], tools=[])
    assert msg.content == []


# ---------------- 6: routing ----------------

def test_an_anthropic_model_reaches_the_original_client_object_untouched():
    """Pass-through must be IDENTITY. Anything else risks the caching, thinking and beta
    behaviour that already works — and a regression there fails this work outright."""
    anthropic, facade = _facade()
    assert facade.client_for("claude-opus-4-8") is anthropic
    facade.messages.create(model="claude-opus-4-8", max_tokens=10, system="s",
                           messages=[], tools=[])
    assert anthropic.calls[0]["model"] == "claude-opus-4-8"


def test_an_openai_model_reaches_the_adapter_and_never_the_anthropic_client():
    fake = FakeOpenAI(_resp([]))
    anthropic, facade = _facade(fake)
    facade.messages.create(model="gpt-5.6", max_tokens=10, system="s",
                           messages=[], tools=[])
    assert len(fake.requests) == 1
    assert anthropic.calls == []


def test_model_ids_are_routed_by_prefix():
    for m in ("gpt-5.6", "gpt-4o", "o3-mini", "chatgpt-4o-latest"):
        assert client_mod.provider_for(m) == "openai"
    # Unrecognised ids go to the primary client — where they went before this existed.
    for m in ("claude-opus-4-8", "", "some-internal-alias"):
        assert client_mod.provider_for(m) == "anthropic"


def test_model_offers_the_catalogue_of_every_backend():
    _anthropic, facade = _facade()
    assert client_mod.list_models(facade) == ["claude-opus-4-8", "gpt-5.6"]


def test_one_backend_failing_to_list_does_not_hide_the_other():
    anthropic, facade = _facade()
    anthropic.models.list = lambda: (_ for _ in ()).throw(RuntimeError("no catalogue"))
    assert client_mod.list_models(facade) == ["gpt-5.6"]


def test_a_single_provider_setup_gets_no_facade_at_all(tmp_path, monkeypatch):
    """No build_openai_client means nothing about the existing path changes."""
    f = tmp_path / "client_local.py"
    f.write_text("def build_client():\n    return 'THE CLIENT'\n")
    monkeypatch.setenv("LUBAN_CLIENT_LOCAL", str(f))
    assert client_mod.get_client() == "THE CLIENT"


def test_a_second_builder_produces_a_routing_facade(tmp_path, monkeypatch):
    f = tmp_path / "client_local.py"
    f.write_text("def build_client():\n    return 'ANTHROPIC'\n"
                 "def build_openai_client():\n    return 'OPENAI'\n")
    monkeypatch.setenv("LUBAN_CLIENT_LOCAL", str(f))
    got = client_mod.get_client()
    assert isinstance(got, client_mod.Facade)
    assert got.client_for("claude-opus-4-8") == "ANTHROPIC"
    assert got.client_for("gpt-5.6").raw == "OPENAI"


# ---------------- 7: probes are per provider ----------------

def test_a_probe_set_on_one_provider_is_not_inherited_by_the_other():
    """Process-global tri-states were fine while one process meant one backend. They are
    contamination the moment /model can cross providers."""
    client_mod.probes("claude-opus-4-8")["extras"] = True
    client_mod.probes("claude-opus-4-8")["ctx_mgmt"] = True
    assert client_mod.probes("gpt-5.6")["extras"] is None
    assert client_mod.probes("gpt-5.6")["ctx_mgmt"] is None


def test_context_editing_is_disabled_for_openai_without_touching_anthropic():
    """The beta surface is Anthropic-only. The fallback must be scoped to the provider
    that lacks it, or one gpt turn would silently disable context editing for claude."""
    fake = FakeOpenAI(_resp([]))
    _anthropic, facade = _facade(fake)
    msg = client_mod.create_turn(facade, model="gpt-5.6", max_tokens=10, system="",
                                 messages=[], tools=[],
                                 ctx_mgmt=client_mod.context_management(150_000))
    assert msg is not None                                    # the turn still happened
    assert client_mod.probes("gpt-5.6")["ctx_mgmt"] is False
    assert client_mod.probes("claude-opus-4-8")["ctx_mgmt"] is None


# ---------------- 8: cost is attributed per model ----------------

def test_a_mid_session_switch_prices_each_model_at_its_own_rate():
    """Pricing the cumulative ledger at whichever model is current blends dollars that
    were never the same dollars — and here the two rates differ in both directions."""
    led = usage_mod.Ledger()
    led.add(usage_mod.Usage(input_tokens=1_000_000, output_tokens=0), "claude-opus-4-8")
    led.add(usage_mod.Usage(input_tokens=0, output_tokens=1_000_000), "gpt-5.6")

    claude_in = usage_mod.rates("claude-opus-4-8")["input_cost_per_token"] * 1_000_000
    gpt_out = usage_mod.rates("gpt-5.6")["output_cost_per_token"] * 1_000_000
    assert usage_mod.cost(led, "gpt-5.6") == pytest.approx(claude_in + gpt_out)
    # and the answer does not depend on which model happens to be current
    assert usage_mod.cost(led, "claude-opus-4-8") == pytest.approx(claude_in + gpt_out)

    out = usage_mod.report(led, 200_000, "gpt-5.6")
    assert "claude-opus-4-8" in out and "gpt-5.6" in out


def test_an_unpriced_model_in_the_mix_yields_no_total_rather_than_a_low_one():
    led = usage_mod.Ledger()
    led.add(usage_mod.Usage(input_tokens=1_000), "claude-opus-4-8")
    led.add(usage_mod.Usage(input_tokens=1_000), "some-internal-alias")
    assert usage_mod.cost(led, "claude-opus-4-8") is None
    assert usage_mod.unpriced(led) == ["some-internal-alias"]
    assert "some-internal-alias" in usage_mod.report(led, 200_000, "claude-opus-4-8")


def test_every_call_is_attributed_to_the_model_that_made_it():
    src = Path("luban/cli.py").read_text(encoding="utf-8")
    body = src[src.index("def build_agent_config"):src.index("def flush_memory")]
    assert "ledger.add(u, session.model)" in body


# ---------------- 9: an estimate must announce itself ----------------

def test_context_says_the_figure_is_an_estimate_on_a_backend_without_count_tokens(
        mem, tmp_path):
    fake = FakeOpenAI()
    _anthropic, facade = _facade(fake)
    out = cli.context_report(
        cli.Session(model="gpt-5.6", max_tokens=100, auto=True, stream=False, messages=[]),
        config_mod.Config(platform="mac"), tmp_path, client=facade)
    assert "ESTIMATE" in out and "no count_tokens" in out
    # and it must not claim an eligibility verdict for a caching mechanism that is not
    # running on this provider
    assert "ELIGIBLE" not in out
    assert "caching: automatic on this provider" in out


def test_count_tokens_is_refused_rather_than_guessed():
    with pytest.raises(NotImplementedError):
        oa.OpenAIAdapter(FakeOpenAI()).messages.count_tokens(model="gpt-5.6", system="s")
    assert cli.count_tokens(oa.OpenAIAdapter(FakeOpenAI()), "gpt-5.6", "s") is None


# ---------------- the whole agent loop, end to end, over the adapter ----------------

def test_a_full_tool_using_turn_runs_against_the_adapter(tmp_path):
    """Unit round-trips prove the translation; this proves the loop actually closes —
    call, dispatch, result, follow-up — with the reasoning item replayed throughout."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    fake = FakeOpenAI(
        _resp([{"type": "reasoning", "id": "rs_1", "encrypted_content": "SEALED",
                "summary": []},
               {"type": "function_call", "call_id": "c1", "name": "read_file",
                "arguments": json.dumps({"path": "a.txt"})}],
              usage=SimpleNamespace(
                  input_tokens=1_000, output_tokens=50,
                  input_tokens_details=SimpleNamespace(cached_tokens=600),
                  output_tokens_details=SimpleNamespace(reasoning_tokens=40))),
        _resp([{"type": "message", "content": [{"type": "output_text",
                                                "text": "it says hello"}]}]))
    _anthropic, facade = _facade(fake)

    led = usage_mod.Ledger()
    said = []
    cfg = cli.agent.AgentConfig(
        "gpt-5.6", 4096, stream=False, platform="mac", cache_prompt=True,
        tools=cli.tools.active_tools(False), thinking=True, effort="high",
        on_usage=lambda u: led.add(u, "gpt-5.6"))
    ctx = cli.tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                                render_diff=lambda *a: None,
                                render_command=lambda c: None)
    out = cli.agent.run_turn(facade, cfg, [{"role": "user", "content": "read a.txt"}],
                             ctx, said.append)

    assert "".join(said) == "it says hello"
    assert any(m["role"] == "user" and isinstance(m["content"], list)
               and m["content"][0].get("type") == "tool_result" for m in out)
    # the reasoning item was replayed on the follow-up call, not dropped
    assert any(i.get("type") == "reasoning" for i in fake.requests[1]["input"])
    assert led.calls == 2 and led.cache_read_input_tokens == 600
    assert led.reasoning_tokens == 40
    assert usage_mod.cost(led, "gpt-5.6") is not None


# ---------------- the adapter belongs in core, not in client_local.py ----------------

def test_the_wire_translation_is_a_tested_core_module():
    """A translator in the gitignored client_local.py would get no tests, no review, no
    leak-guard coverage and no distribution — every colleague would reinvent it."""
    assert Path("luban/providers/openai.py").exists()
    example = Path("luban/client_local.example.py").read_text(encoding="utf-8")
    assert "build_openai_client" in example
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "luban.providers" in pyproject, "the adapter must ship in the wheel"


def test_the_adapter_imports_no_provider_sdk():
    """Zero dependencies is an invariant: the client arrives from client_local.py and is
    called duck-typed, exactly as the Anthropic one is."""
    src = Path("luban/providers/openai.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        assert not line.startswith(("import openai", "from openai")), line
