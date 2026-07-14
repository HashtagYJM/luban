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

from luban import paths
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


def get_client() -> Any:
    provider = _load_provider()
    if provider is None:
        raise RuntimeError(_SETUP_HINT)
    return provider.build_client()


# Tri-state: None = untried, True = backend accepts thinking/effort, False = rejected
# (probed once per process so a backend that lacks them, e.g. some non-Anthropic
# endpoints, degrades to a plain request instead of erroring every turn).
_EXTRAS_SUPPORTED: bool | None = None


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


def create_turn(client, *, model, max_tokens, system, messages, tools,
                thinking=False, effort="medium", verbose=False, on_retry=None):
    global _EXTRAS_SUPPORTED
    base = dict(model=model, max_tokens=max_tokens, system=system,
                messages=messages, tools=tools)
    extras = _thinking_extras(thinking, effort, verbose) if _EXTRAS_SUPPORTED is not False else {}
    if not extras:
        return _with_retry(lambda: client.messages.create(**base), on_retry)
    try:
        msg = _with_retry(lambda: client.messages.create(**base, **extras), on_retry)
        _EXTRAS_SUPPORTED = True
        return msg
    except Exception as exc:
        if _EXTRAS_SUPPORTED is True:
            raise  # extras worked before — this is a real error, don't mask it
        if is_transient(exc):
            raise  # a dropped connection is not "this backend rejects extras"
        msg = _with_retry(lambda: client.messages.create(**base), on_retry)  # probe
        _EXTRAS_SUPPORTED = False
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
                on_retry=None):
    global _EXTRAS_SUPPORTED
    base = dict(model=model, max_tokens=max_tokens, system=system,
                messages=messages, tools=tools)
    extras = _thinking_extras(thinking, effort, verbose) if _EXTRAS_SUPPORTED is not False else {}
    if not extras:
        return _with_retry(lambda: _stream_once(client, base, {}, on_text, on_thinking),
                           on_retry)
    try:
        msg = _with_retry(
            lambda: _stream_once(client, base, extras, on_text, on_thinking), on_retry)
        _EXTRAS_SUPPORTED = True
        return msg
    except Exception as exc:
        if _EXTRAS_SUPPORTED is True:
            raise  # extras worked before — this is a real error, don't mask it
        # The first-run probe must not read a DROPPED CONNECTION as "this backend
        # rejects thinking/effort" — that would silently disable them for the whole
        # process because a proxy hiccuped on turn one.
        if is_transient(exc):
            raise
        msg = _with_retry(
            lambda: _stream_once(client, base, {}, on_text, on_thinking), on_retry)
        _EXTRAS_SUPPORTED = False
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
                blocks.append({"type": "thinking", "thinking": b.thinking, "signature": signature})
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
