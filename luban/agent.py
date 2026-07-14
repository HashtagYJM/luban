from __future__ import annotations

from dataclasses import dataclass

from luban import client as client_mod
from luban import tools as tools_mod

SYSTEM_PROMPT = (
    "You are Luban, a terminal coding agent operating inside the user's project "
    "directory. Use the tools to search and read files before editing. Prefer "
    "edit_file over rewriting whole files; keep changes minimal and targeted. "
    "Briefly say what you are about to do before calling mutating tools — in the SAME "
    "turn as the tool call, never as a turn of its own. NEVER end a turn announcing "
    "work you have not done ('I'll write the file now', 'let me update X'): a turn that "
    "ends with no tool call yields control back to the user, so the announced work "
    "simply never happens and the session stalls. If you say you are about to act, the "
    "tool call must be in that same turn. All paths are relative to the project root."
    " The user drives the session with slash-commands you can point them to when "
    "relevant: /compact (summarize a long conversation and keep going), /reflect "
    "(tidy your long-term memory), /model (show or switch the model), /thinking "
    "(toggle extended thinking), /effort (low..max reasoning depth), /verbose "
    "(show or hide the reasoning text), /config (show effective settings), /sessions "
    "(list saved sessions — `/sessions all` spans folders), /resume (reopen this "
    "project's last session; `/resume <number|id|name>` picks a specific one), /new "
    "(save the current thread and start another one, optionally `/new <title>`), "
    "/title (name the current session so it's findable later), and /retry (resend a "
    "prompt whose turn the network killed — the typed prompt is not lost)."
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
    web_search: bool = False
    web_search_tool_type: str = "web_search_20250305"
    thinking: bool = False
    effort: str = "medium"
    thinking_verbose: bool = False  # stream the reasoning (grey text) vs think silently


def _run_model_turn(client, config, messages, on_text, on_thinking, on_retry=None):
    system = system_prompt_for(config.platform, config.skills, config.memory, config.global_memory)
    tool_schemas = config.tools if config.tools is not None else tools_mod.TOOLS
    if config.web_search:
        # Server-side tool: the API runs the search and returns results inline; luban
        # never dispatches it (run_turn only handles client tool_use blocks). Append
        # rather than mutate the shared TOOLS list.
        tool_schemas = [
            *tool_schemas,
            {"type": config.web_search_tool_type, "name": "web_search"},
        ]
    if config.stream:
        return client_mod.stream_turn(
            client, model=config.model, max_tokens=config.max_tokens,
            system=system, messages=messages, tools=tool_schemas,
            on_text=on_text, on_thinking=on_thinking,
            thinking=config.thinking, effort=config.effort,
            verbose=config.thinking_verbose, on_retry=on_retry,
        )
    msg = client_mod.create_turn(
        client, model=config.model, max_tokens=config.max_tokens,
        system=system, messages=messages, tools=tool_schemas,
        thinking=config.thinking, effort=config.effort,
        verbose=config.thinking_verbose, on_retry=on_retry,
    )
    for b in msg.content:
        if b.type == "text":
            on_text(b.text)
        elif b.type == "thinking" and on_thinking is not None:
            on_thinking(b.thinking)
    return msg


def sanitize_history(messages: list[dict]) -> list[dict]:
    """Guarantee an API-valid tail: history must never END in an assistant message
    that contains unanswered tool_use blocks.

    The Anthropic API requires every tool_use to be immediately followed by its
    tool_result. A response truncated at max_tokens mid-tool-call (or any path that
    leaves a trailing tool_use) would 400 on the next send — and on resume that
    crash killed the session (E14). This strips trailing unanswered tool_use blocks
    (keeping any text), dropping a message that becomes empty. Pure; returns a new
    list only when it changes something. Enforced at run_turn's returns, on save,
    and on restore (which repairs already-corrupted session files)."""
    if not messages:
        return messages
    out = list(messages)
    while out:
        last = out[-1]
        if last.get("role") != "assistant":
            break
        content = last.get("content")
        if not isinstance(content, list):
            break
        if not any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
            break
        kept = [b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_use")]
        if kept:
            out[-1] = {**last, "content": kept}
            break  # message still valid (text remains), tail is now clean
        out.pop()  # nothing but tool_use — drop the whole message and re-check
    return out


MAX_PAUSE_RESUMES = 8
MAX_TRUNCATION_RETRIES = 2

TRUNCATION_NUDGE = (
    "Your previous turn hit the output token ceiling (max_tokens) before it finished. "
    "It ended mid-tool-call, so THE TOOL CALL WAS DROPPED — nothing ran, nothing was "
    "written. Do not assume it succeeded. Retry the action in a way that fits: split a "
    "large write into parts (write_file, then edit_file to append), or do less per turn."
)


def _has_tool_use(content) -> bool:
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" for b in content
    )


def run_turn(client, config: AgentConfig, messages: list[dict], ctx, on_text,
             on_thinking=None, on_retry=None, on_truncated=None) -> list[dict]:
    messages = list(messages)
    pauses = 0
    truncations = 0
    while True:
        msg = _run_model_turn(client, config, messages, on_text, on_thinking, on_retry)
        messages.append({"role": "assistant", "content": client_mod.message_to_blocks(msg)})
        if msg.stop_reason == "pause_turn":
            # A server tool (web search) hit the API's internal iteration limit.
            # Re-send the same messages (now including this partial assistant turn,
            # with its server_tool_use blocks preserved) so the server resumes — no
            # extra user message. Bounded so a stuck server tool can't loop forever.
            if pauses >= MAX_PAUSE_RESUMES:
                return sanitize_history(messages)
            pauses += 1
            continue
        if msg.stop_reason == "max_tokens" and _has_tool_use(messages[-1]["content"]):
            # The turn was cut off MID-TOOL-CALL. sanitize_history must strip that
            # tool_use (an unanswered one 400s the next send — E14), but stripping it
            # silently is how a write "vanishes": the user sees the model announce the
            # write, no tool runs, no error appears anywhere, and the model itself never
            # learns the call was dropped — so it reports success (E24, and the
            # mechanism behind most of what E23 logged as "announce-and-yield").
            # Tell BOTH: the human, and the model on its next turn.
            messages = sanitize_history(messages)
            if on_truncated is not None:
                on_truncated(config.max_tokens, truncations + 1, MAX_TRUNCATION_RETRIES)
            if truncations >= MAX_TRUNCATION_RETRIES:
                return messages
            truncations += 1
            messages.append({"role": "user", "content": TRUNCATION_NUDGE})
            continue
        if msg.stop_reason != "tool_use":
            # Any other non-tool_use stop can still carry a trailing tool_use — never
            # return it unanswered, or the next send/resume 400s.
            return sanitize_history(messages)
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
            return sanitize_history(messages)
        messages.append({"role": "user", "content": results})
