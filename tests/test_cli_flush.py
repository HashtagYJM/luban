import types

import pytest

from luban import cli, config as config_mod, memory, tools


class _Stub:
    """Records the tool schemas offered on each create() call; never calls remember."""
    def __init__(self, script):
        self.script = script
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        step = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        return types.SimpleNamespace(content=step, stop_reason="end_turn")


def _text(t):
    return types.SimpleNamespace(type="text", text=t)


def _ctx(tmp_path):
    return tools.ToolContext(
        project_root=tmp_path,
        confirm=lambda p: True,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
    )


def _session(msgs):
    return cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       messages=msgs, project="proj", title="t")


def _cfg():
    return config_mod.Config(platform="mac")


@pytest.fixture(autouse=True)
def _mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")


def test_flush_offers_only_journal_tool(tmp_path):
    stub = _Stub([[_text("saved.")]])
    sess = _session([{"role": "user", "content": "hi"}])
    cli.flush_memory(sess, stub, _ctx(tmp_path), _cfg())
    offered = {t["name"] for t in stub.calls[0]["tools"]}
    assert offered == {"journal"}, offered  # remember/forget/recall NOT offered


def test_flush_prompt_has_no_remember_instruction():
    assert "remember" not in cli.FLUSH_PROMPT.lower()
    assert "journal" in cli.FLUSH_PROMPT.lower()


def test_flush_without_journal_call_leaves_flag_false(tmp_path):
    stub = _Stub([[_text("saved.")]])
    sess = _session([{"role": "user", "content": "hi"}])
    assert sess.journaled is False
    cli.flush_memory(sess, stub, _ctx(tmp_path), _cfg())
    assert sess.journaled is False


def test_flush_with_journal_call_sets_flag(tmp_path):
    def _tool(id_, name, inp):
        return types.SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)
    script = [
        [_tool("j1", "journal", {"text": "did stuff; decided X; next Y"})],
        [_text("saved.")],
    ]
    class _S:
        def __init__(self): self.n = 0; self.messages = self
        def create(self, **kw):
            self.n += 1
            if self.n == 1:
                return types.SimpleNamespace(content=script[0], stop_reason="tool_use")
            return types.SimpleNamespace(content=script[1], stop_reason="end_turn")
    sess = _session([{"role": "user", "content": "hi"}])
    cli.flush_memory(sess, _S(), _ctx(tmp_path), _cfg())
    assert sess.journaled is True
    # and the journal file actually got the entry
    files = list((tmp_path / "memory" / "journal").glob("*.md"))
    assert files and "did stuff" in files[0].read_text(encoding="utf-8")


def test_second_flush_is_skipped(tmp_path):
    def _tool(id_, name, inp):
        return types.SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)
    script = [
        [_tool("j1", "journal", {"text": "did stuff"})],
        [_text("saved.")],
    ]
    class _S:
        def __init__(self): self.n = 0; self.calls = []; self.messages = self
        def create(self, **kw):
            self.calls.append(kw)
            self.n += 1
            if self.n == 1:
                return types.SimpleNamespace(content=script[0], stop_reason="tool_use")
            return types.SimpleNamespace(content=script[1], stop_reason="end_turn")
    sess = _session([{"role": "user", "content": "hi"}])
    stub = _S()
    cli.flush_memory(sess, stub, _ctx(tmp_path), _cfg())
    n = len(stub.calls)
    cli.flush_memory(sess, stub, _ctx(tmp_path), _cfg())  # already journaled
    assert len(stub.calls) == n  # no second model call


def test_exit_journal_skipped_when_already_flushed(tmp_path):
    sess = _session([{"role": "user", "content": "hi"}])
    sess.journaled = True
    cli.exit_journal(sess, _cfg(), tmp_path)
    jdir = tmp_path / "memory" / "journal"
    assert not jdir.exists() or not any(jdir.iterdir())  # no mechanical line added


def test_exit_journal_writes_when_never_flushed(tmp_path):
    sess = _session([{"role": "user", "content": "hi"}])
    assert sess.journaled is False
    cli.exit_journal(sess, _cfg(), tmp_path)
    files = list((tmp_path / "memory" / "journal").glob("*.md"))
    assert files
    # exit_journal uses the basename of project_root, not session.project
    text = files[0].read_text(encoding="utf-8")
    assert f"[{tmp_path.name}]" in text


def test_flush_failure_does_not_set_flag_or_crash(tmp_path):
    class _Boom:
        def __init__(self): self.messages = self
        def create(self, **kw): raise RuntimeError("model down")
    sess = _session([{"role": "user", "content": "hi"}])
    cli.flush_memory(sess, _Boom(), _ctx(tmp_path), _cfg())  # must not raise
    assert sess.journaled is False  # exit_journal will still cover the session


def test_hygiene_mentions_journal_vs_facts():
    assert "The journal is for what happened; facts are for what stays true." in memory._HYGIENE


def test_exit_journal_uses_project_basename(tmp_path):
    sess = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       messages=[{"role": "user", "content": "hi"}],
                       project="/Users/alice/Developer/luban", title="t")
    cli.exit_journal(sess, config_mod.Config(platform="mac"),
                     "/Users/alice/Developer/luban")
    files = list((tmp_path / "memory" / "journal").glob("*.md"))
    assert files, "expected journal file to be created"
    text = files[0].read_text(encoding="utf-8")
    assert "[luban]" in text and "/Users/alice" not in text


def test_compact_resets_journaled_flag():
    # after a compaction reseed, journaled must be False so the next segment journals
    sess = cli.Session(model="m", max_tokens=100, auto=True, stream=False,
                       messages=[{"role": "user", "content": "hi"}], project="proj", title="t")
    sess.journaled = True
    # minimal fake client whose create returns a text summary
    import types
    class _C:
        def __init__(self): self.messages = self
        def create(self, **kw):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="a summary")],
                stop_reason="end_turn")
    cli.compact_session(sess, _C())  # 2-arg form: no flush, just summarize+reseed
    assert sess.journaled is False
