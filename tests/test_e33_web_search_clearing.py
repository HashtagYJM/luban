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


# ---------------- L3: the bricked session heals itself ----------------

class _Status400(Exception):
    status_code = 400


class _FakeBeta:
    """A backend whose beta surface raises the orphan-pair 400 the clear produces."""
    def __init__(self, exc):
        self.messages = type("M", (), {"create": self._raise})()
        self._exc = exc

    def _raise(self, **kw):
        raise self._exc


class _FakeClient:
    def __init__(self, exc):
        self.beta = _FakeBeta(exc)


_STRAND_400 = _Status400(
    "messages.12: `web_search_tool_use` ids were found without `web_search_tool_result` "
    "blocks immediately after: srv_1. Each `web_search_tool_use` block must have a "
    "corresponding `web_search_tool_result` block in the next message."
)


def _attempt(exc, prior_success):
    client_mod._PROBES.clear()
    if prior_success:
        client_mod.probes("m")["ctx_mgmt"] = True
    return client_mod._try_context_managed(
        _FakeClient(exc), "create", {"model": "m"}, {},
        client_mod.context_management(150_000), None)


def test_the_strand_400_turns_clearing_off_even_after_it_worked():
    """The branch that makes E33 fatal: once a context-managed call succeeds the probe
    reads True and every later exception re-raises. The early turns ALWAYS succeed —
    the 400 only starts once the trigger is crossed — so without this the thread dies."""
    assert _attempt(_STRAND_400, prior_success=True) is None
    assert client_mod.probes("m")["ctx_mgmt"] is False
    client_mod._PROBES.clear()


def test_an_unrelated_400_still_raises():
    """Over-reach guard: a predicate that swallows every 400 passes the test above too."""
    import pytest
    with pytest.raises(_Status400):
        _attempt(_Status400("model: unknown model 'm'"), prior_success=True)
    client_mod._PROBES.clear()


def test_a_transient_failure_still_raises(monkeypatch):
    """A 500 is the network, not a rejection — it must not disable clearing for good.

    sleep is stubbed because _with_retry backs off for real between attempts."""
    import pytest

    class _Status500(Exception):
        status_code = 500

    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)
    with pytest.raises(_Status500):
        _attempt(_Status500("web_search_tool_result upstream error"), prior_success=True)
    client_mod._PROBES.clear()


# ---------------- L4: the guarantee sits at the SEND, not at a list of callers ----------------
# The recurrence proved the shape of the defect. Three send paths called sanitize_history
# and a fourth (compaction) did not, so a thread that had web-searched 400'd the moment it
# was compacted — and folding, added later, had the same gap. Enumerating callers is a
# promise that each new caller must remember to keep. Enforcing inside the two functions
# that actually send is a property no caller can miss.

def _stranded():
    return [{"role": "user", "content": "search"},
            {"role": "assistant", "content": [
                {"type": "server_tool_use", "id": "w1", "name": "web_search", "input": {}}]},
            {"role": "user", "content": "and now compact"}]


class _Recorder:
    """Stands in for the company client, capturing what would go over the wire."""
    def __init__(self):
        self.sent = None
        self.messages = self

    def create(self, **kw):
        self.sent = kw["messages"]
        return type("M", (), {"content": [], "usage": None})()

    def stream(self, **kw):
        self.sent = kw["messages"]
        raise RuntimeError("stop here — the payload is what is under test")


def _blocks(messages):
    return [b.get("type") for m in messages for b in m.get("content", [])
            if isinstance(b, dict)]


def test_create_turn_cannot_send_a_strand():
    rec = _Recorder()
    client_mod.create_turn(rec, model="m", max_tokens=10, system="s",
                           messages=_stranded(), tools=[])
    assert "server_tool_use" not in _blocks(rec.sent)


def test_stream_turn_cannot_send_a_strand():
    rec = _Recorder()
    try:
        client_mod.stream_turn(rec, model="m", max_tokens=10, system="s",
                               messages=_stranded(), tools=[], on_text=lambda t: None)
    except Exception:
        pass
    assert rec.sent is not None, "the send never reached the client"
    assert "server_tool_use" not in _blocks(rec.sent)


def test_the_caller_is_not_trusted_to_remember():
    """Every path that reaches the API — turn, fold, compact, and anything added next —
    goes through one of these two functions, so none of them can carry a strand."""
    import inspect
    for fn in (client_mod.create_turn, client_mod.stream_turn):
        src = inspect.getsource(fn)
        assert "sanitize_history(messages)" in src, (
            f"{fn.__name__} sends the caller's list unchecked")
