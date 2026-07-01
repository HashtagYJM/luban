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
