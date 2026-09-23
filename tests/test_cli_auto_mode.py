"""R1c — permission mode is explicit, reversible, and visible.

`/auto off` used to set auto = True: the parser ignored its argument.
"""
import dataclasses

from luban import cli, config as config_mod, tools


def _session(project=""):
    return cli.Session(model="m", max_tokens=10, auto=False, stream=False, project=project)


def test_auto_on_off_and_bare(monkeypatch):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    s = _session()
    assert cli.handle_command("/auto off", s) == "handled" and s.auto is False
    assert cli.handle_command("/auto on", s) == "handled" and s.auto is True
    assert cli.handle_command("/auto off", s) == "handled" and s.auto is False
    assert cli.handle_command("/auto", s) == "handled" and s.auto is True  # compatibility
    assert printed and all("auto: " in t for t in printed)
    assert "off" in printed[0] and "on" in printed[1]


def test_auto_rejects_junk_without_changing_state(monkeypatch):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    s = _session()
    assert cli.handle_command("/auto maybe", s) == "handled"
    assert s.auto is False and "usage" in printed[-1]
    s.auto = True
    cli.handle_command("/auto yes", s)
    assert s.auto is True


def test_prompt_shows_the_mode():
    s = _session()
    assert "auto" not in cli.prompt_line(s)
    s.auto = True
    assert "auto" in cli.prompt_line(s)


def test_confirm_all_says_how_to_undo(tmp_path, monkeypatch):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    monkeypatch.setattr(cli.ui, "ask_confirm", lambda prompt, input_fn=input: "all")
    s = _session()
    ctx = cli.build_tool_context(s, tmp_path)
    assert ctx.confirm("Write x?") is True and s.auto is True
    assert any("/auto off" in t for t in printed)


def test_switching_off_restores_prompting_and_rules_still_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli.ui, "render_diff", lambda *a: None)
    monkeypatch.setattr(cli.ui, "render_command", lambda *a: None)
    asked = []
    monkeypatch.setattr(cli.ui, "ask_confirm",
                        lambda prompt, input_fn=input: asked.append(prompt) or "no")
    cfg = config_mod.Config(platform="mac", allow=["write_file:notes/*"],
                            deny=["write_file:*.env"])
    s = _session(project=str(tmp_path))
    s.auto = True
    ctx = cli.build_tool_context(s, tmp_path, cfg)
    (tmp_path / "notes").mkdir()
    # auto on: an unmatched write lands without a prompt; a deny still blocks
    assert not tools.run_tool("write_file", {"path": "a.txt", "content": "1"}, ctx).is_error
    assert (tmp_path / "a.txt").read_text() == "1"
    out = tools.run_tool("write_file", {"path": "x.env", "content": "k"}, ctx)
    assert out.is_error and not (tmp_path / "x.env").exists()
    assert asked == []
    # off: the next unmatched mutation asks, and a decline leaves the file untouched
    cli.handle_command("/auto off", s)
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "2"}, ctx)
    assert out.is_error is False or "declined" in out.content.lower()
    assert (tmp_path / "a.txt").read_text() == "1"
    assert len(asked) == 1
    out = tools.run_tool("run_command", {"command": "echo hi"}, ctx)
    assert "declined" in out.content.lower() and len(asked) == 2
    # explicit allow keeps its standing consent; deny holds in both modes
    assert not tools.run_tool("write_file", {"path": "notes/n.md", "content": "n"}, ctx).is_error
    assert (tmp_path / "notes" / "n.md").read_text() == "n"
    assert tools.run_tool("write_file", {"path": "y.env", "content": "k"}, ctx).is_error
    assert len(asked) == 2
    # and switching off erased no rule
    assert cfg.allow == ["write_file:notes/*"] and cfg.deny == ["write_file:*.env"]


def test_new_and_resume_do_not_flip_the_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ui, "print_text", lambda t: None)
    monkeypatch.setattr(cli, "_print_last_exchange", lambda m: None)
    s = _session(project=str(tmp_path))
    s.messages = [{"role": "user", "content": "hi"},
                  {"role": "assistant", "content": [{"type": "text", "text": "yo"}]}]
    cli.save_session(s)
    saved_id = s.session_id
    cli.handle_command("/auto off", s)
    cli.handle_command("/new", s)
    assert s.auto is False
    cli.handle_command(f"/resume {saved_id}", s)
    assert s.auto is False and s.session_id == saved_id
    s.auto = True
    cli.handle_command("/new", s)
    assert s.auto is True
