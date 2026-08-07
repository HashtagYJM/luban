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
