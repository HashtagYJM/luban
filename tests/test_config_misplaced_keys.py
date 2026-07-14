"""The silent-config bug: --sync-config appended new keys at EOF, and if the file
ended with a [permissions] table, TOML reparented them into it. The setting was then
live, valid, and completely ignored — `effort = "xhigh"` stayed medium for weeks."""
import pytest

from luban import config as config_mod

# The user's real file shape: top-level keys, then a [permissions] table, then the
# keys --sync-config appended at EOF — which TOML swallowed into [permissions].
FIELD_FILE = """\
platform = "windows"
memory_enabled = true
model = "claude-opus-4-8"
web_search = true
subagents = true

[permissions]
allow = ["run_command:python *", "edit_file:*"]

# --- settings added by luban --sync-config (v0.5.11) ---
thinking = true
effort = "xhigh"
thinking_verbose = false
"""


@pytest.fixture()
def cfg_path(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(FIELD_FILE, encoding="utf-8")
    return p


def test_the_bug_reproduces(cfg_path):
    """Before the fix this is what the user saw: xhigh in the file, medium in luban."""
    import tomllib
    raw = tomllib.loads(cfg_path.read_text())
    assert raw["permissions"]["effort"] == "xhigh"  # swallowed by the table
    assert "effort" not in raw                      # …so not a top-level setting
    assert config_mod.load_config(cfg_path).effort == "medium"  # hence: ignored


def test_misplaced_keys_are_detected(cfg_path):
    bad = dict(config_mod.misplaced_keys(cfg_path))
    assert bad == {"thinking": "permissions", "effort": "permissions",
                   "thinking_verbose": "permissions"}


def test_startup_warns_loudly(cfg_path):
    warns = config_mod.config_warnings(cfg_path)
    assert len(warns) == 1
    assert "IGNORED" in warns[0] and "effort" in warns[0] and "--sync-config" in warns[0]


def test_sync_config_repairs_and_the_value_takes_effect(cfg_path):
    touched = config_mod.sync_config(cfg_path)
    assert {"thinking", "effort", "thinking_verbose"} <= set(touched)
    cfg = config_mod.load_config(cfg_path)
    assert cfg.effort == "xhigh"          # the value the user set, finally read
    assert cfg.thinking is True
    assert cfg.allow == ["run_command:python *", "edit_file:*"]  # perms intact
    assert config_mod.misplaced_keys(cfg_path) == []
    assert config_mod.config_warnings(cfg_path) == []


def test_repair_preserves_the_users_values_verbatim(cfg_path):
    config_mod.sync_config(cfg_path)
    text = cfg_path.read_text()
    assert 'effort = "xhigh"' in text  # not reset to a commented default
    assert text.count('effort = "xhigh"') == 1


def test_sync_is_idempotent(cfg_path):
    config_mod.sync_config(cfg_path)
    first = cfg_path.read_text()
    assert config_mod.sync_config(cfg_path) == []  # nothing left to do
    assert cfg_path.read_text() == first


# ---- the root cause: never append below a table header ----

def test_sync_inserts_above_the_table_not_at_eof(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "windows"\n\n[permissions]\nallow = ["x"]\n', encoding="utf-8")
    added = config_mod.sync_config(p)
    assert "effort" in added
    text = p.read_text()
    assert text.index("effort") < text.index("[permissions]")  # above the header
    # and it's a real top-level setting now, not permissions.effort
    assert config_mod.misplaced_keys(p) == []
    assert config_mod.load_config(p).allow == ["x"]


def test_missing_keys_ignores_keys_trapped_in_a_table(cfg_path):
    """The reason re-running --sync-config never helped: the old scan matched the
    raw text anywhere, saw `effort =` under [permissions], and called it present."""
    assert "effort" in config_mod.missing_keys(cfg_path)


def test_file_with_no_table_still_appends(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\n', encoding="utf-8")
    assert "effort" in config_mod.sync_config(p)
    assert config_mod.load_config(p).platform == "mac"


def test_unreadable_config_warns_instead_of_silently_defaulting(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text('platform = "windows"\neffort = "xhigh\n', encoding="utf-8")  # unclosed quote
    cfg = config_mod.load_config(p)
    assert cfg.effort == "medium"  # still doesn't crash
    assert "could not be read" in capsys.readouterr().err  # but says so
