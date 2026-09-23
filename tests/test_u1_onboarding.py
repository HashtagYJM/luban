"""U1 — discoverable commands, a diagnosis instead of a traceback, and prompts that are
one turn however many lines they have."""
import re
from pathlib import Path

import pytest

from luban import cli, client as client_mod, config as config_mod, doctor, paths, ui

README = Path(__file__).resolve().parents[1] / "README.md"


def test_the_readme_table_is_the_command_list():
    text = README.read_text(encoding="utf-8")
    table = text[text.index("### In-session commands"):]
    table = table[:table.index("\n\n", table.index("| "))]
    documented = [re.sub(r"\\\|", "|", m) for m in re.findall(r"^\| `([^`]+)` \|", table, re.M)]
    assert documented == [u for u, _ in cli.COMMANDS]


def test_every_listed_command_is_handled_and_help_lists_them(monkeypatch):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    s = cli.Session(model="m", max_tokens=10, auto=False, stream=False)
    assert cli.handle_command("/help", s) == "handled"
    for usage, _ in cli.COMMANDS:
        assert usage in printed[-1]
    src = Path(cli.__file__).read_text(encoding="utf-8")
    for name in cli._COMMAND_NAMES - {"/retry", "/help"}:
        assert f'"{name}"' in src.split("def handle_command")[1], name


def test_an_unknown_command_says_so_and_a_path_is_a_prompt(monkeypatch):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    s = cli.Session(model="m", max_tokens=10, auto=False, stream=False)
    assert cli.handle_command("/comapct", s) == "handled"
    assert "unknown command: /comapct" in printed[-1] and "/help" in printed[-1]
    assert cli.handle_command("/usr/bin/python fails, why?", s) == "not_command"


def _feed(lines):
    it = iter(lines)
    return lambda prompt="": next(it)


def test_a_paste_is_one_prompt():
    waiting = [True, True, False]
    got = ui.read_prompt("you> ", _feed(["line one", "line two", "line three"]),
                         pending=lambda: waiting.pop(0))
    assert got == "line one\nline two\nline three"


def test_a_typed_block_is_one_prompt_and_a_single_line_is_unchanged():
    got = ui.read_prompt("you> ", _feed(['"""', "first", "", "third", '"""']),
                         pending=lambda: False)
    assert got == "first\n\nthird"
    assert ui.read_prompt("you> ", _feed(["just this"]), pending=lambda: False) == "just this"


# ------------------------------------------------------------------ doctor ----

@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(paths, "luban_home", lambda: h)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", h / "config.toml")
    monkeypatch.setattr(client_mod, "USER_CLIENT_PATH", h / "client_local.py")
    monkeypatch.delenv("LUBAN_CLIENT_LOCAL", raising=False)
    monkeypatch.setattr(client_mod, "_in_package_local", lambda: None)
    return h


def _run(**kw):
    out = []
    code = doctor.run(out=out.append, **kw)
    return code, "\n".join(out)


def test_doctor_names_a_missing_adapter_and_where_the_example_is(home):
    code, text = _run()
    assert code == 1 and "FAIL" in text and "client adapter: none found" in text
    assert "client_local.example.py" in text and str(home / "client_local.py") in text
    assert "connection not tested" in text


def test_doctor_reports_the_placeholder_and_a_broken_import(home):
    (home / "client_local.py").write_text(
        "def build_client():\n    raise NotImplementedError('edit me')\n")
    assert "still the example's placeholder" in _run()[1]
    (home / "client_local.py").write_text("import no_such_company_pkg\n")
    code, text = _run()
    assert code == 1 and "fails to import" in text and "no_such_company_pkg" in text


def test_doctor_reports_a_malformed_config(home):
    (home / "config.toml").write_text("model = \n")
    code, text = _run()
    assert "config does not parse" in text


