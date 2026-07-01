from pathlib import Path
from luban import tools


def _ctx(root: Path, confirm_value: bool):
    return tools.ToolContext(root, lambda p: confirm_value, lambda a, b, c: None, lambda c: None)


def test_run_command_ok(tmp_path):
    out = tools._run_command({"command": "echo hello"}, _ctx(tmp_path, True))
    assert "hello" in out.content
    assert "exit code: 0" in out.content.lower()


def test_run_command_declined(tmp_path):
    out = tools._run_command({"command": "echo hi"}, _ctx(tmp_path, False))
    assert "declined" in out.content.lower()


def test_run_command_nonzero(tmp_path):
    out = tools._run_command({"command": "exit 3"}, _ctx(tmp_path, True))
    assert "exit code: 3" in out.content.lower()


def test_run_command_timeout(tmp_path):
    out = tools._run_command({"command": "sleep 5", "timeout": 1}, _ctx(tmp_path, True))
    assert out.is_error and "timed out" in out.content.lower()
