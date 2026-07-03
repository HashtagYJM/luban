from luban import permissions


def test_tool_only_rule_matches_every_call():
    d = permissions.evaluate("run_command", {"command": "anything"}, ["run_command"], [], read_only=False)
    assert d.action == "allow"


def test_pattern_rule_matches_command_glob():
    d = permissions.evaluate("run_command", {"command": "python x.py"}, ["run_command:python *"], [], False)
    assert d.action == "allow"
    assert "python *" in d.reason


def test_pattern_rule_no_match_falls_through_to_ask():
    d = permissions.evaluate("run_command", {"command": "pip install x"}, ["run_command:python *"], [], False)
    assert d.action == "ask"


def test_deny_beats_allow():
    d = permissions.evaluate("run_command", {"command": "del foo"}, ["run_command"], ["run_command:del *"], False)
    assert d.action == "deny"
    assert "del *" in d.reason


def test_default_read_only_allows():
    d = permissions.evaluate("read_file", {"path": "a.py"}, [], [], read_only=True)
    assert d.action == "allow"


def test_default_mutating_asks():
    d = permissions.evaluate("write_file", {"path": "a.py"}, [], [], read_only=False)
    assert d.action == "ask"


def test_deny_on_file_path_pattern():
    d = permissions.evaluate("write_file", {"path": "config/.env"}, [], ["write_file:*.env"], False)
    assert d.action == "deny"


def test_rule_for_other_tool_ignored():
    d = permissions.evaluate("write_file", {"path": "a"}, ["run_command"], [], False)
    assert d.action == "ask"


def test_target_of_mapping():
    assert permissions.target_of("run_command", {"command": "ls"}) == "ls"
    assert permissions.target_of("edit_file", {"path": "f.py"}) == "f.py"
    assert permissions.target_of("glob", {"pattern": "*.py"}) == "*.py"
    assert permissions.target_of("load_skill", {"name": "conv"}) == "conv"
    assert permissions.target_of("unknown_tool", {"x": 1}) == ""
