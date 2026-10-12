"""Three token controls for a long agentic run, judged on the window they leave.

A — stale tool results are stubbed on the client before the fold is needed (every
provider). B — a gpt-* send replays reasoning only from the turn being answered.
C — read_output waits for the job instead of handing back "still running".
"""
import json
import time

import pytest

from luban import cli, config as config_mod, history, sessions as sessions_mod, tools, usage as usage_mod

from tests.test_conversation_fold import _a, _call, _result, _u


def _use(tid):
    return {"type": "tool_use", "id": tid, "name": "read_file", "input": {}}


def _think(sig, text="t"):
    return {"type": "thinking", "thinking": text, "signature": sig, "id": "rs_" + sig,
            history.PROVIDER_KEY: "openai"}


# ------------------------------------------------------------------- B ----

def test_openai_send_replays_reasoning_only_from_the_current_turn():
    msgs = [
        _u("first task"),
        {"role": "assistant", "content": [_think("old1"), _use("t1")]}, _result("t1"),
        {"role": "assistant", "content": [_think("old2"), {"type": "text", "text": "done"}]},
        _u("second task"),
        {"role": "assistant", "content": [_think("new1"), _use("t2")]}, _result("t2"),
    ]
    out = history.for_send(json.loads(json.dumps(msgs)), "openai")
    sigs = [b["signature"] for m in out if isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "thinking"]
    assert sigs == ["new1"]
    assert out[1]["content"][0]["type"] == "tool_use"  # the call survives, the state goes
    assert len(out) == len(msgs)


def test_an_assistant_step_that_was_only_old_reasoning_is_dropped_whole():
    msgs = [_u("a"), {"role": "assistant", "content": [_think("x")]}, _u("b"),
            {"role": "assistant", "content": [_think("y")]}]
    out = history.for_send(msgs, "openai")
    assert [m["role"] for m in out] == ["user", "user", "assistant"]


def test_anthropic_send_is_untouched_by_the_reasoning_rule():
    old = {"type": "thinking", "thinking": "t", "signature": "sig"}
    msgs = [_u("a"), {"role": "assistant", "content": [old, {"type": "text", "text": "x"}]},
            _u("b"), {"role": "assistant", "content": [{"type": "text", "text": "y"}]}]
    assert history.for_send(msgs, "anthropic") == msgs


# ------------------------------------------------------------------- A ----

def _rounds(n, chars=3_000, start=0):
    out = []
    for i in range(start, start + n):
        out += [_call(f"r{i}"), _result(f"r{i}", chars)]
    return out


def _session(tmp_path, monkeypatch, messages, ctx_tokens):
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 1.0)
    monkeypatch.setattr(cli, "standing_tokens", lambda *a: 0)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(tools, "LUBAN_HOME", tmp_path)
    s = cli.Session(model="gpt-x", max_tokens=100, auto=True, stream=False,
                    messages=messages, project=str(tmp_path))
    s.ledger.add(usage_mod.Usage(input_tokens=ctx_tokens), "gpt-x")
    return s, config_mod.Config(platform="mac", warn_tokens=100_000)


def test_stale_results_are_stubbed_and_the_newest_eight_stay_verbatim(tmp_path, monkeypatch):
    msgs = [_u("go")] + _rounds(20) + [_a("done")]
    s, cfg = _session(tmp_path, monkeypatch, msgs, ctx_tokens=60_000)  # over 40% of 100k
    original = json.loads(json.dumps(msgs))
    cli.maintain_context(s, object(), cfg, tmp_path)
    results = [b for m in s.messages if isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result"]
    assert [r["content"].startswith("[cleared ") for r in results] == [True] * 12 + [False] * 8
    assert "read_file" in results[0]["content"] and "~/.luban/sessions/archive/" in results[0]["content"]
    # the call that produced each cleared result is untouched, so the history stays valid
    assert sum(1 for m in s.messages for b in (m["content"] if isinstance(m["content"], list) else [])
               if b.get("type") == "tool_use") == 20
    # the archive holds every original result, and the window shrank
    archives = list((tmp_path / "sessions" / "archive").glob("*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8"))["messages"] == original
    assert s.ledger.context_tokens < 30_000 < 60_000


def test_clearing_waits_for_a_batch_so_a_synced_home_is_not_written_every_call(tmp_path, monkeypatch):
    msgs = [_u("go")] + _rounds(20) + [_a("done")]
    s, cfg = _session(tmp_path, monkeypatch, msgs, ctx_tokens=60_000)
    cli.maintain_context(s, object(), cfg, tmp_path)
    s.ledger.add(usage_mod.Usage(input_tokens=60_000), "gpt-x")  # a new measurement
    s.messages += _rounds(3, start=20)  # 9k chars of newly stale output — under the batch
    cli.maintain_context(s, object(), cfg, tmp_path)
    assert len(list((tmp_path / "sessions" / "archive").glob("*.json"))) == 1
    s.messages += _rounds(8, start=23)
    s.ledger.add(usage_mod.Usage(input_tokens=60_000), "gpt-x")
    cli.maintain_context(s, object(), cfg, tmp_path)
    assert len(list((tmp_path / "sessions" / "archive").glob("*.json"))) == 2


def test_under_the_clear_trigger_nothing_is_touched(tmp_path, monkeypatch):
    msgs = [_u("go")] + _rounds(20) + [_a("done")]
    s, cfg = _session(tmp_path, monkeypatch, msgs, ctx_tokens=30_000)
    before = json.loads(json.dumps(msgs))
    cli.maintain_context(s, object(), cfg, tmp_path)
    assert s.messages == before


def test_a_loaded_skill_and_small_results_are_never_cleared(tmp_path, monkeypatch):
    skill_body = tools._SKILL_HEADER + "planning]\n" + "do this " * 500
    msgs = [_u("go"), _call("s"), {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "s", "content": skill_body}]},
        _call("tiny"), _result("tiny", 50)] + _rounds(20) + [_a("done")]
    s, cfg = _session(tmp_path, monkeypatch, msgs, ctx_tokens=60_000)
    cli.maintain_context(s, object(), cfg, tmp_path)
    assert s.messages[2]["content"][0]["content"] == skill_body
    assert s.messages[4]["content"][0]["content"] == "x" * 50


# ------------------------------------------------------------------- C ----

def _ctx(tmp_path):
    return tools.ToolContext(tmp_path, lambda p: True, lambda a, b, c: None, lambda c: None)


def test_read_output_waits_for_the_job_instead_of_polling(tmp_path):
    import sys
    started = tools._start_background(
        f'"{sys.executable}" -c "import time; time.sleep(0.6); print(\'finished-marker\')"',
        _ctx(tmp_path))
    handle = started.content.split(" as ")[1].split(".")[0]
    t0 = time.monotonic()
    out = tools._read_output({"handle": handle, "wait_seconds": 10}, _ctx(tmp_path))
    assert time.monotonic() - t0 < 5
    assert "finished, exit code 0" in out.content and "finished-marker" in out.content


def test_read_output_with_no_wait_still_reports_a_running_job(tmp_path):
    import sys
    started = tools._start_background(
        f'"{sys.executable}" -c "import time; time.sleep(3)"', _ctx(tmp_path))
    handle = started.content.split(" as ")[1].split(".")[0]
    out = tools._read_output({"handle": handle, "wait_seconds": 0}, _ctx(tmp_path))
    assert "still running" in out.content
    tools._read_output({"handle": handle, "kill": True}, _ctx(tmp_path))
