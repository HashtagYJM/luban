"""E64: the /reflect report joins the conversation. E65: a project's identity can be declared."""
import pytest

from luban import cli, config as config_mod, hooks, memory
from luban import sessions as sessions_mod
from tests.conftest import FakeBlock, FakeClient, FakeMessage

REPORT = "Report\n## Suggested edits\n1. drop X\n2. merge Y"


@pytest.fixture(autouse=True)
def _reset_project(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    (tmp_path / "memory" / "journal").mkdir(parents=True)
    memory.set_project("")
    memory._keys.clear()
    yield
    memory.set_project("")
    memory._keys.clear()


def _cfg():
    return config_mod.Config(platform="mac", memory_enabled=True, auto=True)


def _setup(tmp_path, monkeypatch, scripted):
    monkeypatch.setattr(cli.ui, "print_text", lambda *a, **k: None)
    monkeypatch.setattr(cli.ui, "print_thinking", lambda *a, **k: None, raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    session = cli.Session(model="m", max_tokens=1000, auto=True, stream=False,
                          project=str(proj), session_id="s1", messages=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}])
    cfg = _cfg()
    client = FakeClient(scripted)
    ctx = cli.build_tool_context(session, proj, cfg)
    cli.reflect_session(session, client, ctx, cfg, proj)
    return session, client


def _reported(tmp_path, monkeypatch):
    return _setup(tmp_path, monkeypatch,
                  [FakeMessage([FakeBlock("text", text=REPORT)], "end_turn")])


def test_reflect_report_joins_session_history(tmp_path, monkeypatch):
    session, client = _reported(tmp_path, monkeypatch)
    assert len(session.messages) == 4
    assert session.messages[2] == {"role": "user", "content": "/reflect"}
    last = session.messages[3]
    assert last["role"] == "assistant"
    text = last["content"][0]["text"]
    assert "1. drop X" in text and "2. merge Y" in text and "NOT applied" in text
    assert client.messages.calls[0]["messages"][0] not in session.messages
    assert len(sessions_mod.load(session.session_id)["messages"]) == 4


def test_reflect_failure_adds_nothing(tmp_path, monkeypatch):
    session, _ = _setup(tmp_path, monkeypatch, [])
    assert len(session.messages) == 2


def test_next_turn_sees_the_report(tmp_path, monkeypatch):
    session, _ = _reported(tmp_path, monkeypatch)
    assert any("2. merge Y" in b.get("text", "") for m in session.messages
               if m["role"] == "assistant" and isinstance(m["content"], list)
               for b in m["content"])


def _folder(tmp_path, *parts, **files):
    p = tmp_path.joinpath(*parts)
    p.mkdir(parents=True)
    for name, text in files.items():
        (p / name).write_text(text, encoding="utf-8")
    return p


def test_project_key_defaults_to_folder_name(tmp_path):
    assert memory.project_key(_folder(tmp_path, "a", "_workspace")) == "_workspace"


def test_project_key_reads_declared_name(tmp_path):
    a = _folder(tmp_path, "a", **{"LUBAN.md": "# notes\n<!-- luban-project: alpha -->\nmore\n"})
    b = _folder(tmp_path, "b", **{"CLAUDE.md": "luban-project: beta-proj\n"})
    assert memory.project_key(a) == "alpha"
    assert memory.project_key(b) == "beta-proj"


def test_first_existing_memory_file_wins(tmp_path):
    f = _folder(tmp_path, "f", **{"LUBAN.md": "nothing here\n",
                                  "CLAUDE.md": "luban-project: other\n"})
    assert memory.project_key(f) == "f"


def test_explicit_names_override(tmp_path):
    f = _folder(tmp_path, "f", **{"NOTES.md": "luban-project: n1\n"})
    assert memory.project_key(f, ("NOTES.md",)) == "n1"


def test_pointer_records_folder_and_keeps_it_on_refresh():
    memory.checkpoint("alpha", "next: x", session_id="s1", writer="s1", folder="/p/alpha")
    memory.checkpoint("alpha", "", session_id="s2")
    assert memory.checkpoint_folder("alpha") == "/p/alpha"


def test_same_named_folders_warn_and_declared_names_separate(tmp_path):
    a = _folder(tmp_path, "A", "_workspace")
    b = _folder(tmp_path, "B", "_workspace")
    memory.checkpoint("_workspace", "", "s1", folder=str(a.resolve()))
    note = cli.project_name_notice("_workspace", b.resolve())
    assert str(a.resolve()) in note and "luban-project:" in note
    assert cli.project_name_notice("_workspace", a.resolve()) == ""
    (b / "LUBAN.md").write_text("<!-- luban-project: beta -->", encoding="utf-8")
    memory._keys.clear()
    assert memory.project_key(b) == "beta"
    assert memory.checkpoint_slug("beta") != memory.checkpoint_slug("_workspace")


def test_exit_writes_pointer_under_declared_name(tmp_path):
    folder = _folder(tmp_path, "f", **{"LUBAN.md": "luban-project: alpha\n"})
    session = cli.Session(model="m", max_tokens=100, auto=True, stream=False, session_id="s1",
                          messages=[{"role": "user", "content": "a"},
                                    {"role": "assistant", "content": "b"}])
    cli.exit_journal(session, _cfg(), folder)
    assert "folder: " + str(folder.resolve()) in memory.read_fact(memory.checkpoint_slug("alpha"))
    assert memory.read_fact(memory.checkpoint_slug(folder.name)) is None


def test_hook_payload_and_scope_use_declared_name(tmp_path):
    folder = _folder(tmp_path, "f", **{"LUBAN.md": "luban-project: alpha\n"})
    assert hooks.payload("SessionStart", folder)["project"] == "alpha"
    assert hooks.in_project(hooks.Hook(event="post_tool_use", run="", project="alpha"), folder)
    assert not hooks.in_project(
        hooks.Hook(event="post_tool_use", run="", project="nomatch"), folder)
