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
)


@dataclass
class AgentConfig:
    model: str
    max_tokens: int
    stream: bool


def _run_model_turn(client, config, messages, on_text):
    if config.stream:
        return client_mod.stream_turn(
            client, model=config.model, max_tokens=config.max_tokens,
            system=SYSTEM_PROMPT, messages=messages, tools=tools_mod.TOOLS,
            on_text=on_text,
        )
    msg = client_mod.create_turn(
        client, model=config.model, max_tokens=config.max_tokens,
        system=SYSTEM_PROMPT, messages=messages, tools=tools_mod.TOOLS,
    )
    for b in msg.content:
        if b.type == "text":
            on_text(b.text)
    return msg


def run_turn(client, config: AgentConfig, messages: list[dict], ctx, on_text) -> list[dict]:
    messages = list(messages)
    while True:
        msg = _run_model_turn(client, config, messages, on_text)
        messages.append({"role": "assistant", "content": client_mod.message_to_blocks(msg)})
        if msg.stop_reason != "tool_use":
            return messages
        results = []
        for block in msg.content:
            if block.type != "tool_use":
                continue
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
