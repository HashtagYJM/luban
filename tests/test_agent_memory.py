from luban import agent


def test_memory_in_prompt():
    p = agent.system_prompt_for("mac", memory="Use output/raw_data for downloads.")
    assert "Project instructions (from the project's memory file):" in p
    assert "output/raw_data" in p


def test_no_memory_no_block():
    assert "Project instructions" not in agent.system_prompt_for("mac")


def test_ordering_platform_memory_skills():
    skills = [{"name": "s", "description": "d", "scope": "global"}]
    p = agent.system_prompt_for("windows", skills, memory="MEMBLOCK")
    assert p.index("cmd.exe") < p.index("MEMBLOCK") < p.index("Skills available")


def test_agent_config_memory_default():
    cfg = agent.AgentConfig(model="m", max_tokens=1, stream=False)
    assert cfg.memory == ""
