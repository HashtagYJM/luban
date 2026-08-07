"""Model-client access. No internal identifiers live here.

The company-specific client lives in a `client_local.py` that defines
`build_client()` returning an Anthropic-compatible client exposing
`.messages.create(...)` and `.messages.stream(...)`. It is resolved from,
in order: the `LUBAN_CLIENT_LOCAL` env var, `~/.luban/client_local.py`,
or an in-package `luban/client_local.py` (dev fallback). It is never
committed.
"""
from __future__ import annotations

import importlib.util
import os
import random
import time
import types
from pathlib import Path
from types import SimpleNamespace

from luban import paths
from luban.providers import openai as openai_mod
from typing import Any

DEFAULT_MODEL = "claude-sonnet-5"

USER_CLIENT_PATH = paths.luban_home() / "client_local.py"

_SETUP_HINT = (
    "No client_local.py found. Create ~/.luban/client_local.py with a "
    "build_client() that returns your Anthropic-compatible client "
    "(see client_local.example.py). You can also point LUBAN_CLIENT_LOCAL "
    "at a file. It is never committed."
)


def _load_from_path(path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("luban_client_local", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # errors inside the file surface here
    return module


def _in_package_local() -> types.ModuleType | None:
    try:
        from luban import client_local  # noqa: PLC0415  (lazy dev fallback)
    except ModuleNotFoundError as exc:
        if exc.name == "luban.client_local":
            return None
        raise  # a DIFFERENT missing module (e.g. inside client_local) — surface it
    return client_local


def _load_provider() -> types.ModuleType | None:
    override = os.environ.get("LUBAN_CLIENT_LOCAL")
    if override and Path(override).exists():
        return _load_from_path(Path(override))
    if USER_CLIENT_PATH.exists():
        return _load_from_path(USER_CLIENT_PATH)
    return _in_package_local()


# ------------------------------------------------------------------------ routing ----
# Model ids are disjoint across providers, so a prefix IS the route. Anything
# unrecognised goes to the primary client — which is where it went before this existed.
_OPENAI_PREFIXES = ("gpt-", "chatgpt-", "o1", "o3", "o4")


def provider_for(model: str) -> str:
    return "openai" if (model or "").startswith(_OPENAI_PREFIXES) else "anthropic"


class _MessagesRouter:
    """`.messages` dispatched by the `model` kwarg. Nothing else about the call changes."""

    def __init__(self, facade: "Facade", beta: bool = False):
        self._facade, self._beta = facade, beta

    def _target(self, kw):
        client = self._facade.client_for(kw.get("model", ""))
        if self._beta:
            return client.beta.messages  # AttributeError => caller falls back, by design
        return client.messages

    def create(self, **kw):
        return self._target(kw).create(**kw)

    def stream(self, **kw):
        return self._target(kw).stream(**kw)

    def count_tokens(self, **kw):
        return self._target(kw).count_tokens(**kw)


class _ModelsRouter:
    def __init__(self, facade: "Facade"):
        self._facade = facade

    def list(self):
        """Every model id luban can route, from every backend. One provider being
        unable to answer must not hide the other's catalogue."""
        ids: list[str] = []
        for client in self._facade.clients():
            try:
                result = client.models.list()
                ids += [m.id for m in getattr(result, "data", result)]
            except Exception:
                continue
        return [SimpleNamespace(id=i) for i in ids]


class Facade:
    """One client per provider behind one Anthropic-shaped surface.

    The Anthropic side is the ORIGINAL client object, untouched — `client_for` returns it
    by identity, so caching, thinking, betas and streaming behave exactly as they did
    before this existed. A regression there fails this work regardless of how well the
    other branch performs.
    """

    def __init__(self, anthropic, openai):
        self._by_provider = {"anthropic": anthropic, "openai": openai}
        self.messages = _MessagesRouter(self)
        self.beta = SimpleNamespace(messages=_MessagesRouter(self, beta=True))
        self.models = _ModelsRouter(self)

    def client_for(self, model: str):
        return self._by_provider[provider_for(model)]

    def clients(self):
        return list(self._by_provider.values())


def get_client() -> Any:
    provider = _load_provider()
    if provider is None:
        raise RuntimeError(_SETUP_HINT)
    primary = provider.build_client()
    build_openai = getattr(provider, "build_openai_client", None)
    if build_openai is None:
        return primary  # one provider: no facade, no behaviour change at all
    return Facade(primary, openai_mod.OpenAIAdapter(build_openai()))


# ------------------------------------------------------------------------- probes ----
# Tri-states: None = untried, True = backend accepts it, False = rejected. Probed once so
# a backend that lacks a capability degrades to a plain request instead of erroring every
# turn.
#
# KEYED BY PROVIDER, not process-global. One process now means more than one backend: an
# Anthropic turn that sets extras=True would otherwise leave the OpenAI branch inheriting
# a flag it cannot honour, and vice versa. Provider is what the flag is actually a
# property of.
_PROBE_FIELDS = ("extras", "block_system", "ctx_mgmt", "cache_ttl")
_PROBES: dict[str, dict] = {}


def probes(model: str) -> dict:
    return _PROBES.setdefault(provider_for(model), dict.fromkeys(_PROBE_FIELDS, None))


def _thinking_extras(thinking: bool, effort: str, verbose: bool = False) -> dict:
    if not thinking:
        return {}
    # display: "summarized" streams the reasoning (grey text); "omitted" thinks
    # silently. Set it explicitly so behavior is the same across models.
    display = "summarized" if verbose else "omitted"
    extras: dict = {"thinking": {"type": "adaptive", "display": display}}
    if effort:
        extras["output_config"] = {"effort": effort}
    return extras



# ---------------------------------------------------------------- context editing ----
# Server-side clearing of stale TOOL RESULTS before the prompt reaches the model. This is
# the single largest lever on an agentic session's token use: a file-heavy session accrues
# dozens of results at up to MAX_OUTPUT (20,000 chars) each, and nothing ever removed them.
# Anthropic call tool-result clearing "the safest lightest touch form of compaction".
#
# It is NOT auto-compaction: conversation is never summarised or dropped, only stale tool
# OUTPUT, and the client keeps the full unmodified history on disk either way.
#
# Values are derived, not offered as knobs:
#   trigger        0.6 x warn_tokens - clear well before compaction is needed
#   keep           the recent tool pairs that are still the working set
#   clear_at_least clearing invalidates the cached prefix at that point, so many small
#                  clears are strictly worse than a few large ones. This floor makes each
#                  clear worth the cache write it costs.
#   exclude_tools  two different reasons, kept apart deliberately. Memory results are
#                  small and semantically load-bearing; clearing them would make the model
#                  re-fetch what it already had — wasteful. A web search result CANNOT be
#                  cleared at all: it arrives paired with the server_tool_use that asked
#                  for it, and the API rejects a server_tool_use no result follows. Clear
#                  one and every later send 400s against a history the client never sees.
CONTEXT_MGMT_BETA = "context-management-2025-06-27"
_KEEP_TOOL_USES = 6
_CLEAR_AT_LEAST = 8_000
_MEMORY_TOOLS = ["remember", "recall", "forget", "journal", "sessions"]
# Server tools are named, not versioned, in the block that carries them: server_tool_use
# has name "web_search" under every web_search_tool_type. Match the name.
_UNCLEARABLE_TOOLS = ["web_search"]
_NEVER_CLEAR = _MEMORY_TOOLS + _UNCLEARABLE_TOOLS


def context_management(warn_tokens: int) -> dict:
    return {"edits": [{
        "type": "clear_tool_uses_20250919",
        "trigger": {"type": "input_tokens", "value": max(20_000, int(warn_tokens * 0.6))},
        "keep": {"type": "tool_uses", "value": _KEEP_TOOL_USES},
        "clear_at_least": {"type": "input_tokens", "value": _CLEAR_AT_LEAST},
        "exclude_tools": _NEVER_CLEAR,
    }]}


def _beta_fn(client, method: str):
    """The beta surface, or None if this backend has no beta surface.

    A corporate proxy that lacks it must keep working, so the caller falls back to the
    ordinary path rather than failing.
    """
    beta = getattr(client, "beta", None)
    msgs = getattr(beta, "messages", None) if beta is not None else None
    return getattr(msgs, method, None) if msgs is not None else None




def _is_strand_rejection(exc: BaseException) -> bool:
    """The 400 that server-side clearing produces by stranding a web search (E33).

    Clearing a web_search_tool_result leaves the server_tool_use that asked for it with
    nothing following, and the API rejects that — on its own edited copy of the history,
    which is why no local repair reaches it. Treated as the backend refusing the
    parameter rather than as a failed turn, so the session drops to the unmanaged path
    instead of 400ing on every send until the user starts over.

    Kept narrow on purpose: only a 400 naming the block type. A predicate that swallowed
    every 400 would silently disable the largest token lever on a typo in a model name.
    """
    if getattr(exc, "status_code", None) not in (400, None):
        return False
    return "web_search_tool_result" in str(exc)


def _try_context_managed(client, method, base, extras, ctx_mgmt, on_retry,
                         on_text=None, on_thinking=None):
    """Issue via the beta surface with context editing on. None => caller falls back.

    Probed once per provider: a backend without the beta surface, or one that rejects the
    parameter, must keep working rather than fail the turn. The OpenAI branch has no beta
    surface at all, which is exactly this path.
    """
    p = probes(base.get("model", ""))
    if p["ctx_mgmt"] is False:
        return None
    fn = _beta_fn(client, method)
    if fn is None:
        p["ctx_mgmt"] = False
        return None
    kw = dict(**base, **extras, betas=[CONTEXT_MGMT_BETA], context_management=ctx_mgmt)
    try:
        if method == "stream":
            msg = _with_retry(
                lambda: _stream_with(fn, kw, on_text, on_thinking), on_retry)
        else:
            msg = _with_retry(lambda: fn(**kw), on_retry)
    except Exception as exc:
        if is_transient(exc):
            raise  # the network died — not a rejection
        if p["ctx_mgmt"] is True and not _is_strand_rejection(exc):
            raise  # it worked before, so this is a real failure of this turn
        p["ctx_mgmt"] = False
        return None
    p["ctx_mgmt"] = True
    return msg


def _stream_with(fn, kw, on_text, on_thinking):
    with fn(**kw) as stream:
        for event in stream:
            if getattr(event, "type", None) != "content_block_delta":
                continue
            delta = event.delta
            dtype = getattr(delta, "type", None)
            if dtype == "text_delta":
                on_text(delta.text)
            elif dtype == "thinking_delta" and on_thinking is not None:
                on_thinking(delta.thinking)
        return stream.get_final_message()


def create_turn(client, *, model, max_tokens, system, messages, tools,
                thinking=False, effort="medium", verbose=False, on_retry=None,
                ctx_mgmt=None):
    p = probes(model)
    base = dict(model=model, max_tokens=max_tokens, system=system,
                messages=messages, tools=tools)
    extras = _thinking_extras(thinking, effort, verbose) if p["extras"] is not False else {}
    if ctx_mgmt:
        msg = _try_context_managed(client, "create", base, extras, ctx_mgmt, on_retry)
        if msg is not None:
            return msg
    if not extras:
        return _with_retry(lambda: client.messages.create(**base), on_retry)
    try:
        msg = _with_retry(lambda: client.messages.create(**base, **extras), on_retry)
        p["extras"] = True
        return msg
    except Exception as exc:
        if p["extras"] is True:
            raise  # extras worked before — this is a real error, don't mask it
        if is_transient(exc):
            raise  # a dropped connection is not "this backend rejects extras"
        msg = _with_retry(lambda: client.messages.create(**base), on_retry)  # probe
        p["extras"] = False
        return msg


def _stream_once(client, base, extras, on_text, on_thinking):
    # Iterate raw stream events (not just .text_stream) so reasoning models that
    # emit `thinking` deltas are surfaced too — otherwise a thinking-only turn
    # streams nothing and the user sees a blank response.
    with client.messages.stream(**base, **extras) as stream:
        for event in stream:
            if getattr(event, "type", None) != "content_block_delta":
                continue
            delta = event.delta
            dtype = getattr(delta, "type", None)
            if dtype == "text_delta":
                on_text(delta.text)
            elif dtype == "thinking_delta" and on_thinking is not None:
                on_thinking(delta.thinking)
        return stream.get_final_message()


STREAM_RETRIES = 3  # attempts after the first, per turn

# Two failures, two very different right answers.
# A CUT STREAM is a one-off: the connection died, the backend is fine, go again soon.
# An OVERLOAD (429/529) means the backend is saturated — and by the time it reaches
# us the SDK has ALREADY burned its own max_retries on it with short exponential
# backoff. Retrying again seconds later just adds load to something that is already
# telling us it has none to give. Wait properly.
_BACKOFF_DROPPED = (2, 5, 12)
_BACKOFF_OVERLOAD = (20, 60, 120)
_MAX_RETRY_AFTER = 180  # trust the server's retry-after, but don't hang forever on it


def _is_overload(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (429, 503, 529)
    text = str(exc).lower()
    return "overloaded" in text or "rate limit" in text


def _retry_after(exc: BaseException) -> float | None:
    """The server's own answer to 'when should I come back'. Always beats a guess."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, min(float(raw), _MAX_RETRY_AFTER))
    except (TypeError, ValueError):
        return None  # HTTP-date form; not worth parsing — fall back to our backoff


def retry_delay(exc: BaseException, attempt: int) -> float:
    """Seconds to wait before attempt N+1. Jittered: a shared corporate gateway sees
    every colleague's luban at once, and un-jittered backoff marches them all back in
    lockstep — the retries re-collide and the overload sustains itself."""
    told = _retry_after(exc)
    if told is not None:
        return told
    table = _BACKOFF_OVERLOAD if _is_overload(exc) else _BACKOFF_DROPPED
    base = table[min(attempt, len(table) - 1)]
    return base * random.uniform(0.8, 1.3)

# Matched by NAME and message, not by class, so this works whatever HTTP stack the
# client wraps (we never import httpx — luban stays zero-dependency).
_TRANSIENT_TYPES = {
    "APIConnectionError", "APITimeoutError", "RemoteProtocolError", "ProtocolError",
    "ReadError", "ReadTimeout", "ConnectError", "ConnectionResetError",
    "IncompleteRead", "ChunkedEncodingError", "InternalServerError",
    "OverloadedError", "APIStatusError",
}
_TRANSIENT_TEXT = (
    "peer closed connection",       # the one the field keeps hitting
    "incomplete chunked read",
    "connection reset",
    "connection aborted",
    "server disconnected",
    "remote end closed",
    "overloaded",
)


def is_transient(exc: BaseException) -> bool:
    """A network-level failure that a fresh identical request may well survive.

    The SDK's own max_retries CANNOT cover this case: it retries failures that
    happen while *issuing* the request. Once a 200 is streaming and bytes have
    been consumed, a severed body is handed to us as an exception — there is no
    resume for a half-read stream, so the only possible retry is a new request.
    That's what this enables. Deliberately excludes 4xx (a bad request will fail
    identically forever) — hence the status check below.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):  # an HTTP status is authoritative — don't also sniff text
        return status in (408, 409, 429) or status >= 500
    if type(exc).__name__ in _TRANSIENT_TYPES:
        return True
    text = str(exc).lower()
    return any(frag in text for frag in _TRANSIENT_TEXT)


def _with_retry(call, on_retry=None):
    """Re-issue a turn that died on the wire. Safe to repeat: no tool has run yet
    (they execute only after the turn returns), and the request is unchanged, so a
    retry is a pure re-ask — never a duplicated side effect."""
    last: BaseException | None = None
    for attempt in range(STREAM_RETRIES + 1):
        try:
            return call()
        except Exception as exc:
            if not is_transient(exc) or attempt == STREAM_RETRIES:
                raise
            last = exc
            delay = retry_delay(exc, attempt)
            if on_retry is not None:
                on_retry(exc, attempt + 1, STREAM_RETRIES, delay)
            time.sleep(delay)
    raise last  # unreachable


def stream_turn(client, *, model, max_tokens, system, messages, tools, on_text,
                on_thinking=None, thinking=False, effort="medium", verbose=False,
                on_retry=None, ctx_mgmt=None):
    p = probes(model)
    base = dict(model=model, max_tokens=max_tokens, system=system,
                messages=messages, tools=tools)
    extras = _thinking_extras(thinking, effort, verbose) if p["extras"] is not False else {}
    if ctx_mgmt:
        msg = _try_context_managed(client, "stream", base, extras, ctx_mgmt, on_retry,
                                   on_text=on_text, on_thinking=on_thinking)
        if msg is not None:
            return msg
    if not extras:
        return _with_retry(lambda: _stream_once(client, base, {}, on_text, on_thinking),
                           on_retry)
    try:
        msg = _with_retry(
            lambda: _stream_once(client, base, extras, on_text, on_thinking), on_retry)
        p["extras"] = True
        return msg
    except Exception as exc:
        if p["extras"] is True:
            raise  # extras worked before — this is a real error, don't mask it
        # The first-run probe must not read a DROPPED CONNECTION as "this backend
        # rejects thinking/effort" — that would silently disable them for the whole
        # provider because a proxy hiccuped on turn one.
        if is_transient(exc):
            raise
        msg = _with_retry(
            lambda: _stream_once(client, base, {}, on_text, on_thinking), on_retry)
        p["extras"] = False
        return msg


def message_to_blocks(message) -> list[dict]:
    blocks: list[dict] = []
    for b in message.content:
        if b.type == "text":
            blocks.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif b.type == "thinking":
            # Extended thinking + tool use requires echoing the *signed* thinking
            # block back in the assistant turn, or the next request is rejected.
            # Unsigned thinking (some non-Anthropic backends) is display-only —
            # don't echo it back, as an unsigned block would fail validation.
            signature = getattr(b, "signature", None)
            if signature:
                block = {"type": "thinking", "thinking": b.thinking, "signature": signature}
                item_id = getattr(b, "id", None)
                if item_id:
                    # An OpenAI reasoning item must be replayed with its own id alongside
                    # its encrypted state. Anthropic thinking blocks have no id, so this
                    # key is simply absent there.
                    block["id"] = item_id
                blocks.append(block)
        elif b.type == "redacted_thinking":
            blocks.append({"type": "redacted_thinking", "data": b.data})
        elif b.type in ("server_tool_use", "web_search_tool_result"):
            # Server-side tools (web search): the API resolved these inline. Echo the
            # raw block back on the next turn or the follow-up request 400s / loses the
            # search context. model_dump() gives the wire-shaped dict the API expects.
            dump = getattr(b, "model_dump", None)
            if callable(dump):
                blocks.append(dump(exclude_none=True))
    return blocks


def list_models(client) -> list[str] | None:
    """Model ids the client offers, or None if it can't say (never raises)."""
    try:
        result = client.models.list()
        items = getattr(result, "data", result)  # SDK may return a paginated page
        ids = [m.id for m in items]
        return ids or None
    except Exception:
        return None
