import types
import pytest
from luban import client


def test_default_model():
    assert client.DEFAULT_MODEL == "claude-sonnet-5"


def test_get_client_missing_everywhere(tmp_path, monkeypatch):
    # No env override, no ~/.luban file, no in-package fallback -> friendly error.
    monkeypatch.delenv("LUBAN_CLIENT_LOCAL", raising=False)
    monkeypatch.setattr(client, "USER_CLIENT_PATH", tmp_path / "nope.py")
    monkeypatch.setattr(client, "_in_package_local", lambda: None)
    with pytest.raises(RuntimeError) as exc:
        client.get_client()
    assert "client_local.py" in str(exc.value)


def test_get_client_from_user_path(tmp_path, monkeypatch):
    # A ~/.luban/client_local.py is loaded from its file path.
    f = tmp_path / "client_local.py"
    f.write_text("def build_client():\n    return 'USER_CLIENT'\n")
    monkeypatch.delenv("LUBAN_CLIENT_LOCAL", raising=False)
    monkeypatch.setattr(client, "USER_CLIENT_PATH", f)
    assert client.get_client() == "USER_CLIENT"


def test_get_client_env_override(tmp_path, monkeypatch):
    f = tmp_path / "custom.py"
    f.write_text("def build_client():\n    return 'ENV_CLIENT'\n")
    monkeypatch.setenv("LUBAN_CLIENT_LOCAL", str(f))
    assert client.get_client() == "ENV_CLIENT"


def test_get_client_in_package_fallback(tmp_path, monkeypatch):
    # No env, no user file -> falls back to the in-package module.
    monkeypatch.delenv("LUBAN_CLIENT_LOCAL", raising=False)
    monkeypatch.setattr(client, "USER_CLIENT_PATH", tmp_path / "nope.py")
    fake = types.SimpleNamespace(build_client=lambda: "PKG_CLIENT")
    monkeypatch.setattr(client, "_in_package_local", lambda: fake)
    assert client.get_client() == "PKG_CLIENT"


def test_broken_user_file_surfaces_error(tmp_path, monkeypatch):
    # An import error INSIDE the user's file must surface, not be masked.
    f = tmp_path / "client_local.py"
    f.write_text("import a_package_that_does_not_exist_zzz\n")
    monkeypatch.delenv("LUBAN_CLIENT_LOCAL", raising=False)
    monkeypatch.setattr(client, "USER_CLIENT_PATH", f)
    with pytest.raises(ModuleNotFoundError):
        client.get_client()
