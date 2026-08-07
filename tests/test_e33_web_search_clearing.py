"""E33: server-side clearing strands a web search.

A web search yields a PAIR in one assistant turn — a server_tool_use and the
web_search_tool_result answering it — and the pair is not independently clearable: the
API rejects a server_tool_use that no result follows. Clearing the older result while
leaving its use makes every subsequent send 400, on a copy of the history the client
never sees, so nothing local repairs it.
"""
from luban import agent, client as client_mod


# ---------------- L1: the pair is never cleared ----------------

def test_web_search_is_never_cleared():
    """Excluded for a different reason than memory: clearing it is invalid, not wasteful."""
    cm = client_mod.context_management(150_000)["edits"][0]
    assert "web_search" in cm["exclude_tools"]


def test_the_exclusion_covers_every_web_search_tool_version():
    """server_tool_use carries name='web_search' whichever tool type is configured, so
    the exclusion must key on the NAME, never on a version string."""
    excluded = client_mod.context_management(150_000)["edits"][0]["exclude_tools"]
    assert not any("2025" in t or "2026" in t for t in excluded), (
        "a version-stamped entry would silently stop matching on the next tool version"
    )


# ---------------- L2: a stranded server_tool_use never survives locally ----------------

def _search_turn(use_id="srv_1", with_result=True):
    content = [
        {"type": "text", "text": "Searching."},
        {"type": "server_tool_use", "id": use_id, "name": "web_search",
         "input": {"query": "q"}},
    ]
    if with_result:
        content.append({"type": "web_search_tool_result", "tool_use_id": use_id,
                        "content": [{"type": "web_search_result", "url": "u"}]})
    return {"role": "assistant", "content": content}


def test_a_stranded_server_tool_use_is_stripped():
    msgs = [{"role": "user", "content": "go"},
            _search_turn(with_result=False),
            {"role": "user", "content": "next"}]
    out = agent.sanitize_history(msgs)
    kept = [b for m in out for b in m["content"] if isinstance(b, dict)]
    assert not any(b["type"] == "server_tool_use" for b in kept)
    assert any(b.get("text") == "Searching." for b in kept), "text must survive"


def test_a_paired_search_is_returned_untouched():
    """The destructive failure: over-reach that deletes a valid search."""
    msgs = [{"role": "user", "content": "go"},
            _search_turn(),
            {"role": "user", "content": "next"}]
    assert agent.sanitize_history(msgs) == msgs


def test_a_strand_is_found_anywhere_in_history_not_only_at_the_tail():
    msgs = [{"role": "user", "content": "go"},
            _search_turn("srv_1", with_result=False),
            {"role": "user", "content": "mid"},
            _search_turn("srv_2"),
            {"role": "user", "content": "end"}]
    out = agent.sanitize_history(msgs)
    ids = [b["id"] for m in out for b in m["content"]
           if isinstance(b, dict) and b["type"] == "server_tool_use"]
    assert ids == ["srv_2"]


def test_the_tail_guarantee_still_holds():
    """Widening the pass must not weaken what it already promised (E14)."""
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant",
             "content": [{"type": "tool_use", "id": "t1", "name": "write_file",
                          "input": {}}]}]
    assert agent.sanitize_history(msgs) == [msgs[0]]
