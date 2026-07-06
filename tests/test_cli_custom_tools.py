import pytest

from luban import cli, tools

VALID = '''
TOOLS = [
    {
        "name": "greet",
        "description": "Say hello.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": lambda inp, root: "hello",
        "read_only": True,
    },
]
'''


@pytest.fixture(autouse=True)
def _isolate():
    yield
    tools.reset_custom()


def test_setup_registers_and_returns_names(tmp_path, monkeypatch):
    p = tmp_path / "tools_local.py"
    p.write_text(VALID, encoding="utf-8")
    monkeypatch.setenv("LUBAN_TOOLS_LOCAL", str(p))
    assert cli.setup_custom_tools() == ["greet"]
    assert "greet" in tools._DISPATCH


def test_setup_no_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("LUBAN_TOOLS_LOCAL", raising=False)
    monkeypatch.setattr(cli.custom_tools_mod, "DEFAULT_PATH", tmp_path / "absent.py")
    assert cli.setup_custom_tools() == []


def test_setup_broken_file_never_raises(tmp_path, monkeypatch, capsys):
    p = tmp_path / "tools_local.py"
    p.write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setenv("LUBAN_TOOLS_LOCAL", str(p))
    assert cli.setup_custom_tools() == []
    assert "could not load" in capsys.readouterr().err
