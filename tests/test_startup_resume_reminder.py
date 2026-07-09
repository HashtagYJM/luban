"""Plain-start reminder that a prior session exists, and opt-in auto_continue."""
import pytest

from luban import cli, config as config_mod, sessions as sessions_mod


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_mod, "SESSIONS_DIR", tmp_path / "sessions")
    return tmp_path


def _save_a_session(project, title="prior work", messages=None):
    return sessions_mod.save({
        "id": sessions_mod.new_session_id(), "project": project,
        "created": "2026-07-09T10:00:00", "model": "m", "title": title,
        "messages": messages or [{"role": "user", "content": "hi"},
                                 {"role": "assistant", "content": [{"type": "text", "text": "hey"}]}],
    })


def _run_main(monkeypatch, home, argv, cfg):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: printed.append(t))
    monkeypatch.setattr(cli, "input", lambda *a: (_ for _ in ()).throw(EOFError), raising=False)
    monkeypatch.setattr(cli.client_mod, "get_client", lambda: object())
    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "setup_custom_tools", lambda: [])
    monkeypatch.setattr(cli.memory_mod, "ensure_scaffold", lambda: None)
    cli.main(argv)
    return "".join(printed)


def test_plain_start_reminds_of_recent_session(monkeypatch, home):
    proj = str(home / "proj")
    _save_a_session(proj)
    out = _run_main(monkeypatch, home, ["--dir", proj], config_mod.Config(platform="mac", memory_enabled=False))
    assert "recent session is saved here" in out and "prior work" in out and "luban -c" in out


def test_plain_start_no_reminder_when_no_session(monkeypatch, home):
    proj = str(home / "empty")
    out = _run_main(monkeypatch, home, ["--dir", proj], config_mod.Config(platform="mac", memory_enabled=False))
    assert "recent session" not in out


def test_auto_continue_reopens_instead_of_reminding(monkeypatch, home):
    proj = str(home / "proj")
    _save_a_session(proj, title="resume me")
    cfg = config_mod.Config(platform="mac", memory_enabled=False, auto_continue=True)
    out = _run_main(monkeypatch, home, ["--dir", proj], cfg)
    assert "resumed" in out and "resume me" in out  # restore_session ran
    assert "set auto_continue = true" not in out    # not the reminder path


def test_reminder_suppressed_when_using_continue_flag(monkeypatch, home):
    proj = str(home / "proj")
    _save_a_session(proj)
    out = _run_main(monkeypatch, home, ["--dir", proj, "-c"], config_mod.Config(platform="mac", memory_enabled=False))
    assert "recent session is saved here" not in out  # -c path, not the reminder


def test_auto_continue_config_default_off_and_migratable():
    assert config_mod.Config(platform="mac").auto_continue is False
    assert any(k == "auto_continue" for k, _ in config_mod._MIGRATABLE)
