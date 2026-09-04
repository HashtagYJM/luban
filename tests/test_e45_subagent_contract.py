"""E45 — the subagent inherited the main agent's prose while its tools were cut.

The nested run was given `SYSTEM_PROMPT` verbatim: a prompt that prefers `edit_file`,
tells the model to announce mutating tool calls, and lists the slash-commands "the user"
drives the session with. A subagent has no write tools, no `run_command` and no user. It
was also handed `load_skill` (it is in READ_ONLY_TOOLS) with no skill catalog in its
prompt, while that tool's own description says the catalog is there — so the tool could
never fire and the subagent could not know what it was missing.

These tests state the invariant: the subagent's prompt describes the subagent's job, and
every tool it is offered is one it can actually use.
"""
from pathlib import Path

from luban import agent, tools


def test_subagent_prompt_does_not_promise_tools_it_lacks():
    prompt = agent.SUBAGENT_SYSTEM_PROMPT
    for absent in ("edit_file", "write_file", "run_command"):
        assert absent not in prompt, f"the subagent has no {absent}"


def test_subagent_prompt_does_not_point_at_slash_commands():
    """There is no human reading the subagent's turn — its answer goes to another agent."""
    assert "/compact" not in agent.SUBAGENT_SYSTEM_PROMPT
    assert "/model" not in agent.SUBAGENT_SYSTEM_PROMPT


def test_subagent_prompt_says_what_the_answer_is_for():
    text = agent.SUBAGENT_SYSTEM_PROMPT.lower()
    assert "final" in text or "answer" in text


def test_a_subagent_config_carries_the_skill_catalog(tmp_path):
    """load_skill is offered to a subagent, so the catalog it matches against must be
    in the prompt. The alternative — dropping the tool — would make skill behaviour
    untestable through subagents, which is the cheapest place to test it."""
    skills = [{"name": "quant-research", "description": "how to run a study", "scope": "global"}]
    prompt = agent.system_prompt_for("mac", skills, subagent=True)
    assert "quant-research" in prompt
    assert "load_skill" in prompt


def test_subagent_prompt_is_not_the_main_prompt():
    assert agent.SUBAGENT_SYSTEM_PROMPT != agent.SYSTEM_PROMPT


def test_load_skill_is_still_offered_to_a_read_only_run():
    assert "load_skill" in tools.READ_ONLY_TOOLS


def test_subagent_context_enforces_its_tool_set_at_dispatch(tmp_path):
    """Withholding a tool from the schema is a request; `only` is the control.

    The nested run's contract is 'read-only'. It inherits no conversation, but the
    backend need not read the schema at all — so the restriction has to live at the one
    choke point every call passes through.
    """
    ctx = tools.ToolContext(
        project_root=tmp_path,
        confirm=lambda p: False,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
        only=frozenset(tools.READ_ONLY_TOOLS),
    )
    (tmp_path / "f.py").write_text("x\n")
    out = tools.run_tool("write_file", {"path": "f.py", "content": "y\n"}, ctx)
    assert out.is_error
    assert (tmp_path / "f.py").read_text() == "x\n"
