import json

from luban import audit


def test_log_appends_lines_with_ts(tmp_path):
    p = tmp_path / "audit.jsonl"
    audit.log({"tool": "read_file"}, path=p)
    audit.log({"tool": "run_command"}, path=p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool"] == "read_file"
    assert "ts" in first


def test_log_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "audit.jsonl"
    audit.log({"a": 1}, path=p)
    assert p.exists()


def test_log_never_raises(tmp_path):
    audit.log({"a": 1}, path=tmp_path)  # path IS a directory -> OSError swallowed


def test_log_non_ascii_readable(tmp_path):
    p = tmp_path / "a.jsonl"
    audit.log({"target": "修复.py"}, path=p)
    assert "修复" in p.read_text(encoding="utf-8")
