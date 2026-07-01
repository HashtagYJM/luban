"""Model-client access. No internal identifiers live here.

The one company-specific line belongs in `client_local.py` (gitignored),
which must define `build_client()` returning an Anthropic-compatible client
exposing `.messages.create(...)` and `.messages.stream(...)`.
"""
from __future__ import annotations

import types
from typing import Any

DEFAULT_MODEL = "claude-sonnet-5"

_SETUP_HINT = (
    "luban/client_local.py not found. Copy client_local.example.py to "
    "client_local.py and edit build_client() to return your Anthropic-"
    "compatible client. (client_local.py is gitignored on purpose.)"
)


def _import_local() -> types.ModuleType:
    from luban import client_local  # noqa: PLC0415  (lazy on purpose)
    return client_local


def get_client() -> Any:
    try:
        local = _import_local()
    except ModuleNotFoundError as exc:
        raise RuntimeError(_SETUP_HINT) from exc
    return local.build_client()


def create_turn(client, *, model, max_tokens, system, messages, tools):
    return client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=messages, tools=tools,
    )


def stream_turn(client, *, model, max_tokens, system, messages, tools, on_text):
    with client.messages.stream(
        model=model, max_tokens=max_tokens, system=system,
        messages=messages, tools=tools,
    ) as stream:
        for text in stream.text_stream:
            on_text(text)
        return stream.get_final_message()


def message_to_blocks(message) -> list[dict]:
    blocks: list[dict] = []
    for b in message.content:
        if b.type == "text":
            blocks.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return blocks
