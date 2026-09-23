"""Defect 4: a permission rule must match a call by MEANING, not just by the exact
spelling the model happened to send — the raw target, its absolute path, its
project-relative path, and the ~/.luban alias are the same file.
"""
import pytest

from luban import permissions, tools


def test_evaluate_targets_none_is_raw_only_legacy_behavior():
    """No `targets` kwarg: behaves exactly like matching against tool_input alone."""
    deny = ["write_file:~/.luban/*"]
    tool_input = {"path": "/abs/project/.luban/secret.md"}
    decision = permissions.evaluate("write_file", tool_input, [], deny, read_only=False)
    assert decision.action == "ask"  # raw absolute spelling does not match the alias pattern


def test_evaluate_deny_on_alias_blocks_absolute_spelling():
    deny = ["write_file:~/.luban/*"]
    tool_input = {"path": "/abs/project/.luban/secret.md"}
    targets = ["/abs/project/.luban/secret.md", "~/.luban/secret.md"]
    decision = permissions.evaluate(
        "write_file", tool_input, [], deny, read_only=False, targets=targets
    )
    assert decision.action == "deny"


def test_evaluate_deny_on_alias_blocks_relative_spelling():
    deny = ["write_file:~/.luban/*"]
    tool_input = {"path": "some/relative/path.md"}
    targets = ["some/relative/path.md", "~/.luban/secret.md", "/abs/.luban/secret.md"]
    decision = permissions.evaluate(
        "write_file", tool_input, [], deny, read_only=False, targets=targets
    )
    assert decision.action == "deny"


def test_evaluate_allow_on_relative_pattern_allows_absolute_spelling():
    allow = ["read_file:src/*"]
    tool_input = {"path": "/abs/project/src/x.py"}
    targets = ["/abs/project/src/x.py", "src/x.py"]
    decision = permissions.evaluate(
        "read_file", tool_input, allow, [], read_only=True, targets=targets
    )
    assert decision.action == "allow"


def test_evaluate_deny_still_beats_allow_with_targets():
    allow = ["write_file:*"]
    deny = ["write_file:~/.luban/*"]
    tool_input = {"path": "/abs/project/.luban/secret.md"}
    targets = ["/abs/project/.luban/secret.md", "~/.luban/secret.md"]
    decision = permissions.evaluate(
        "write_file", tool_input, allow, deny, read_only=False, targets=targets
    )
    assert decision.action == "deny"


# --- tools.equivalent_targets ---

def test_equivalent_targets_relative_path(tmp_path):
    (tmp_path / "src").mkdir()
    spellings = tools.equivalent_targets("read_file", {"path": "src/x.py"}, tmp_path)
    assert "src/x.py" in spellings
    assert str((tmp_path / "src" / "x.py").resolve()) in spellings
    assert (tmp_path / "src" / "x.py").resolve().as_posix() in spellings


def test_equivalent_targets_absolute_path_includes_relative_spelling(tmp_path):
    abs_path = str((tmp_path / "src" / "x.py").resolve())
    spellings = tools.equivalent_targets("read_file", {"path": abs_path}, tmp_path)
    assert abs_path in spellings
    assert "src/x.py" in spellings


def test_equivalent_targets_includes_home_alias(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".luban"
    home.mkdir(parents=True)
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    proj = tmp_path / "proj"
    proj.mkdir()
    abs_path = str(home / "memory" / "note.md")
    spellings = tools.equivalent_targets("write_file", {"path": abs_path}, proj)
    assert abs_path in spellings
    assert "~/.luban/memory/note.md" in spellings


def test_equivalent_targets_run_command_returns_raw_only(tmp_path):
    spellings = tools.equivalent_targets("run_command", {"command": "git status"}, tmp_path)
    assert spellings == ["git status"]


def test_equivalent_targets_glob_returns_raw_only(tmp_path):
    spellings = tools.equivalent_targets("glob", {"pattern": "**/*.py"}, tmp_path)
    assert spellings == ["**/*.py"]


def test_equivalent_targets_never_raises_for_refused_path(tmp_path):
    # Escapes the project root — resolve_tool_path would raise; the helper must not.
    spellings = tools.equivalent_targets("read_file", {"path": "../../etc/passwd"}, tmp_path)
    assert spellings == ["../../etc/passwd"]


def test_equivalent_targets_never_raises_missing_path_key(tmp_path):
    spellings = tools.equivalent_targets("read_file", {}, tmp_path)
    assert spellings == [""]
