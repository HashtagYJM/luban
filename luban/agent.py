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
    "(show or hide the reasoning text), /config (show effective settings), /context "
    "(what is loaded into the prompt every turn, and its token cost), /sessions "
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


def system_blocks(platform: str, skills: list[dict] | None = None, memory: str = "",
                  global_memory: str = "",
                  tool_guidance: list[tuple[str, str]] | None = None,
                  global_volatile: str = "") -> tuple[str, str]:
    """The system prompt split into (stable, volatile), in prompt order.

    Prompt caching is a PREFIX match, so anything that changes invalidates every byte
    after it. Everything luban does NOT rewrite mid-session goes in `stable` (and gets
    the cache breakpoint); the fact index and journal — which luban rewrites whenever it
    calls remember/journal — go last, where they can't invalidate the rest (P2).
    """
    return (system_prompt_for(platform, skills, memory, global_memory, tool_guidance),
            global_volatile)


def system_prompt_for(platform: str, skills: list[dict] | None = None, memory: str = "",
                      global_memory: str = "",
                      tool_guidance: list[tuple[str, str]] | None = None) -> str:
    prompt = SYSTEM_PROMPT
    line = _PLATFORM_LINE.get(platform)
    if line:
        prompt = f"{prompt}\n\n{line}"
    if global_memory:
        prompt = f"{prompt}\n\n{global_memory}"
    if memory:
        prompt = f"{prompt}\n\nProject instructions (from the project's memory file):\n{memory}"
    if tool_guidance:
        # Per-tool usage guidance from custom tools: when to reach for each, how, and
        # how they combine — the orchestration layer the tool `description` can't carry
        # for a growing suite (E25). Descriptions still live on the tool schemas.
        block = "\n".join(f"- {name}: {text}" for name, text in tool_guidance)
        prompt = f"{prompt}\n\nCustom tool usage guidance:\n{block}"
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
    global_volatile: str = ""  # index + journal: kept LAST so writes can't bust the cache
    # Callable re-rendering the volatile half per model call, so a mid-turn
    # remember/forget is visible immediately (E28). Falls back to the static string.
    volatile_fn: object = None
    cache_prompt: bool = False  # send the stable prefix as a cacheable block (P2)
    tools: list | None = None
    tool_guidance: list | None = None  # (name, guidance) from custom tools (E25)
    web_search: bool = False
    web_search_tool_type: str = "web_search_20250305"
    thinking: bool = False
    effort: str = "medium"
    thinking_verbose: bool = False  # stream the reasoning (grey text) vs think silently


_BLOCK_SYSTEM_SUPPORTED = None  # tri-state probe: does this backend accept block-form system?


def build_system_param(stable: str, volatile: str, cache: bool):
    """Block-form (cacheable) or a plain concatenated string.

    The cache breakpoint sits on the STABLE block only. Note a short prefix caches
    nothing at all — Opus 4.8 needs ~4,096 tokens — and that failure is SILENT, which
    is why /context reports cache eligibility rather than leaving it to faith.
    """
    if not cache:
        return "\n\n".join(p for p in (stable, volatile) if p)
    blocks = [{"type": "text", "text": stable,
               "cache_control": {"type": "ephemeral"}}]
    if volatile:
        blocks.append({"type": "text", "text": volatile})
    return blocks


def _run_model_turn(client, config, messages, on_text, on_thinking, on_retry=None):
    global _BLOCK_SYSTEM_SUPPORTED
    # Re-render the volatile half EVERY model call. It was captured once per user turn,
    # so within a multi-step turn the index went stale the moment the model called
    # remember/forget — it would then be told a fact it had just saved did not exist,
    # which is exactly the belief that makes it save a duplicate (E28). This is cheap
    # precisely because volatile sits AFTER the cache breakpoint: re-rendering it cannot
    # invalidate the cached stable prefix.
    volatile_now = config.volatile_fn() if config.volatile_fn else config.global_volatile
    stable, volatile = system_blocks(
        config.platform, config.skills, config.memory, config.global_memory,
        config.tool_guidance, volatile_now)
    use_blocks = config.cache_prompt and _BLOCK_SYSTEM_SUPPORTED is not False
    system = build_system_param(stable, volatile, use_blocks)
    tool_schemas = config.tools if config.tools is not None else tools_mod.TOOLS
    if config.web_search:
        # Server-side tool: the API runs the search and returns results inline; luban
        # never dispatches it (run_turn only handles client tool_use blocks). Append
        # rather than mutate the shared TOOLS list.
        tool_schemas = [
            *tool_schemas,
            {"type": config.web_search_tool_type, "name": "web_search"},
        ]
    def _call(system_param):
        if config.stream:
            return client_mod.stream_turn(
                client, model=config.model, max_tokens=config.max_tokens,
                system=system_param, messages=messages, tools=tool_schemas,
                on_text=on_text, on_thinking=on_thinking,
                thinking=config.thinking, effort=config.effort,
                verbose=config.thinking_verbose, on_retry=on_retry,
            )
        return client_mod.create_turn(
            client, model=config.model, max_tokens=config.max_tokens,
            system=system_param, messages=messages, tools=tool_schemas,
            thinking=config.thinking, effort=config.effort,
            verbose=config.thinking_verbose, on_retry=on_retry,
        )

    try:
        msg = _call(system)
    except Exception as exc:
        # Probe once per process: a backend that rejects block-form system (or
        # cache_control) must keep working, exactly as _EXTRAS_SUPPORTED does for
        # thinking/effort. A dropped connection is NOT evidence of rejection.
        if not (use_blocks and _BLOCK_SYSTEM_SUPPORTED is None
                and not client_mod.is_transient(exc)):
            raise
        _BLOCK_SYSTEM_SUPPORTED = False
        msg = _call(build_system_param(stable, volatile, False))
    else:
        if use_blocks:
            _BLOCK_SYSTEM_SUPPORTED = True
    if config.stream:
        return msg
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
