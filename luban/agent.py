from __future__ import annotations

from dataclasses import dataclass

from luban import client as client_mod
from luban import history as history_mod
from luban import usage as usage_mod
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
    after it. Everything luban does NOT rewrite mid-session goes in `stable` and gets the
    first cache breakpoint. The volatile half — the fact index and journal, rewritten
    whenever the model calls remember/journal — is returned separately because it must
    end up behind the SECOND breakpoint, in the message tail; see with_cache_breakpoint.
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
    global_volatile: str = ""  # index + journal: placed behind BOTH cache breakpoints
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
    # Sink for the REAL token counts every response carries. luban used to discard them
    # and estimate at 4 chars/token instead — a 36% undercount that made the /compact
    # nudge fire ~54k tokens late. These cost nothing: they arrive with the response.
    on_usage: object = None
    # Server-side tool-result clearing config (None = off). Derived from
    # warn_tokens by cli; not a knob.
    ctx_mgmt: dict | None = None


def build_system_param(stable: str, volatile: str, cache: bool, model: str = ""):
    """Block-form (cacheable) or a plain concatenated string.

    The cache breakpoint sits on the STABLE block only. Note a short prefix caches
    nothing at all — Opus 4.8 needs ~4,096 tokens — and that failure is SILENT, which
    is why /context reports cache eligibility rather than leaving it to faith.

    `volatile` is normally empty here now: it rides the message tail, behind the second
    breakpoint. It falls back to this position only when the tail cannot take it.
    """
    if not cache:
        return "\n\n".join(p for p in (stable, volatile) if p)
    blocks = [{"type": "text", "text": stable, "cache_control": cache_control(model)}]
    if volatile:
        blocks.append({"type": "text", "text": volatile})
    return blocks



CACHE_TTL = "1h"


def cache_control(model: str) -> dict:
    """A cache entry that survives thinking time.

    luban only ever wrote 5-MINUTE entries, so any pause longer than that — reading a
    long answer, a meeting — killed the prefix and the next call re-wrote the whole thing.
    A session should write about one context's worth of tokens in total, since each one
    enters the cache once; field measurement showed close to an order of magnitude more.

    A 1h write costs 2x input against 1.25x, so it pays for itself the first time it
    prevents one expiry — and an expiry re-writes the ENTIRE prefix, not a delta. Probed
    per provider: a backend that rejects `ttl` degrades to the 5-minute form rather than
    failing the turn.
    """
    if client_mod.probes(model)["cache_ttl"] is False:
        return {"type": "ephemeral"}
    return {"type": "ephemeral", "ttl": CACHE_TTL}


def with_cache_breakpoint(messages: list[dict], model: str = "",
                          volatile: str = "") -> tuple[list[dict], bool]:
    """Mark the END of the conversation as cacheable, and hang volatile context behind it.

    Caching matches an unbroken prefix from the start of the prompt, and luban marked
    exactly ONE spot: the end of the stable system block. So the cached amount was a
    CONSTANT — the size of that block, no matter how long the session ran — while the
    conversation, which is byte-identical on every call, was billed fresh every time. In
    the field the majority of all tokens a session spent fell into that one category:
    repeats.

    Marking the last message each call gives incremental caching: this call writes a cache
    entry covering everything so far, the next call reads it and pays a write only for the
    delta. Anthropic allow four breakpoints; this is the second.

    WHY VOLATILE RIDES ALONG HERE. The fact index and journal used to sit last in the
    SYSTEM prompt, which was genuinely last while there was one breakpoint. The second
    breakpoint moved the finish line: volatile then sat in the MIDDLE of the cached
    conversation prefix, so every remember/journal write invalidated the entire
    conversation and re-wrote ~110,000 tokens — and _HYGIENE asks the model to journal at
    the close of every working block, so luban was doing this to itself. Placed after the
    breakpoint it can change as often as it likes and cost nothing but its own size.

    Returns a NEW list plus whether volatile was actually placed — the caller keeps it in
    the system prompt when it was not, so it can never be silently dropped. The list is new
    because session.messages must never carry request-shaping metadata into the saved
    transcript.
    """
    if not messages:
        return messages, False
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else b for b in content]
    else:
        return messages, False  # nothing markable; leave it alone
    if not isinstance(blocks[-1], dict):
        return messages, False
    blocks[-1]["cache_control"] = cache_control(model)
    # Only a USER message may carry it: appending to an assistant turn would read as the
    # model having said it. The one case that reaches here with an assistant last message
    # is a pause_turn re-send, where volatile stays in the system prompt for that call.
    placed = bool(volatile) and last.get("role") == "user"
    if placed:
        blocks.append({"type": "text", "text": volatile})
    last["content"] = blocks
    out[-1] = last
    return out, placed


