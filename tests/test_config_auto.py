"""`auto = true` in config starts sessions in auto mode, like --auto."""
from luban import cli, config as config_mod


def test_the_key_loads_and_defaults_off(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("auto = true\n")
    assert config_mod.load_config(p).auto is True
    p.write_text('auto = "yes"\n')  # not a bool: ignored, never a surprise ON
    assert config_mod.load_config(p).auto is False
    assert config_mod.Config(platform="mac").auto is False
    assert any(k == "auto" for k, _ in config_mod._MIGRATABLE)


def _start(monkeypatch, tmp_path, cfg, argv=()):
    printed, seen = [], []
    monkeypatch.setattr(cli.ui, "print_text", printed.append)
    monkeypatch.setattr(cli, "input", lambda *a: (_ for _ in ()).throw(EOFError), raising=False)
    monkeypatch.setattr(cli.client_mod, "get_client", lambda: object())
    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "setup_custom_tools", lambda: [])
    # main() names the project globally for journal filtering; keep it out of later tests
    monkeypatch.setattr(cli.memory_mod, "set_project", lambda name: None)
    real = cli.build_tool_context
    monkeypatch.setattr(cli, "build_tool_context",
                        lambda s, *a, **k: seen.append(s) or real(s, *a, **k))
    cli.main(["--dir", str(tmp_path), *argv])
    return seen[0], "".join(printed)


def test_config_auto_starts_in_auto_and_says_so(monkeypatch, tmp_path):
    s, out = _start(monkeypatch, tmp_path,
                    config_mod.Config(platform="mac", memory_enabled=False, auto=True))
    assert s.auto is True and "auto: on" in out


def test_without_the_key_or_flag_it_asks(monkeypatch, tmp_path):
    s, out = _start(monkeypatch, tmp_path, config_mod.Config(platform="mac", memory_enabled=False))
    assert s.auto is False and "auto: on" not in out
