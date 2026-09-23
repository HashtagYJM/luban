import dataclasses

from luban import audit, cli, config, tools


def _session(project="/projA"):
    return cli.Session(model="m", max_tokens=10, auto=True, stream=False, project=project)


def test_build_tool_context_two_arg_compat(tmp_path):
    ctx = cli.build_tool_context(_session(), tmp_path)
    assert ctx.decide is None and ctx.audit is None


def test_deny_rule_blocks_even_under_auto(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    cfg = config.Config(platform="mac", allow=[], deny=["run_command:del *"])
    s = _session()  # auto=True — deny must still win
    ctx = cli.build_tool_context(s, tmp_path, cfg)
    out = tools.run_tool("run_command", {"command": "del everything"}, ctx)
    assert out.is_error and "Blocked" in out.content


def test_allow_rule_end_to_end_writes_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    cfg = config.Config(platform="mac", allow=["write_file:*.txt"], deny=[])
    s = _session(project=str(tmp_path))
    s.auto = False  # prove the RULE (not auto mode) skips the ask

    def never_confirm(prompt: str) -> bool:
        raise AssertionError("no prompt expected: rule-allowed")

    ctx = cli.build_tool_context(s, tmp_path, cfg)
    ctx = dataclasses.replace(ctx, confirm=never_confirm)  # tripwire
    out = tools.run_tool("write_file", {"path": "a.txt", "content": "hi"}, ctx)
    assert not out.is_error
    assert (tmp_path / "a.txt").read_text() == "hi"
    logged = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert '"write_file"' in logged and str(tmp_path) in logged


def test_a_deny_rule_on_the_alias_blocks_the_absolute_spelling(tmp_path, monkeypatch):
    """The documented `write_file:~/.luban/*` deny matched only the raw string the model
    typed, so the absolute spelling of the same file walked past it (R1b)."""
    home = tmp_path / "home"
    (home / "memory").mkdir(parents=True)
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    project = tmp_path / "proj"
    project.mkdir()
    cfg = config.Config(platform="mac", allow=[], deny=["write_file:~/.luban/*"])
    s = _session(project=str(project))  # auto=True: only the rule can stop this
    ctx = cli.build_tool_context(s, project, cfg)
    for spelling in (str(home / "memory" / "x.md"), "~/.luban/memory/x.md"):
        out = tools.run_tool("write_file", {"path": spelling, "content": "no"}, ctx)
        assert out.is_error and "deny rule" in out.content, spelling
    assert not (home / "memory" / "x.md").exists()
    # and a relative allow still covers the absolute spelling of a project file
    cfg = config.Config(platform="mac", allow=["write_file:notes/*"], deny=[])
    s = _session(project=str(project))
    s.auto = False
    ctx = cli.build_tool_context(s, project, cfg)
    ctx = dataclasses.replace(ctx, confirm=lambda p: (_ for _ in ()).throw(AssertionError(p)))
    (project / "notes").mkdir()
    out = tools.run_tool("write_file", {"path": str(project / "notes" / "n.md"),
                                        "content": "ok"}, ctx)
    assert not out.is_error and (project / "notes" / "n.md").read_text() == "ok"
