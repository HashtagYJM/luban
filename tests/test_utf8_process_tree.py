"""E20 + holistic cp1252 root fix: luban forces UTF-8 across its whole process
tree — own streams, the environment children inherit, and the pipes it reads back."""
import os
import re
import sys
from pathlib import Path

import pytest

from luban import cli, tools


@pytest.fixture(autouse=True)
def _restore_env():
    saved = {k: os.environ.get(k) for k in ("PYTHONUTF8", "PYTHONIOENCODING")}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _ctx(tmp_path):
    return tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                             render_diff=lambda *a: None, render_command=lambda c: None)


def test_configure_utf8_io_sets_child_env():
    os.environ.pop("PYTHONUTF8", None)
    cli.configure_utf8_io()
    assert os.environ["PYTHONUTF8"] == "1"
    assert os.environ["PYTHONIOENCODING"] == "utf-8"  # set when absent


def test_run_command_child_inherits_utf8_env(tmp_path):
    cli.configure_utf8_io()  # sets the env children inherit
    cmd = f'"{sys.executable}" -c "import os;print(os.environ.get(chr(80)+chr(89)+chr(84)+chr(72)+chr(79)+chr(78)+chr(85)+chr(84)+chr(70)+chr(56)))"'
    out = tools.run_tool("run_command", {"command": cmd}, _ctx(tmp_path))
    assert out.content.splitlines()[0].strip() == "1"  # child saw PYTHONUTF8=1


def test_run_command_decodes_utf8_child_output(tmp_path):
    cli.configure_utf8_io()
    # child prints an arrow and a bar-chart emoji — the exact chars E20 crashed on
    cmd = f'''"{sys.executable}" -c "import sys;sys.stdout.write('arrow \\u2192 chart \\U0001F4CA end')"'''
    out = tools.run_tool("run_command", {"command": cmd}, _ctx(tmp_path))
    assert "→" in out.content and "\U0001F4CA" in out.content


def test_run_command_popen_pins_utf8_decode():
    """Code guard (anti-whack-a-mole): the child pipe must be decoded as UTF-8,
    not the cp1252 locale default."""
    src = Path(tools.__file__).read_text(encoding="utf-8")
    start = src.index("proc = subprocess.Popen(")
    popen = src[start:start + 600]  # the whole Popen call spans several lines
    assert 'encoding="utf-8"' in popen, "run_command Popen must decode child output as UTF-8"


def test_configure_utf8_io_forces_env_in_source():
    """Guard: the UTF-8 setup must set PYTHONUTF8 so children inherit it."""
    src = Path(cli.__file__).read_text(encoding="utf-8")
    fn = src[src.index("def configure_utf8_io"):]
    fn = fn[: fn.index("\ndef ")]
    assert "PYTHONUTF8" in fn and "reconfigure" in fn
