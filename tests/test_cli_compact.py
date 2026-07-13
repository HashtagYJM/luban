from conftest import FakeBlock, FakeClient, FakeMessage

from luban import cli, sessions


def _session(**over):
    kw = dict(model="m", max_tokens=100, auto=True, stream=False, project="/projA",
              messages=[
                  {"role": "user", "content": "fix the bug"},
                  {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
              ],
              session_id="2026-07-03-1400-abcd", created="2026-07-03T14:00:00",
              title="fix the bug")
    kw.update(over)
    return cli.Session(**kw)


def test_compact_reseeds_and_detaches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    fc = FakeClient([FakeMessage([FakeBlock("text", text="THE SUMMARY")], "end_turn")])
    s = _session()
    cli.compact_session(s, fc)
    # old transcript preserved on disk
    old = sessions.load("2026-07-03-1400-abcd", sessions_dir=tmp_path)
    assert len(old["messages"]) == 2
    # live session reseeded: summary seed + assistant ack
    assert len(s.messages) == 2
    assert "THE SUMMARY" in s.messages[0]["content"]
    assert "compacted from 2026-07-03-1400-abcd" in s.messages[0]["content"]
    assert s.messages[1]["role"] == "assistant"
    # detached to a NEW id, saved immediately, title prefixed
    assert s.session_id and s.session_id != "2026-07-03-1400-abcd"
    new = sessions.load(s.session_id, sessions_dir=tmp_path)
    assert new["title"].startswith("compacted:")
    assert "compacted" in capsys.readouterr().out


def test_compact_api_failure_is_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    fc = FakeClient([])  # scripted empty -> create() raises IndexError
    s = _session()
    before = list(s.messages)
    cli.compact_session(s, fc)
    assert s.messages == before
    assert s.session_id == "2026-07-03-1400-abcd"
    assert "compact failed" in capsys.readouterr().out


def test_compact_empty_summary_is_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    fc = FakeClient([FakeMessage([FakeBlock("text", text="   ")], "end_turn")])
    s = _session()
    before = list(s.messages)
    cli.compact_session(s, fc)
    assert s.messages == before
    assert "compact failed" in capsys.readouterr().out


def test_compact_nothing_to_compact(capsys):
    s = _session(messages=[], session_id="", created="", title="")
    cli.compact_session(s, FakeClient([]))
    assert "nothing to compact" in capsys.readouterr().out


def test_compact_via_handle_command(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    fc = FakeClient([FakeMessage([FakeBlock("text", text="SUM")], "end_turn")])
    s = _session()
    assert cli.handle_command("/compact", s, fc) == "handled"
    assert "SUM" in s.messages[0]["content"]


def test_compact_system_prompt_excludes_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    fc = FakeClient([FakeMessage([FakeBlock("text", text="SUM")], "end_turn")])
    s = _session()
    cli.compact_session(s, fc)
    sent_system = fc.messages.calls[0]["system"]
    assert "LUBAN.md" not in sent_system  # memory & skills deliberately excluded


def test_estimate_tokens_counts_content_not_dict_repr():
    msgs = [{"role": "user", "content": "x" * 400}]
    assert cli.estimate_tokens(msgs) == 100  # 400 chars / 4 — no dict overhead
    assert cli.estimate_tokens([]) == 0


def test_estimate_tokens_walks_content_blocks():
    msgs = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "a" * 40},
            {"type": "tool_use", "id": "t", "name": "grep", "input": {"pattern": "x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "b" * 40},
        ]},
    ]
    est = cli.estimate_tokens(msgs)
    assert 20 <= est <= 40  # the text + args, not the surrounding dict punctuation


def test_estimate_tokens_ignores_dict_keys():
    """The old version str()'d the whole dict, so an empty message still 'cost'
    tokens for its keys/punctuation — inflating the count and tripping /compact early."""
    assert cli.estimate_tokens([{"role": "user", "content": ""}]) == 0


def test_compact_warn_threshold_is_configurable_and_no_longer_60k():
    from luban import config as config_mod
    assert config_mod.Config(platform="mac").warn_tokens == 150_000
    assert cli.DEFAULT_WARN_TOKENS == 150_000
    # and it's user-tunable
    assert config_mod.Config(platform="mac", warn_tokens=500_000).warn_tokens == 500_000
