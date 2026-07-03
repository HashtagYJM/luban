import json
import re

import pytest

from luban import sessions


def _data(sid="2026-07-03-1400-abcd", **over):
    d = {
        "id": sid,
        "project": "/tmp/projA",
        "created": "2026-07-03T00:00:00",
        "model": "claude-sonnet-5",
        "title": "fix the bug",
        "messages": [{"role": "user", "content": "fix the bug"}],
    }
    d.update(over)
    return d


def test_new_session_id_format():
    sid = sessions.new_session_id()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}-[0-9a-f]{4}", sid)


def test_new_session_ids_unique():
    assert sessions.new_session_id() != sessions.new_session_id()


def test_save_load_round_trip(tmp_path):
    path = sessions.save(_data(), sessions_dir=tmp_path)
    assert path == tmp_path / "2026-07-03-1400-abcd.json"
    loaded = sessions.load("2026-07-03-1400-abcd", sessions_dir=tmp_path)
    assert loaded["messages"] == [{"role": "user", "content": "fix the bug"}]
    assert loaded["model"] == "claude-sonnet-5"


def test_save_sets_updated(tmp_path):
    sessions.save(_data(), sessions_dir=tmp_path)
    loaded = sessions.load("2026-07-03-1400-abcd", sessions_dir=tmp_path)
    assert "updated" in loaded and loaded["updated"] >= loaded["created"]


def test_save_is_atomic_no_tmp_residue(tmp_path):
    sessions.save(_data(), sessions_dir=tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_save_creates_dir(tmp_path):
    target = tmp_path / "nested" / "sessions"
    sessions.save(_data(), sessions_dir=target)
    assert (target / "2026-07-03-1400-abcd.json").exists()


def test_save_overwrites_same_id(tmp_path):
    sessions.save(_data(), sessions_dir=tmp_path)
    sessions.save(_data(title="second write"), sessions_dir=tmp_path)
    assert sessions.load("2026-07-03-1400-abcd", sessions_dir=tmp_path)["title"] == "second write"


def test_load_missing_raises(tmp_path):
    with pytest.raises(sessions.SessionNotFound):
        sessions.load("nope", sessions_dir=tmp_path)
