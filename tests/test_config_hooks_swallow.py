"""A setting written below an ARRAY of tables is swallowed exactly like one written
below a plain table — and must be reported the same way.

`[[hooks]]` was the first array-of-tables luban ever read, and it arrived with the
existing swallow guard blind to that shape: `misplaced_keys` inspected dict-valued
tables only. So `warn_tokens` placed under a hook block was live, valid TOML, part of
the hook entry, completely ignored, and silent — the precise failure the guard exists
to prevent.
"""
from luban import config as config_mod


def _write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_setting_below_a_hooks_block_is_reported(tmp_path):
    path = _write(tmp_path, (
        'platform = "windows"\n'
        '[[hooks]]\n'
        'event = "stop"\n'
        "run = 'echo hi'\n"
        'warn_tokens = 999\n'
    ))
    assert ("warn_tokens", "hooks") in config_mod.misplaced_keys(path)
    warnings = config_mod.config_warnings(path)
    assert warnings and "warn_tokens" in warnings[0]
    # ...and the value really is being ignored, which is why it must be reported.
    assert config_mod.load_config(path).warn_tokens == 150_000


def test_a_correctly_placed_hooks_block_warns_about_nothing(tmp_path):
    path = _write(tmp_path, (
        'platform = "windows"\n'
        'warn_tokens = 999\n'
        '\n'
        '[[hooks]]\n'
        'event = "session_start"\n'
        "run = 'type SKILL.md'\n"
    ))
    assert config_mod.config_warnings(path) == []
    cfg = config_mod.load_config(path)
    assert cfg.warn_tokens == 999
    assert [h.event for h in cfg.hooks] == ["session_start"]


def test_windows_paths_survive_as_toml_literal_strings(tmp_path):
    r"""Single quotes are the practical form on the target box: a TOML basic string
    needs every backslash doubled, and `C:\Users\...` silently becomes an escape
    sequence error or a mangled path."""
    path = _write(tmp_path, (
        'platform = "windows"\n'
        '\n'
        '[[hooks]]\n'
        'event = "session_start"\n'
        "run = 'type C:\\Users\\me\\.luban\\skills\\superpowers\\SKILL.md'\n"
    ))
    cfg = config_mod.load_config(path)
    assert cfg.hooks[0].run == r"type C:\Users\me\.luban\skills\superpowers\SKILL.md"
