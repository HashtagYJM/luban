"""The one guarantee every API send has to satisfy: a valid message history.

This lives in its own leaf module for one reason. The rules were enforced by calling
`sanitize_history` at each place that sends — run_turn's returns, save, restore — and a
fourth send path (compaction) was added without one, which 400'd every later request on any
thread that had used web search. The guard existed and was correct; the enumeration of
callers was what failed, and a fifth path (folding) had the same gap.

So the rules moved to where they cannot be forgotten: `client.create_turn` and
`client.stream_turn` apply them to everything they send. Enumerating GUARDS is a promise;
enforcing at the SEND is a property. `agent` re-exports both names, because repairing a
session file on load is a real use that has no send attached to it.
"""
from __future__ import annotations


def strip_stranded_server_tools(messages: list[dict]) -> list[dict]:
    """Drop any server_tool_use with no web_search_tool_result answering it.

    A web search arrives as a pair in one assistant turn, and the API rejects a
    server_tool_use that no result follows. A bare one reaches us two ways: a paused
    server tool that exhausted MAX_PAUSE_RESUMES, and a session file written before this
    guarantee existed. Whole-history, because the strand can sit anywhere — unlike the
    tail rule below.

    This does NOT repair server-side clearing: that damage exists only on the server's
    edited copy of the history, which the client never sees.
    """
    answered = {b.get("tool_use_id") for m in messages
                for b in m.get("content", []) if isinstance(b, dict)
                and b.get("type") == "web_search_tool_result"}
    out = []
    changed = False
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        kept = [b for b in content if not (
            isinstance(b, dict) and b.get("type") == "server_tool_use"
            and b.get("id") not in answered)]
        if len(kept) == len(content):
            out.append(m)
        elif kept:
            out.append({**m, "content": kept})
            changed = True
        else:
            changed = True  # nothing left — drop the message entirely
    return out if changed else messages


def sanitize_history(messages: list[dict]) -> list[dict]:
    """Guarantee an API-valid history. Two rules:

    1. No server_tool_use may stand without the web_search_tool_result answering it,
       anywhere in the history — see strip_stranded_server_tools.
    2. History must never END in an assistant message with unanswered tool_use blocks.

    The Anthropic API requires every tool_use to be immediately followed by its
    tool_result. A response truncated at max_tokens mid-tool-call (or any path that
    leaves a trailing tool_use) would 400 on the next send — and on resume that crash
    killed the session. This strips trailing unanswered tool_use blocks (keeping any
    text), dropping a message that becomes empty. Pure; returns a new list only when it
    changes something.
    """
    if not messages:
        return messages
    out = list(strip_stranded_server_tools(messages))
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
