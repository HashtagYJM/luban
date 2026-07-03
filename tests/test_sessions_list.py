from luban import sessions


def _mk(tmp_path, sid, project, title="t", updated_hint=None):
    sessions.save(
        {
            "id": sid,
            "project": project,
            "created": "2026-07-03T10:00:00",
            "model": "claude-sonnet-5",
            "title": title,
            "messages": [{"role": "user", "content": title}],
        },
        sessions_dir=tmp_path,
    )
    if updated_hint:  # force a deterministic order without sleeping
        import json
        p = tmp_path / f"{sid}.json"
        d = json.loads(p.read_text())
        d["updated"] = updated_hint
        p.write_text(json.dumps(d))


def test_list_filters_by_project(tmp_path):
    _mk(tmp_path, "2026-07-01-0900-aaaa", "/projA")
    _mk(tmp_path, "2026-07-02-0900-bbbb", "/projB")
    got = sessions.list_sessions("/projA", sessions_dir=tmp_path)
    assert [h["id"] for h in got] == ["2026-07-01-0900-aaaa"]


def test_list_none_returns_all(tmp_path):
    _mk(tmp_path, "2026-07-01-0900-aaaa", "/projA")
    _mk(tmp_path, "2026-07-02-0900-bbbb", "/projB")
    assert len(sessions.list_sessions(None, sessions_dir=tmp_path)) == 2


def test_list_newest_first(tmp_path):
    _mk(tmp_path, "2026-07-01-0900-aaaa", "/p", updated_hint="2026-07-01T09:00:00")
    _mk(tmp_path, "2026-07-02-0900-bbbb", "/p", updated_hint="2026-07-02T09:00:00")
    got = sessions.list_sessions("/p", sessions_dir=tmp_path)
    assert [h["id"] for h in got] == ["2026-07-02-0900-bbbb", "2026-07-01-0900-aaaa"]


def test_list_headers_have_message_count_not_messages(tmp_path):
    _mk(tmp_path, "2026-07-01-0900-aaaa", "/p")
    h = sessions.list_sessions("/p", sessions_dir=tmp_path)[0]
    assert h["message_count"] == 1
    assert "messages" not in h


def test_list_skips_corrupt_file(tmp_path, capsys):
    _mk(tmp_path, "2026-07-01-0900-aaaa", "/p")
    (tmp_path / "2026-07-02-0900-bad0.json").write_text("{not json", encoding="utf-8")
    got = sessions.list_sessions("/p", sessions_dir=tmp_path)
    assert len(got) == 1
    assert "skipping" in capsys.readouterr().err


def test_list_missing_dir_is_empty(tmp_path):
    assert sessions.list_sessions("/p", sessions_dir=tmp_path / "absent") == []


def test_latest_returns_full_data(tmp_path):
    _mk(tmp_path, "2026-07-01-0900-aaaa", "/p", updated_hint="2026-07-01T09:00:00")
    _mk(tmp_path, "2026-07-02-0900-bbbb", "/p", title="newer", updated_hint="2026-07-02T09:00:00")
    got = sessions.latest("/p", sessions_dir=tmp_path)
    assert got["id"] == "2026-07-02-0900-bbbb"
    assert got["messages"]  # full data, not just header


def test_latest_none_when_empty(tmp_path):
    assert sessions.latest("/p", sessions_dir=tmp_path) is None
