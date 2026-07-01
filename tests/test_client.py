import sys
import types
import pytest
from luban import client


def test_default_model():
    assert client.DEFAULT_MODEL == "claude-sonnet-5"


def test_get_client_missing_local(monkeypatch):
    # Ensure no client_local is importable
    monkeypatch.delitem(sys.modules, "luban.client_local", raising=False)
    monkeypatch.setattr(client, "_import_local", lambda: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'luban.client_local'", name="luban.client_local")))
    with pytest.raises(RuntimeError) as exc:
        client.get_client()
    assert "client_local.py" in str(exc.value)


def test_get_client_broken_local_reraises(monkeypatch):
    def boom():
        raise ModuleNotFoundError("No module named 'some_internal_pkg'", name="some_internal_pkg")
    monkeypatch.setattr(client, "_import_local", boom)
    with pytest.raises(ModuleNotFoundError):
        client.get_client()


def test_get_client_uses_local_build(monkeypatch):
    fake_local = types.SimpleNamespace(build_client=lambda: "THE_CLIENT")
    monkeypatch.setattr(client, "_import_local", lambda: fake_local)
    assert client.get_client() == "THE_CLIENT"
