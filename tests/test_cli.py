from pathlib import Path
from luban import cli


def _session():
    return cli.Session(model="claude-sonnet-5", max_tokens=8192, auto=False, stream=True, messages=[])


def test_parse_args_defaults():
    ns = cli.parse_args([])
    assert ns.model == "claude-sonnet-5"
    assert ns.auto is False
    assert ns.max_tokens == 8192
    assert ns.stream is True


def test_parse_args_flags():
    ns = cli.parse_args(["--auto", "--model", "claude-opus-4-8", "--no-stream", "--max-tokens", "1000"])
    assert ns.auto is True and ns.model == "claude-opus-4-8"
    assert ns.stream is False and ns.max_tokens == 1000


def test_handle_command_auto():
    s = _session()
    assert cli.handle_command("/auto", s) == "handled"
    assert s.auto is True


def test_handle_command_model():
    s = _session()
    assert cli.handle_command("/model claude-opus-4-8", s) == "handled"
    assert s.model == "claude-opus-4-8"


def test_handle_command_clear():
    s = _session()
    s.messages.append({"role": "user", "content": "x"})
    assert cli.handle_command("/clear", s) == "handled"
    assert s.messages == []


def test_handle_command_exit():
    assert cli.handle_command("/exit", _session()) == "exit"


def test_handle_command_not_command():
    assert cli.handle_command("hello there", _session()) == "not_command"


def test_confirm_all_flips_auto(tmp_path, monkeypatch):
    s = _session()
    monkeypatch.setattr(cli.ui, "ask_confirm", lambda prompt, input_fn=input: "all")
    ctx = cli.build_tool_context(s, tmp_path)
    assert ctx.confirm("Write x?") is True
    assert s.auto is True


def test_confirm_auto_mode_autoapproves(tmp_path):
    s = _session()
    s.auto = True
    ctx = cli.build_tool_context(s, tmp_path)
    assert ctx.confirm("Run: rm -rf /tmp/x") is True
