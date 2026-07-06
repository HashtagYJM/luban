from luban import agent


def test_system_prompt_lists_user_commands():
    prompt = agent.system_prompt_for("mac")
    for cmd in ("/compact", "/reflect", "/model", "/sessions"):
        assert cmd in prompt


def test_command_line_present_in_base_constant():
    assert "/compact" in agent.SYSTEM_PROMPT
