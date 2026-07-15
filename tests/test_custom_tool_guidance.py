"""E25: a custom tool can contribute usage GUIDANCE to the system prompt, not just its
per-tool `description`. The API `tools` param carries descriptions; for a growing suite
of custom + MCP-bridge tools, the model also needs orchestration hints (when to reach
for each, how they combine) — which have nowhere to live today."""
import pytest

from luban import agent, custom_tools, tools


def _spec(name, guidance=None):
    entry = {
        "name": name,
        "description": f"does {name}",
        "input_schema": {"type": "object", "properties": {}},
        "handler": lambda inp, project_root: "ok",
    }
    if guidance is not None:
        entry["guidance"] = guidance
    return entry


@pytest.fixture(autouse=True)
def _clean():
    tools.reset_custom()
    yield
    tools.reset_custom()


# ---------------- validation ----------------

def test_guidance_is_optional():
    assert custom_tools._valid(_spec("a"), 0)


def test_guidance_string_is_accepted():
    assert custom_tools._valid(_spec("a", "call me first"), 0)


def test_non_string_guidance_is_rejected(capsys):
    assert not custom_tools._valid(_spec("a", guidance=123), 0)
    assert "guidance must be a string" in capsys.readouterr().err


# ---------------- registration + retrieval ----------------

def test_guidance_is_registered_and_retrievable():
    tools.register_custom([_spec("query_sql", "Use before any analysis tool.")])
    assert tools.custom_guidance() == [("query_sql", "Use before any analysis tool.")]


def test_a_tool_without_guidance_contributes_none():
    tools.register_custom([_spec("plain")])
    assert tools.custom_guidance() == []


def test_guidance_follows_registration_order():
    tools.register_custom([_spec("first", "1"), _spec("second", "2")])
    assert tools.custom_guidance() == [("first", "1"), ("second", "2")]


def test_reset_clears_guidance():
    tools.register_custom([_spec("x", "hint")])
    tools.reset_custom()
    assert tools.custom_guidance() == []


def test_description_still_reaches_the_tool_schema():
    """Guidance is ADDITIVE — it must not replace the description on the tool schema."""
    tools.register_custom([_spec("q", "orchestration hint")])
    schema = next(t for t in tools.TOOLS if t["name"] == "q")
    assert schema["description"] == "does q"
    assert "guidance" not in schema  # guidance is not a tool-schema field


# ---------------- it reaches the system prompt ----------------

def test_guidance_is_injected_into_the_system_prompt():
    prompt = agent.system_prompt_for(
        "mac", tool_guidance=[("mcp_call_tool", "Call mcp_list_tools first to discover.")])
    assert "Custom tool usage guidance:" in prompt
    assert "mcp_call_tool: Call mcp_list_tools first to discover." in prompt


def test_no_guidance_adds_no_section():
    plain = agent.system_prompt_for("mac")
    assert "Custom tool usage guidance" not in plain


def test_end_to_end_registered_tool_guidance_shows_in_prompt():
    tools.register_custom([_spec("consult_role", "One role per call; pass role_id.")])
    prompt = agent.system_prompt_for("mac", tool_guidance=tools.custom_guidance())
    assert "consult_role: One role per call; pass role_id." in prompt