def _run_model_turn(client, config, messages, on_text, on_thinking, on_retry=None):
    # Does THIS backend accept block-form system (and so cache_control)? Kept per
    # provider, not per process — see client.probes.
    probe = client_mod.probes(config.model)
    # Re-render the volatile half EVERY model call. It was captured once per user turn,
    # so within a multi-step turn the index went stale the moment the model called
    # remember/forget — it would then be told a fact it had just saved did not exist,
    # which is exactly the belief that makes it save a duplicate (E28). Cheap only because
    # it now sits after BOTH breakpoints; see with_cache_breakpoint.
    volatile_now = config.volatile_fn() if config.volatile_fn else config.global_volatile
    stable, volatile = system_blocks(
        config.platform, config.skills, config.memory, config.global_memory,
        config.tool_guidance, volatile_now)
    use_blocks = config.cache_prompt and probe["block_system"] is not False

    def _shape(cache: bool):
        """(system, messages) for one attempt. Rebuilt per attempt because a degrade
        changes both — the breakpoints live in the messages as well as the system."""
        placed = False
        msgs = messages
        if cache:
            # Second breakpoint, on the conversation, with volatile hung behind it.
            # Without it the cached block is a fixed prefix and every repeated turn is
            # re-billed at full price.
            msgs, placed = with_cache_breakpoint(messages, config.model, volatile)
        # Volatile stays in the system prompt whenever the tail could not take it —
        # caching off, or a pause_turn re-send whose last message is the assistant's.
        # Never dropped.
        return (build_system_param(stable, "" if placed else volatile, cache, config.model),
                msgs)

    tool_schemas = config.tools if config.tools is not None else tools_mod.TOOLS
    if config.web_search:
        # Server-side tool: the API runs the search and returns results inline; luban
        # never dispatches it (run_turn only handles client tool_use blocks). Append
        # rather than mutate the shared TOOLS list.
        tool_schemas = [
            *tool_schemas,
            {"type": config.web_search_tool_type, "name": "web_search"},
        ]
    def _call(shape):
        system_param, msgs = shape
        if config.stream:
            return client_mod.stream_turn(
                client, model=config.model, max_tokens=config.max_tokens,
                system=system_param, messages=msgs, tools=tool_schemas,
                on_text=on_text, on_thinking=on_thinking,
                thinking=config.thinking, effort=config.effort,
                verbose=config.thinking_verbose, on_retry=on_retry,
                ctx_mgmt=config.ctx_mgmt,
            )
        return client_mod.create_turn(
            client, model=config.model, max_tokens=config.max_tokens,
            system=system_param, messages=msgs, tools=tool_schemas,
            thinking=config.thinking, effort=config.effort,
            verbose=config.thinking_verbose, on_retry=on_retry,
            ctx_mgmt=config.ctx_mgmt,
        )

    # Degrade narrowest-first: a backend that rejects the 1h `ttl` may still accept
    # block-form system, and giving up caching entirely over an unknown TTL field would
    # cost far more than the TTL saves. A dropped connection is NOT evidence of rejection.
    def _rejected(exc) -> bool:
        return use_blocks and not client_mod.is_transient(exc)

    try:
        msg = _call(_shape(use_blocks))
    except Exception as exc:
        if not _rejected(exc):
            raise
        if probe["cache_ttl"] is None:
            probe["cache_ttl"] = False
            try:
                msg = _call(_shape(use_blocks))
            except Exception as exc2:
                if not _rejected(exc2) or probe["block_system"] is not None:
                    raise
                probe["block_system"] = False
                msg = _call(_shape(False))
            else:
                probe["block_system"] = True
        elif probe["block_system"] is None:
            probe["block_system"] = False
            msg = _call(_shape(False))
        else:
            raise
    else:
        if use_blocks:
            probe["block_system"] = True
            if probe["cache_ttl"] is None:
                probe["cache_ttl"] = True
    if config.on_usage is not None:
        try:
            config.on_usage(usage_mod.from_response(msg))
        except Exception:
            pass  # accounting must never break a turn
    if config.stream:
        return msg
    for b in msg.content:
        if b.type == "text":
            on_text(b.text)
        elif b.type == "thinking" and on_thinking is not None:
            on_thinking(b.thinking)
    return msg


# The two history rules live in `history.py` and are enforced inside client.create_turn /
# client.stream_turn, so no send can miss them. Re-exported here because repairing a
# session file on load is a real use with no send attached to it.
_strip_stranded_server_tools = history_mod.strip_stranded_server_tools
sanitize_history = history_mod.sanitize_history


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
