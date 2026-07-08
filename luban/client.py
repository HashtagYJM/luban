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


def _thinking_extras(thinking: bool, effort: str) -> dict:
    if not thinking:
        return {}
    extras: dict = {"thinking": {"type": "adaptive", "display": "summarized"}}
    if effort:
        extras["output_config"] = {"effort": effort}
    return extras


def create_turn(client, *, model, max_tokens, system, messages, tools,
                thinking=False, effort="high"):
    global _EXTRAS_SUPPORTED
    base = dict(model=model, max_tokens=max_tokens, system=system,
                messages=messages, tools=tools)
    extras = _thinking_extras(thinking, effort) if _EXTRAS_SUPPORTED is not False else {}
    if not extras:
        return client.messages.create(**base)
    try:
        msg = client.messages.create(**base, **extras)
        _EXTRAS_SUPPORTED = True
        return msg
    except Exception:
        if _EXTRAS_SUPPORTED is True:
            raise  # extras worked before — this is a real error, don't mask it
        msg = client.messages.create(**base)  # first-run probe: retry without extras
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


def stream_turn(client, *, model, max_tokens, system, messages, tools, on_text,
                on_thinking=None, thinking=False, effort="high"):
    global _EXTRAS_SUPPORTED
    base = dict(model=model, max_tokens=max_tokens, system=system,
                messages=messages, tools=tools)
    extras = _thinking_extras(thinking, effort) if _EXTRAS_SUPPORTED is not False else {}
    if not extras:
        return _stream_once(client, base, {}, on_text, on_thinking)
    try:
        msg = _stream_once(client, base, extras, on_text, on_thinking)
        _EXTRAS_SUPPORTED = True
        return msg
    except Exception:
        if _EXTRAS_SUPPORTED is True:
            raise
        msg = _stream_once(client, base, {}, on_text, on_thinking)
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
