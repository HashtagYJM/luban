from luban import agent


_SKILLS = [
    {"name": "conv", "description": "project conventions", "scope": "project"},
    {"name": "style", "description": "my style rules", "scope": "global"},
]


def test_catalog_appended_when_skills_present():
    prompt = agent.system_prompt_for("mac", _SKILLS)
    assert "Skills available" in prompt
    assert "load_skill" in prompt
    assert "- conv: project conventions [project]" in prompt
    assert "- style: my style rules" in prompt


def test_no_catalog_without_skills():
    assert "Skills available" not in agent.system_prompt_for("mac")
    assert "Skills available" not in agent.system_prompt_for("mac", [])


def test_platform_line_still_present_with_skills():
    prompt = agent.system_prompt_for("windows", _SKILLS)
    assert "cmd.exe" in prompt
    assert "Skills available" in prompt


def test_agent_config_default_skills_none():
    cfg = agent.AgentConfig(model="m", max_tokens=10, stream=False)
    assert cfg.skills is None
