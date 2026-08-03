"""OpenAI's Responses API behind luban's Anthropic-shaped client surface.

The driver is capability, not cost: the target is a reasoning model at high effort because
it is judged better for the work. It is the MORE expensive option, so nothing here should
be read as a saving.

Targets the Responses API rather than Chat Completions: OpenAI's own guidance is that
reasoning models do better on it, and quality is the entire reason this exists.

No `import openai`. The adapter receives whatever client `client_local.py` hands it and
calls it duck-typed, exactly as luban already does with the Anthropic client — zero
dependencies is an invariant.
"""
from __future__ import annotations

import json
from types import SimpleNamespace


def _attr(obj, name, default=None):
    """Field access that works whether the SDK returns objects or plain dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------ request A -> O ----

def _instructions(system) -> str:
    """Block-form system -> a single instructions string, dropping `cache_control`.

    There is no placement-controlled caching on this provider, so the breakpoints luban
    sets are not translated to anything — they are simply gone.
    """
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n\n".join(
            b.get("text", "") for b in system if isinstance(b, dict) and b.get("text"))
    return ""


def _text_part(role: str, text: str) -> dict:
    return {"type": "output_text" if role == "assistant" else "input_text", "text": text}


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return "" if content is None else str(content)


def input_items(messages: list[dict]) -> list[dict]:
    """Anthropic messages -> Responses `input` items.

    Text accumulates into one message item; anything else (a call, a result, a reasoning
    item) is a top-level item, so accumulated text is flushed first to keep the original
    order within a turn.
    """
    items: list[dict] = []

    def flush(role, parts):
        if parts:
            items.append({"role": role, "content": list(parts)})
            parts.clear()

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            items.append({"role": role, "content": [_text_part(role, content)]})
            continue
        parts: list[dict] = []
        for b in content or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                parts.append(_text_part(role, b.get("text", "")))
            elif t == "tool_use":
                flush(role, parts)
                items.append({"type": "function_call", "call_id": b.get("id", ""),
                              "name": b.get("name", ""),
                              "arguments": json.dumps(b.get("input") or {})})
            elif t == "tool_result":
                flush(role, parts)
                items.append({"type": "function_call_output",
                              "call_id": b.get("tool_use_id", ""),
                              "output": _result_text(b.get("content"))})
            elif t == "thinking" and b.get("signature"):
                # Stateless multi-turn: with store=false the reasoning item's state lives
                # in `encrypted_content`, which the caller retains and replays. Same
                # contract as echoing a SIGNED Anthropic thinking block — different field
                # names — which is why it rides luban's existing thinking block.
                flush(role, parts)
                item = {"type": "reasoning", "summary": [],
                        "encrypted_content": b["signature"]}
                if b.get("id"):
                    item["id"] = b["id"]
                items.append(item)
        flush(role, parts)
    return items


def _tools(tools) -> list[dict]:
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if "input_schema" in t:
            out.append({"type": "function", "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t["input_schema"]})
        elif t.get("name") == "web_search":
            out.append({"type": "web_search"})  # server-side on both providers
    return out


def build_request(kw: dict) -> dict:
    """The Anthropic-shaped kwargs luban emits -> a Responses request.

    Only genuinely provider-specific parameters are dropped: `cache_control`, `betas`,
    `context_management` and Anthropic's `thinking` block have no counterpart here.
    EFFORT IS NOT ONE OF THEM — it is the setting the user most cares about, so it is
    translated rather than discarded.
    """
    req: dict = {
        "model": kw.get("model", ""),
        "input": input_items(kw.get("messages") or []),
        "store": False,
    }
    instructions = _instructions(kw.get("system"))
    if instructions:
        req["instructions"] = instructions
    if kw.get("max_tokens"):
        req["max_output_tokens"] = kw["max_tokens"]
    tools = _tools(kw.get("tools"))
    if tools:
        req["tools"] = tools
    effort = (kw.get("output_config") or {}).get("effort")
    if effort:
        # luban's vocabulary is a subset of what reasoning.effort accepts, so the values
        # pass through by name. `context: all_turns` is the half of stateless multi-turn
        # that input_items() cannot express on its own.
        req["reasoning"] = {"effort": effort, "context": "all_turns"}
    return req


# ----------------------------------------------------------------- response O -> A ----

def _usage(resp):
    u = _attr(resp, "usage")
    if u is None:
        return None
    total_in = _int(_attr(u, "input_tokens"))
    cached = _int(_attr(_attr(u, "input_tokens_details"), "cached_tokens"))
    return SimpleNamespace(
        # THE SUBTRACTION IS THE POINT. Anthropic reports input_tokens EXCLUDING cache
        # reads and luban sums the three fields to get context size; OpenAI reports the
        # total INCLUDING them. Mapping the name straight across would double-count every
        # cached token.
        input_tokens=max(0, total_in - cached),
        output_tokens=_int(_attr(u, "output_tokens")),
        cache_creation_input_tokens=0,  # caching is automatic here; writes are not billed
        cache_read_input_tokens=cached,
        # Already counted inside output_tokens — reported separately, never added. At high
        # effort these dominate the bill, so output looks inexplicably large without a line
        # that says why.
        reasoning_tokens=_int(_attr(_attr(u, "output_tokens_details"), "reasoning_tokens")),
    )


def _stop_reason(resp, saw_tool_use: bool) -> str:
    if saw_tool_use:
        return "tool_use"
    if _attr(resp, "status") == "incomplete":
        reason = _attr(_attr(resp, "incomplete_details"), "reason")
        if reason == "max_output_tokens":
            return "max_tokens"
    return "end_turn"


def to_message(resp):
    """A Responses result -> the Message shape agent.py and usage.py already read."""
    blocks = []
    saw_tool_use = False
    for item in _attr(resp, "output") or []:
        t = _attr(item, "type")
        if t == "reasoning":
            encrypted = _attr(item, "encrypted_content")
            if not encrypted:
                continue  # unreplayable — drop it, as luban drops unsigned thinking
            summary = "".join(_attr(p, "text") or "" for p in (_attr(item, "summary") or []))
            blocks.append(SimpleNamespace(type="thinking", thinking=summary,
                                          signature=encrypted, id=_attr(item, "id", "")))
        elif t == "function_call":
            saw_tool_use = True
            raw = _attr(item, "arguments") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                parsed = {}
            blocks.append(SimpleNamespace(type="tool_use", id=_attr(item, "call_id", ""),
                                          name=_attr(item, "name", ""), input=parsed))
        elif t == "message":
            text = "".join(
                _attr(p, "text") or "" for p in (_attr(item, "content") or [])
                if _attr(p, "type") in ("output_text", "text"))
            if text:
                blocks.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=blocks, stop_reason=_stop_reason(resp, saw_tool_use),
                           usage=_usage(resp), context_management=None)


# ------------------------------------------------------------------------ surface ----

def _delta(dtype, **kw):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type=dtype, **kw))


class _Stream:
    """`.messages.stream` over a completed response.

    Streaming is out of scope — the user does not stream. Replaying the finished text as
    one delta satisfies the existing consumer without a second code path through the
    adapter.
    """

    def __init__(self, final):
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self._final.content:
            if b.type == "text":
                yield _delta("text_delta", text=b.text)
            elif b.type == "thinking" and b.thinking:
                yield _delta("thinking_delta", thinking=b.thinking)

    def get_final_message(self):
        return self._final


class _Messages:
    def __init__(self, client):
        self._client = client

    def create(self, **kw):
        return to_message(self._client.responses.create(**build_request(kw)))

    def stream(self, **kw):
        return _Stream(self.create(**kw))

    def count_tokens(self, **kw):
        # Nothing equivalent here. Raising is deliberate: the caller falls back to an
        # estimate and must SAY it is an estimate rather than print a number that looks
        # measured.
        raise NotImplementedError("count_tokens is Anthropic-only")


class _Models:
    def __init__(self, client):
        self._client = client

    def list(self):
        return self._client.models.list()


class OpenAIAdapter:
    """An OpenAI client wearing luban's Anthropic-shaped surface."""

    def __init__(self, client):
        self.raw = client
        self.messages = _Messages(client)
        self.models = _Models(client)
