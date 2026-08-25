"""E40: an empty completion must not return as an answer.

A nested completion that comes back with no body — a refusal, a gateway failure, a
completion with no content — is returned to the caller as an ordinary result, so a
critic or research stage silently degrades to no stage at all and nothing says so. The
caller cannot tell the three apart; it can at least be told that it cannot.
"""
from pathlib import Path

from luban import tools


def _ctx(tmp_path, subagent=None):
    return tools.ToolContext(
        project_root=Path(tmp_path), confirm=lambda p: True,
        render_diff=lambda p, o, n: None, render_command=lambda c: None,
        subagent=subagent)


def test_a_subagent_that_returns_nothing_is_an_error_not_an_answer(tmp_path):
    out = tools.run_tool("spawn_subagent", {"task": "review this"},
                         _ctx(tmp_path, subagent=lambda task: ""))
    assert out.is_error
    assert "EMPTY COMPLETION" in out.content


def test_whitespace_is_not_an_answer_either(tmp_path):
    out = tools.run_tool("spawn_subagent", {"task": "review this"},
                         _ctx(tmp_path, subagent=lambda task: "   \n\n  "))
    assert out.is_error


def test_a_subagent_that_answers_is_untouched(tmp_path):
    out = tools.run_tool("spawn_subagent", {"task": "review this"},
                         _ctx(tmp_path, subagent=lambda task: "found three things"))
    assert not out.is_error
    assert out.content == "found three things"


def _spec(handler, name="consult_role"):
    return {"name": name, "description": "d", "input_schema": {"type": "object"},
            "handler": handler, "read_only": True}


def test_a_custom_tool_that_returns_nothing_says_so(tmp_path):
    tools.reset_custom()
    tools.register_custom([_spec(lambda inp, root: "")])
    try:
        out = tools.run_tool("consult_role", {"role_id": "risk_analyst"}, _ctx(tmp_path))
    finally:
        tools.reset_custom()
    assert out.is_error
    assert "empty result" in out.content
    assert "consult_role" in out.content


def test_a_custom_tool_with_output_is_untouched(tmp_path):
    tools.reset_custom()
    tools.register_custom([_spec(lambda inp, root: "the risk is X")])
    try:
        out = tools.run_tool("consult_role", {"role_id": "risk_analyst"}, _ctx(tmp_path))
    finally:
        tools.reset_custom()
    assert not out.is_error
    assert out.content == "the risk is X"
