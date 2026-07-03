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
