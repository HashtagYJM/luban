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
from typing import Any

DEFAULT_MODEL = "claude-sonnet-5"

USER_CLIENT_PATH = Path.home() / ".luban" / "client_local.py"

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


def create_turn(client, *, model, max_tokens, system, messages, tools):
    return client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=messages, tools=tools,
    )


def stream_turn(client, *, model, max_tokens, system, messages, tools, on_text, on_thinking=None):
    # Iterate raw stream events (not just .text_stream) so reasoning models that
    # emit `thinking` deltas are surfaced too — otherwise a thinking-only turn
    # streams nothing and the user sees a blank response.
    with client.messages.stream(
        model=model, max_tokens=max_tokens, system=system,
        messages=messages, tools=tools,
    ) as stream:
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
    return blocks
