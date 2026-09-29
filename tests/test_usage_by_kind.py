"""Where the calls and tokens went, across sessions.

The gateway reports one monthly total. Whether it came from turns, folds, blank probes or
sub-agents is the question that decides which control to change, and nothing recorded it:
the ledger lives and dies with one session. Every model call now leaves a `model:call`
row in audit.jsonl, and `/usage today` / `/usage 7d` reads them back.
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from luban import agent, audit, cli, usage as usage_mod

from tests.test_archive_preservation import _trim_and_fold_session
from tests.test_empty_turn import Client, _Recording, _ctx, _index_in_tail, _probing_config


def _rows():
    return [json.loads(line) for line in audit.AUDIT_PATH.read_text(encoding="utf-8").splitlines()]


def _session(tmp_path):
    return cli.Session(model="m", max_tokens=100, auto=True, stream=False, project=str(tmp_path))


def test_a_blank_probe_is_booked_as_a_probe_not_a_turn(tmp_path):
    """The probes re-send the full window; counted as turns they would hide inside the
    ordinary traffic they are supposed to be measured against."""
    client = Client([])
    client.messages = _Recording(lambda kw: not _index_in_tail(kw))
    kinds = []
    cfg = _probing_config()
    cfg.on_usage = lambda u, kind: kinds.append(kind)
    agent.run_turn(client, cfg, [{"role": "user", "content": "hi"}], _ctx(tmp_path),
                   lambda t: None, on_empty=lambda m: None, on_probe=lambda *a: None)
    assert kinds == ["turn", "probe"]


def test_a_fold_leaves_a_fold_row_with_what_it_sent(tmp_path, monkeypatch):
    s, cfg = _trim_and_fold_session(monkeypatch, tmp_path)
    usage = SimpleNamespace(input_tokens=1_000, output_tokens=50,
                            cache_creation_input_tokens=0, cache_read_input_tokens=9_000)

    class FB:
        type, text = "text", "SUMMARY"
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: SimpleNamespace(content=[FB()], usage=usage))
    assert cli.fold_history(s, object(), cfg, tmp_path) is True
    calls = [r for r in _rows() if r["tool"] == "model:call"]
    assert len(calls) == 1
    # cache reads are metered in full by the gateway, so they are in `input`
    assert (calls[0]["kind"], calls[0]["input"], calls[0]["output"]) == ("fold", 10_000, 50)
    assert calls[0]["session"] == s.session_id and s.ledger.calls == 1


def test_usage_today_splits_calls_by_kind_and_model_and_skips_older_days(tmp_path, monkeypatch):
    s = _session(tmp_path)
    for kind, model, inp in [("turn", "claude-x", 80_000), ("turn", "claude-x", 90_000),
                             ("turn", "gpt-y", 100_000), ("fold", "claude-x", 30_000),
                             ("probe", "claude-x", 90_000)]:
        cli.record_call(s, usage_mod.Usage(input_tokens=inp, output_tokens=1_000), model, kind)
    audit.log({"tool": "read_file"})
    audit.log({"tool": "read_file"})
    audit.log({"tool": "grep"})
    audit.log({"tool": "model:empty"})
    # yesterday's call is outside "today", inside "2d"
    with audit.AUDIT_PATH.open("a", encoding="utf-8") as f:
        old = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        f.write(json.dumps({"ts": old, "tool": "model:call", "kind": "turn",
                            "target": "claude-x", "input": 7_000_000, "output": 0}) + "\n")
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)

    assert cli.handle_command("/usage today", s) == "handled"
    out = printed[-1]
    assert "5 model calls" in out and "390.0k input" in out
    turn = next(line for line in out.splitlines() if line.strip().startswith("turn"))
    assert turn.split()[1:] == ["3", "270.0k", "69%", "90.0k"]
    fold = next(line for line in out.splitlines() if line.strip().startswith("fold"))
    assert fold.split()[1:3] == ["1", "30.0k"]
    assert "gpt-y" in out and "read_file 2, grep 1" in out and "blank answers 1" in out

    cli.handle_command("/usage 2d", s)
    assert "6 model calls" in printed[-1] and "7.4M input" in printed[-1]


def test_usage_period_without_rows_or_with_a_bad_argument_says_so(tmp_path, monkeypatch):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    s = _session(tmp_path)
    cli.handle_command("/usage today", s)
    assert "no audit log" in printed[-1] or "no model calls recorded" in printed[-1]
    cli.handle_command("/usage lately", s)
    assert printed[-1].startswith("usage: /usage [today | <N>d]")
    assert s.ledger.calls == 0
