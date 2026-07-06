from __future__ import annotations

from dataclasses import dataclass

from luban import client as client_mod
from luban import tools as tools_mod

SYSTEM_PROMPT = (
    "You are Luban, a terminal coding agent operating inside the user's project "
    "directory. Use the tools to search and read files before editing. Prefer "
    "edit_file over rewriting whole files; keep changes minimal and targeted. "
    "Briefly say what you are about to do before calling mutating tools. All paths "
    "are relative to the project root."
    " The user drives the session with slash-commands you can point them to when "
    "relevant: /compact (summarize a long conversation and keep going), /reflect "
    "(tidy your long-term memory), /model (show or switch the model), and /sessions "
    "(list saved sessions)."
)

_PLATFORM_LINE = {
    "windows": "The user is on Windows: use cmd.exe-compatible shell commands "
    "(e.g. `dir`, `type`, `del`) and Windows-style paths in run_command.",
    "mac": "The user is on macOS: use POSIX shell commands in run_command.",
    "linux": "The user is on Linux: use POSIX shell commands in run_command.",
}


def system_prompt_for(platform: str, skills: list[dict] | None = None, memory: str = "", global_memory: str = "") -> str:
    prompt = SYSTEM_PROMPT
    line = _PLATFORM_LINE.get(platform)
    if line:
        prompt = f"{prompt}\n\n{line}"
    if global_memory:
        prompt = f"{prompt}\n\n{global_memory}"
    if memory:
        prompt = f"{prompt}\n\nProject instructions (from the project's memory file):\n{memory}"
    if skills:
        catalog = "\n".join(
            f"- {s['name']}: {s['description']}"
            + (" [project]" if s["scope"] == "project" else "")
            for s in skills
        )
        prompt = (
            f"{prompt}\n\nSkills available (load full instructions with the "
            f"load_skill tool when one is relevant to the task):\n{catalog}"
        )
    return prompt


@dataclass
class AgentConfig:
    model: str
    max_tokens: int
    stream: bool
    platform: str = ""
    skills: list | None = None
    memory: str = ""
    global_memory: str = ""
    tools: list | None = None


def _run_model_turn(client, config, messages, on_text, on_thinking):
    system = system_prompt_for(config.platform, config.skills, config.memory, config.global_memory)
    tool_schemas = config.tools if config.tools is not None else tools_mod.TOOLS
    if config.stream:
        return client_mod.stream_turn(
            client, model=config.model, max_tokens=config.max_tokens,
            system=system, messages=messages, tools=tool_schemas,
            on_text=on_text, on_thinking=on_thinking,
        )
    msg = client_mod.create_turn(
        client, model=config.model, max_tokens=config.max_tokens,
        system=system, messages=messages, tools=tool_schemas,
    )
    for b in msg.content:
        if b.type == "text":
            on_text(b.text)
        elif b.type == "thinking" and on_thinking is not None:
            on_thinking(b.thinking)
    return msg


def run_turn(client, config: AgentConfig, messages: list[dict], ctx, on_text, on_thinking=None) -> list[dict]:
    messages = list(messages)
    while True:
        msg = _run_model_turn(client, config, messages, on_text, on_thinking)
        messages.append({"role": "assistant", "content": client_mod.message_to_blocks(msg)})
        if msg.stop_reason != "tool_use":
            return messages
        offered = {
            t["name"] for t in (config.tools if config.tools is not None else tools_mod.TOOLS)
        }
        results = []
        for block in msg.content:
            if block.type != "tool_use":
                continue
            if block.name not in offered:
                out = tools_mod.ToolResult(
                    f"Tool not available in this turn: {block.name}", is_error=True
                )
            else:
                out = tools_mod.run_tool(block.name, block.input, ctx)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": out.content,
                "is_error": out.is_error,
            })
        if not results:
            # stop_reason was tool_use but no tool_use blocks were present;
            # returning avoids sending an empty tool_result message in a loop.
            return messages
        messages.append({"role": "user", "content": results})
