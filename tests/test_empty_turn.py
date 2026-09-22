"""A turn that comes back empty must not kill the session.

Field symptom: a blank `luban>` answer, then a session that never worked again, and a
saved file that reopened just as dead. The mechanism is one line of history — the API
rejects a message whose content is empty, so a single blank response 400s every later
send, and the session is saved after every turn.

Two upstream causes reach the same place and neither is repairable upstream: the model
genuinely returns nothing, or the only block is unsigned thinking, which `message_to_blocks`
drops on purpose because an unsigned block fails validation when echoed back.
"""
from dataclasses import dataclass, field

from luban import agent, audit, cli, history, tools


@dataclass
class Blk:
    type: str
    text: str = ""
    thinking: str = ""
    signature: str = ""


@dataclass
class Resp:
    content: list
    stop_reason: str = "end_turn"
    usage: object = None


class _Messages:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.scripted.pop(0)


class Client:
    def __init__(self, scripted):
        self.messages = _Messages(scripted)


def _ctx(tmp_path):
    return tools.ToolContext(tmp_path, lambda p: True, lambda a, b, c: None, lambda c: None)


def _run(tmp_path, response, on_empty=None):
    cfg = agent.AgentConfig("m", 100, stream=False)
    return agent.run_turn(
        Client([response]), cfg, [{"role": "user", "content": "hi"}],
        _ctx(tmp_path), lambda t: None, on_empty=on_empty,
    )


# --- the history guarantee ---------------------------------------------------


def test_an_empty_response_leaves_no_empty_message_in_history(tmp_path):
    msgs = _run(tmp_path, Resp([]))
    assert not any(m.get("content") in ([], "") for m in msgs)


def test_a_thinking_only_response_with_no_signature_does_the_same(tmp_path):
    """Unsigned thinking is dropped by message_to_blocks (correctly), which empties the
    message — so the safe-looking path arrives at the identical fatal state."""
    msgs = _run(tmp_path, Resp([Blk(type="thinking", thinking="reasoned, unsigned")]))
    assert not any(m.get("content") in ([], "") for m in msgs)


def test_sanitize_heals_a_session_already_poisoned(tmp_path):
    """Sessions saved before this fix carry the empty message on disk. Loading one must
    repair it, not merely avoid creating another."""
    poisoned = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": []},
        {"role": "user", "content": "still there?"},
    ]
    cleaned = history.sanitize_history(poisoned)
    assert {"role": "assistant", "content": []} not in cleaned
    assert len(cleaned) == 2


def test_a_whitespace_only_string_counts_as_empty():
    cleaned = history.sanitize_history([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "   \n "},
    ])
    assert len(cleaned) == 1


def test_a_normal_turn_is_untouched(tmp_path):
    msgs = _run(tmp_path, Resp([Blk(type="text", text="hello")]))
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == [{"type": "text", "text": "hello"}]


# --- and it must be LOUD -----------------------------------------------------


def test_the_human_is_told_rather_than_shown_a_blank_line(tmp_path):
    seen = []
    _run(tmp_path, Resp([], stop_reason="end_turn"), on_empty=seen.append)
    assert [m.stop_reason for m in seen] == ["end_turn"]


def test_the_typed_prompt_is_kept_for_retry(monkeypatch, tmp_path):
    """Same contract as a turn the network killed: the prompt is not lost, and the
    history does not end on an unanswered user turn."""
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    from luban import sessions
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    session = cli.Session(model="m", max_tokens=10, auto=True, stream=False, messages=[
        {"role": "assistant", "content": [{"type": "text", "text": "earlier"}]},
        {"role": "user", "content": "the prompt I typed"},
    ])
    cli.empty_turn_notice(session, Resp([]))
    assert session.last_failed == "the prompt I typed"
    assert session.messages[-1]["role"] == "assistant"
    body = "".join(printed)
    assert "empty response" in body and "/retry" in body
    # No settings hint: the one that pointed at context_editing was wrong for a month.
    assert "context_editing" not in body and "audit.jsonl" in body


def test_an_empty_response_mid_turn_keeps_the_tool_pair_intact(monkeypatch, tmp_path):
    """The empty answer can come AFTER tool calls. Then the last user message is a tool
    result, not the prompt — and popping it orphans the tool_use before it. The next typed
    line sat where the result had to be, and the API rejected that index on every send,
    /retry included; the saved file carried it into every resume (field, 2026-09-21)."""
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    from luban import sessions
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    session = cli.Session(model="m", max_tokens=10, auto=True, stream=False, messages=[
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
    ])
    cli.empty_turn_notice(session, Resp([]))
    assert session.last_failed is None, "a tool result is not a prompt to retry"
    session.messages.append({"role": "user", "content": "continue"})
    sent = history.sanitize_history(session.messages)
    for i, m in enumerate(sent):
        for b in m["content"] if isinstance(m["content"], list) else []:
            if b.get("type") == "tool_use":
                nxt = sent[i + 1]["content"]
                assert any(r.get("tool_use_id") == b["id"] for r in nxt)


