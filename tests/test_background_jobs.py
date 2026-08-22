"""E35 — background execution: a long run stops holding the session hostage.

The workaround this replaces was a hand-built detached `start /min cmd /c "... > log"`
followed by blind re-reads of the log file. What that bought was non-blocking spawn; what
it cost was any idea of whether the thing was still alive or what it exited with.
"""
import sys
import time

from luban import tools


def _ctx(root, confirm=True):
    return tools.ToolContext(root, lambda p: confirm, lambda a, b, c: None, lambda c: None)


def _drain_until(handle, ctx, want, tries=100):
    """Poll until `want` shows up. The child and the drainer thread are genuinely
    concurrent, so a single read proves nothing either way."""
    seen = ""
    for _ in range(tries):
        seen += tools._read_output({"handle": handle}, ctx).content
        if want in seen:
            return seen
        time.sleep(0.05)
    return seen


def setup_function():
    # Handles are unique for the life of the process — deliberately, so a stale handle
    # from earlier in a session can never address a later job. Tests reset the counter
    # so each can talk about "bg1".
    tools.kill_all_jobs()
    tools._JOB_SEQ = 0


def test_background_returns_a_handle_without_waiting(tmp_path):
    py = sys.executable
    ctx = _ctx(tmp_path)
    started = time.monotonic()
    out = tools._run_command(
        {"command": f'"{py}" -c "import time; time.sleep(20)"', "background": True}, ctx
    )
    assert time.monotonic() - started < 5      # it did NOT wait for the child
    assert "bg1" in out.content
    assert not out.is_error
    assert tools._read_output({"handle": "bg1"}, ctx).content.count("still running") == 1


def test_output_is_readable_while_it_runs_and_reads_are_incremental(tmp_path):
    py = sys.executable
    ctx = _ctx(tmp_path)
    script = tmp_path / "chatty.py"
    script.write_text(
        "import time\n"
        "for i in range(3):\n"
        "    print('line', i, flush=True)\n"
        "    time.sleep(0.2)\n"
    )
    tools._run_command(
        {"command": f'"{py}" "{script}"', "background": True}, ctx
    )
    assert "line 2" in _drain_until("bg1", ctx, "line 2")
    # Everything has been handed over, so a further read repeats nothing.
    again = tools._read_output({"handle": "bg1"}, ctx).content
    assert "line 0" not in again


def test_a_finished_job_reports_its_exit_code(tmp_path):
    ctx = _ctx(tmp_path)
    tools._run_command({"command": "exit 7", "background": True}, ctx)
    seen = _drain_until("bg1", ctx, "exit code 7")
    assert "exit code 7" in seen


def test_kill_terminates_it_and_still_returns_what_it_produced(tmp_path):
    py = sys.executable
    ctx = _ctx(tmp_path)
    src = "import time,sys; print('hello',flush=True); time.sleep(30)"
    tools._run_command({"command": f'"{py}" -c "{src}"', "background": True}, ctx)
    _drain_until("bg1", ctx, "hello")
    out = tools._read_output({"handle": "bg1", "kill": True}, ctx)
    assert not out.is_error
    time.sleep(0.3)
    assert tools._JOBS["bg1"].proc.poll() is not None  # really dead


def test_an_unknown_handle_says_which_ones_exist(tmp_path):
    out = tools._read_output({"handle": "bg99"}, _ctx(tmp_path))
    assert out.is_error and "bg99" in out.content


def test_declining_the_command_starts_nothing(tmp_path):
    out = tools._run_command(
        {"command": "echo nope", "background": True}, _ctx(tmp_path, confirm=False)
    )
    assert "declined" in out.content.lower()
    assert not tools._JOBS


def test_concurrent_jobs_are_capped(tmp_path):
    py = sys.executable
    ctx = _ctx(tmp_path)
    sleeper = f'"{py}" -c "import time; time.sleep(20)"'
    for _ in range(tools.MAX_BACKGROUND_JOBS):
        assert not tools._run_command({"command": sleeper, "background": True}, ctx).is_error
    over = tools._run_command({"command": sleeper, "background": True}, ctx)
    assert over.is_error and "too many" in over.content.lower()


def test_exit_kills_whatever_is_still_running(tmp_path):
    """A job that outlives its session is an orphaned process tree nobody can see —
    the failure this feature is meant to remove, not relocate."""
    py = sys.executable
    ctx = _ctx(tmp_path)
    tools._run_command(
        {"command": f'"{py}" -c "import time; time.sleep(30)"', "background": True}, ctx
    )
    proc = tools._JOBS["bg1"].proc
    killed = tools.kill_all_jobs()
    assert killed == ["bg1"]
    time.sleep(0.3)
    assert proc.poll() is not None


def test_background_is_offered_in_the_tool_schema():
    schema = next(t for t in tools.TOOLS if t["name"] == "run_command")
    assert "background" in schema["input_schema"]["properties"]
    assert any(t["name"] == "read_output" for t in tools.TOOLS)