def test_doctor_passes_offline_and_probes_only_when_asked(home, monkeypatch):
    (home / "client_local.py").write_text(
        "class M:\n    def create(self, **kw): pass\n"
        "class C:\n    messages = M()\n"
        "def build_client():\n    return C()\n")
    sent = []
    monkeypatch.setattr(client_mod, "create_turn", lambda *a, **k: sent.append(k) or type(
        "R", (), {"content": [type("B", (), {"text": "OK"})()]})())
    code, text = _run()
    assert code == 0 and "all checks passed" in text and not sent
    code, text = _run(probe=True, model="m1")
    assert code == 0 and len(sent) == 1 and sent[0]["model"] == "m1" and "'OK'" in text


def test_startup_without_an_adapter_is_a_sentence_not_a_traceback(tmp_path, monkeypatch):
    printed = []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    monkeypatch.setattr(cli.client_mod, "get_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("No client_local.py found.")))
    monkeypatch.setattr(cli.config_mod, "load_config",
                        lambda: config_mod.Config(platform="mac", memory_enabled=False))
    monkeypatch.setattr(cli, "setup_custom_tools", lambda: [])
    monkeypatch.setattr(cli, "detect_upgrade", lambda: (None, "x"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--dir", str(tmp_path)])
    assert exc.value.code == 1
    assert any("luban --doctor" in t and "No client_local.py found" in t for t in printed)


def test_ctrl_c_before_an_answer_keeps_the_prompt_for_retry(tmp_path, monkeypatch):
    """Through the real loop: the prompt used to be dropped with only "[interrupted]"."""
    printed, sent = [], []
    lines = iter(["do the thing", "/retry"])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError
    monkeypatch.setattr(cli, "input", fake_input, raising=False)
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    monkeypatch.setattr(cli.client_mod, "get_client", lambda: object())
    monkeypatch.setattr(cli.config_mod, "load_config",
                        lambda: config_mod.Config(platform="mac", memory_enabled=False))
    monkeypatch.setattr(cli, "setup_custom_tools", lambda: [])
    monkeypatch.setattr(cli, "maintain_context", lambda *a: None)

    def run_turn(client, cfg, messages, *a, **k):
        sent.append([dict(m) for m in messages])
        if len(sent) == 1:
            raise KeyboardInterrupt
        return messages + [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}]
    monkeypatch.setattr(cli.agent, "run_turn", run_turn)
    cli.main(["--dir", str(tmp_path)])
    out = "".join(printed)
    assert "/retry resends your prompt" in out
    assert sent[1][-1] == {"role": "user", "content": "do the thing"}
    assert sum(m["content"] == "do the thing" for m in sent[1]) == 1


def test_piped_input_is_never_joined(monkeypatch):
    class Pipe:
        def isatty(self):
            return False
    monkeypatch.setattr(ui.sys, "stdin", Pipe())
    assert ui.input_pending() is False


def test_an_installed_package_without_an_adapter_says_so(tmp_path):
    """The dev tree has a local adapter file, so the fallback's 'none here' branch never
    ran in tests. In an installed wheel it raised ImportError, not ModuleNotFoundError,
    and startup and --doctor both showed an import error instead of the setup hint."""
    import shutil, subprocess, sys, os
    pkg = Path(cli.__file__).parent
    shutil.copytree(pkg, tmp_path / "luban",
                    ignore=shutil.ignore_patterns("client_local.py", "tools_local.py",
                                                  "__pycache__"))
    code = ("from luban import client, doctor\n"
            "print(client._in_package_local())\n"
            "try:\n    client.get_client()\nexcept RuntimeError as e:\n    print('HINT', e)\n"
            "doctor.run()\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "LUBAN_HOME": str(tmp_path / "home")}
    env.pop("LUBAN_CLIENT_LOCAL", None)
    run = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True,
                         env=env, cwd=tmp_path, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines()[0] == "None"
    assert "HINT No client_local.py found" in run.stdout
    assert "client adapter: none found" in run.stdout
