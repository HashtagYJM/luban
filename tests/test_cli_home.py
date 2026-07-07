"""Tests for the CLI home helpers: --set-home and the stale-copy notice."""
import types

import pytest

from luban import cli, paths


@pytest.fixture(autouse=True)
def _clear_cache():
    paths.luban_home.cache_clear()
    yield
    paths.luban_home.cache_clear()


# ---- home_notice (stale-copy guard) ----

def test_no_notice_when_env_unset(monkeypatch):
    monkeypatch.delenv("LUBAN_HOME", raising=False)
    paths.luban_home.cache_clear()
    assert cli.home_notice() == ""


def test_notice_shows_active_home_when_relocated(monkeypatch, tmp_path):
    home = tmp_path / "OneDrive" / "luban"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))  # empty default home
    monkeypatch.setenv("LUBAN_HOME", str(home))
    paths.luban_home.cache_clear()
    note = cli.home_notice()
    assert str(home.resolve()) in note
    assert "legacy" not in note  # no default-home data → no warning


def test_notice_warns_about_legacy_data(monkeypatch, tmp_path):
    fake_home = tmp_path / "fakehome"
    legacy = fake_home / ".luban"
    (legacy / "memory").mkdir(parents=True)  # legacy data present
    relocated = tmp_path / "OneDrive" / "luban"
    relocated.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LUBAN_HOME", str(relocated))
    paths.luban_home.cache_clear()
    note = cli.home_notice()
    assert "legacy" in note and str(legacy.resolve()) in note


def test_no_warning_when_env_points_at_default(monkeypatch, tmp_path):
    fake_home = tmp_path / "fakehome"
    (fake_home / ".luban" / "memory").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LUBAN_HOME", str(fake_home / ".luban"))  # same as default
    paths.luban_home.cache_clear()
    assert cli.home_notice() == ""


# ---- set_home ----

def test_set_home_creates_dir_and_prints_export_on_posix(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    target = tmp_path / "OneDrive" / "luban"
    cli.set_home(str(target))
    assert target.exists()
    out = capsys.readouterr().out
    assert "export LUBAN_HOME=" in out and str(target.resolve()) in out


def test_set_home_runs_setx_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=0),
    )
    target = tmp_path / "cloud" / "luban"
    cli.set_home(str(target))
    assert target.exists()
    assert calls and calls[0][:2] == ["setx", "LUBAN_HOME"]
    assert calls[0][2] == str(target.resolve())


def test_set_home_reports_uncreatable_target(monkeypatch, tmp_path, capsys):
    def boom(*a, **k):
        raise OSError("read-only filesystem")
    monkeypatch.setattr(cli.Path, "mkdir", boom)
    cli.set_home(str(tmp_path / "x"))
    assert "could not create" in capsys.readouterr().out


def test_main_set_home_short_circuits(monkeypatch, tmp_path):
    """--set-home must persist and exit without starting the agent loop."""
    called = {"client": False}
    monkeypatch.setattr(cli.client_mod, "get_client",
                        lambda: called.__setitem__("client", True))
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    cli.main(["--set-home", str(tmp_path / "luban")])
    assert called["client"] is False  # never reached client setup