def test_a_tool_use_orphaned_mid_history_is_repaired_on_load():
    """Whole-history, not just the tail: a session already poisoned this way must heal
    when it is loaded, or /resume reopens the same dead thread."""
    poisoned = [
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "reading"},
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]},
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "read_file", "input": {}}]},
        {"role": "user", "content": "continue again"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]
    healed = history.sanitize_history(poisoned)
    assert not any(b.get("type") == "tool_use" for m in healed
                   for b in (m["content"] if isinstance(m["content"], list) else []))
    assert healed[1]["content"] == [{"type": "text", "text": "reading"}], "text survives"
    assert [m["content"] for m in healed if m["role"] == "user"] == [
        "do the thing", "continue", "continue again"]
    # an intact pair is never touched
    fine = poisoned[:2] + [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}]
    assert history.sanitize_history(fine) == fine


# --- and the work must be on disk ------------------------------------------


def test_the_work_of_an_abandoned_turn_is_on_disk(monkeypatch, tmp_path):
    """A turn that ends in an empty answer, an exception, or Ctrl-C has usually done real
    work — tool calls the in-turn hook already handed to the session. That history was
    left in memory only: the file on disk still ended where the previous turn did, so
    closing luban after the failure (which is when people close it) lost the whole turn,
    and the resume knew nothing of it (field, 2026-09-22)."""
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    from luban import sessions
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    session = cli.Session(model="m", max_tokens=10, auto=True, stream=False, messages=[
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "run_command", "input": {"command": "x"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "[exit code: 1]"}]},
    ], session_id="s1")
    cli.empty_turn_notice(session, Resp([]))
    saved = sessions.load("s1", tmp_path)["messages"]
    assert any(b.get("id") == "t1" for m in saved for b in
               (m["content"] if isinstance(m["content"], list) else []))
    # The same holds when the turn died by exception or interrupt: both go through
    # abandon_turn, and a pop of the typed prompt must be written too.
    session.messages.append({"role": "user", "content": "typed, then the network died"})
    assert cli.abandon_turn(session) == "typed, then the network died"
    assert sessions.load("s1", tmp_path)["messages"][-1]["role"] == "user"
    assert "typed" not in str(sessions.load("s1", tmp_path)["messages"][-1])


# --- and the provider's account of it must be written down --------------------


def test_the_provider_s_account_of_a_blank_turn_is_recorded_and_shown(monkeypatch, tmp_path):
    """Three blank turns in one field session (2026-09-22), all `end_turn`, and nothing to
    tell a content filter from a refusal from a genuinely empty answer. The notice now
    prints what the provider said and appends it to audit.jsonl, where the user already
    looks; and it no longer recommends `context_editing = false`, which was never the cause."""
    import json
    from luban import sessions
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    session = cli.Session(model="gpt-6", max_tokens=10, auto=True, stream=False,
                          messages=[{"role": "user", "content": "hi"}], project="p")
    msg = Resp([], stop_reason="end_turn")
    msg.diagnostics = {"status": "incomplete", "reason": "content_filter",
                       "error": None, "output": ["message(refusal: no)"]}
    cli.empty_turn_notice(session, msg)
    body = "".join(printed)
    assert "content_filter" in body and "refusal" in body
    assert "context_editing" not in body
    row = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert row["tool"] == "model:empty" and row["reason"] == "content_filter"
    assert row["project"] == "p" and row["target"] == "gpt-6"


def test_a_blank_claude_turn_records_tokens_surface_and_clearing(monkeypatch, tmp_path):
    """Every blank on Claude carried `+2 out` and, in the field, appeared only with
    context_editing on. Before any clearing applies, that setting changes exactly one
    thing — the call goes through the beta surface — so the row has to say whether it
    did, what the model produced, and whether anything had been cleared."""
    import json
    from luban import client as client_mod, sessions, usage
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    client_mod.probes("claude-opus-5")["ctx_mgmt"] = True
    session = cli.Session(model="claude-opus-5", max_tokens=10, auto=True, stream=False,
                          messages=[{"role": "user", "content": "hi"}])
    session.ledger.add(usage.Usage(input_tokens=50_000, output_tokens=2), session.model)
    cli.empty_turn_notice(session, Resp([]))
    row = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert row["output_tokens"] == 2 and row["beta_surface"] is True
    assert row["cleared_tokens"] == 0 and row["output"] == []
