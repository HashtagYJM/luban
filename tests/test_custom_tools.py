from pathlib import Path

from luban import custom_tools

VALID = '''
def _echo(inp, project_root):
    return f"echo:{inp.get('text', '')} root:{project_root}"

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "handler": _echo,
    },
]
'''


def _write(tmp_path, text, name="tools_local.py"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_no_env_no_default_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("LUBAN_TOOLS_LOCAL", raising=False)
    monkeypatch.setattr(custom_tools, "DEFAULT_PATH", tmp_path / "absent.py")
    assert custom_tools.load_custom_tools() == []


def test_valid_file_via_env(tmp_path, monkeypatch):
    p = _write(tmp_path, VALID)
    monkeypatch.setenv("LUBAN_TOOLS_LOCAL", str(p))
    specs = custom_tools.load_custom_tools()
    assert len(specs) == 1
    assert specs[0]["name"] == "echo"
    assert callable(specs[0]["handler"])
    assert specs[0]["handler"]({"text": "hi"}, Path("/proj")).startswith("echo:hi")


def test_default_path_used_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("LUBAN_TOOLS_LOCAL", raising=False)
    p = _write(tmp_path, VALID)
    monkeypatch.setattr(custom_tools, "DEFAULT_PATH", p)
    assert [s["name"] for s in custom_tools.load_custom_tools()] == ["echo"]


def test_env_set_but_file_missing_warns_and_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LUBAN_TOOLS_LOCAL", str(tmp_path / "gone.py"))
    assert custom_tools.load_custom_tools() == []
    assert "not found" in capsys.readouterr().err


def test_import_error_warns_and_empty(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, "def broken(:\n")
    monkeypatch.setenv("LUBAN_TOOLS_LOCAL", str(p))
    assert custom_tools.load_custom_tools() == []
    assert "could not load" in capsys.readouterr().err


def test_tools_not_a_list_warns_and_empty(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, "TOOLS = {'nope': 1}\n")
    monkeypatch.setenv("LUBAN_TOOLS_LOCAL", str(p))
    assert custom_tools.load_custom_tools() == []
    assert "could not load" in capsys.readouterr().err


def test_missing_tools_attribute_warns_and_empty(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, "x = 1\n")
    monkeypatch.setenv("LUBAN_TOOLS_LOCAL", str(p))
    assert custom_tools.load_custom_tools() == []
    assert "could not load" in capsys.readouterr().err


def test_invalid_entries_dropped_individually(tmp_path, monkeypatch, capsys):
    text = VALID + '''
TOOLS.append("not a dict")
TOOLS.append({"name": "no_handler", "description": "d", "input_schema": {}})
TOOLS.append({"name": "bad name!", "description": "d", "input_schema": {}, "handler": _echo})
TOOLS.append({"name": "bad_schema", "description": "d", "input_schema": "str", "handler": _echo})
TOOLS.append({"name": "empty_desc", "description": "", "input_schema": {}, "handler": _echo})
'''
    p = _write(tmp_path, text)
    monkeypatch.setenv("LUBAN_TOOLS_LOCAL", str(p))
    specs = custom_tools.load_custom_tools()
    assert [s["name"] for s in specs] == ["echo"]
    err = capsys.readouterr().err
    assert err.count("skipping custom tool") == 5
