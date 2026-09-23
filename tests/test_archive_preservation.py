"""R1a — every pre-change history survives a trim, a fold, a save and a reload.

`sessions.archive` named files to second precision, so a trim and a fold in the same
tick wrote the same file twice: the second archive (holding the stub) overwrote the
first (holding the original result), and the stub then pointed into the archive that
held the stub. And an archive that failed to write was followed by the destructive
rewrite anyway, with a success line naming a file that did not exist.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from luban import cli, config as config_mod, sessions as sessions_mod, tools

from tests.test_conversation_fold import _a, _call, _result, _tool_heavy, _u


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, 3, 4, 5)


def _summary_client(monkeypatch, text="SUMMARY"):
    class FB:
        type = "text"
    FB.text = text
    monkeypatch.setattr(cli.client_mod, "create_turn",
                        lambda *a, **k: type("M", (), {"content": [FB()]})())


def _trim_and_fold_session(monkeypatch, tmp_path):
    """A history with one oversized result in the old span and enough else to fold."""
    monkeypatch.setattr(cli, "chars_per_token", lambda *a: 1.0)
    monkeypatch.setattr(cli, "FOLD_MIN_TOKENS", 100)
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(tools, "LUBAN_HOME", tmp_path)
    _summary_client(monkeypatch)
    big = [_u("read the big one"), _call("big"), _result("big", 5_000), _a("read it")]
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                    messages=big + _tool_heavy(40), project=str(tmp_path))
    return s, config_mod.Config(platform="mac", warn_tokens=10_000)


def _archive_refs(messages):
    refs = set()
    for m in messages:
        text = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
        for tok in text.replace("]", " ").replace(",", " ").split():
            if tok.startswith("~/.luban/sessions/archive/"):
                refs.add(tok.rstrip("."))
    return refs


def test_two_archives_in_one_clock_tick_are_two_files(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_mod, "datetime", _Frozen)
    a = sessions_mod.archive({"id": "s1", "messages": [_u("first")]})
    b = sessions_mod.archive({"id": "s1", "messages": [_u("second")]})
    assert a != b and a.exists() and b.exists()
    assert json.loads(a.read_text(encoding="utf-8"))["messages"] == [_u("first")]
    assert json.loads(b.read_text(encoding="utf-8"))["messages"] == [_u("second")]
    assert list((tmp_path / "sessions").glob("*.json")) == []  # never a thread


def test_trim_then_fold_then_reload_keeps_the_original_result(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_mod, "datetime", _Frozen)
    s, cfg = _trim_and_fold_session(monkeypatch, tmp_path)
    original = list(s.messages)
    assert cli.fold_history(s, object(), cfg, tmp_path) is True
    # the session file holds the rewritten window; reload it as /resume would
    data = sessions_mod.load(s.session_id)
    restored = cli.Session(model="m", max_tokens=100, auto=True, stream=False)
    monkeypatch.setattr(cli, "_print_last_exchange", lambda m: None)
    cli.restore_session(restored, data)
    # follow every archive reference the way a reader would: the live marker names the
    # fold's archive, whose stub names the trim's archive, which holds the result
    seen, contents, todo = set(), {}, _archive_refs(restored.messages)
    while todo:
        ref = todo.pop()
        seen.add(ref)
        path = tools.resolve_tool_path(tmp_path, ref)
        assert path.exists(), ref
        contents[ref] = json.loads(path.read_text(encoding="utf-8"))["messages"]
        todo |= _archive_refs(contents[ref]) - seen
    assert len(seen) == 2, seen  # the trim's archive and the fold's — two files
    assert original in contents.values()
    assert any(b.get("content") == "x" * 5_000
               for msgs in contents.values() for m in msgs if isinstance(m["content"], list)
               for b in m["content"] if isinstance(b, dict))


def test_a_failed_archive_write_leaves_history_and_prior_archive_alone(tmp_path, monkeypatch):
    printed = []
    s, cfg = _trim_and_fold_session(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    prior = sessions_mod.archive({"id": "older", "messages": [_u("kept")]})
    calls = []
    monkeypatch.setattr(cli.client_mod, "create_turn", lambda *a, **k: calls.append(1))

    def refuse(data, sessions_dir=None):
        raise OSError("disk full")
    monkeypatch.setattr(sessions_mod, "archive", refuse)
    before = json.loads(json.dumps(s.messages))
    assert cli.fold_history(s, object(), cfg, tmp_path) is False
    assert s.messages == before  # nothing stubbed, nothing folded
    assert not calls  # no model call without a safe copy first
    assert prior.exists() and json.loads(prior.read_text(encoding="utf-8"))["messages"] == [_u("kept")]
    assert not any("✓" in t for t in printed)
    assert any("archive" in t and "unchanged" in t for t in printed), printed
