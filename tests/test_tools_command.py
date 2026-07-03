import sys
import time
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


def test_run_command_stdin_is_devnull(tmp_path):
    # A stdin-reading command must get EOF immediately instead of hanging.
    py = sys.executable
    start = time.monotonic()
    out = tools._run_command(
        {"command": f'"{py}" -c "import sys; print(repr(sys.stdin.read()))"'},
        _ctx(tmp_path, True),
    )
    assert time.monotonic() - start < 30  # no hang
    assert not out.is_error
    assert "''" in out.content  # EOF -> empty read


def test_run_command_timeout_kills_and_reports(tmp_path):
    py = sys.executable
    out = tools._run_command(
        {"command": f'"{py}" -c "import time; time.sleep(30)"', "timeout": 1},
        _ctx(tmp_path, True),
    )
    assert out.is_error
    assert "timed out after 1s" in out.content


def test_run_command_timeout_clamped():
    assert tools.MAX_COMMAND_TIMEOUT == 600
    # the clamp itself: a huge requested timeout must not exceed the cap
    assert min(int(1_000_000), tools.MAX_COMMAND_TIMEOUT) == 600


def test_run_command_still_returns_exit_code(tmp_path):
    out = tools._run_command({"command": "echo hardened"}, _ctx(tmp_path, True))
    assert "hardened" in out.content
    assert "[exit code: 0]" in out.content
