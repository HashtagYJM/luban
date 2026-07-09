"""v0.5.12 — E17 cumulative changelog span, E18 grep alias, E19 config sync + /config."""
import pytest

from luban import changelog, cli, config as config_mod, tools


# ================= E17: sections_between (cumulative) =================

_SAMPLE = (
    "# luban changelog\n\n"
    "## v0.5.12 — c\n- twelve\n\n"
    "## v0.5.11 — b\n- eleven\n\n"
    "## v0.5.10 — a\n- ten\n\n"
    "## v0.5.9 — z\n- nine\n"
)


def test_sections_between_spans_multiple_versions():
    out = changelog.sections_between("0.5.9", "0.5.12", text=_SAMPLE)
    assert "twelve" in out and "eleven" in out and "ten" in out  # all intermediate
    assert "nine" not in out  # prev is exclusive


def test_sections_between_single_step():
    out = changelog.sections_between("0.5.11", "0.5.12", text=_SAMPLE)
    assert "twelve" in out and "eleven" not in out and "ten" not in out


def test_sections_between_no_prev_gives_only_cur():
    out = changelog.sections_between(None, "0.5.10", text=_SAMPLE)
    assert "ten" in out and "eleven" not in out and "twelve" not in out


def test_real_changelog_has_a_top_version_section():
    import luban
    assert changelog.sections_between("0.0.1", luban.__version__)  # non-empty span


# ================= E18: grep resolves the ~/.luban alias =================

@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "OneDrive" / ".luban"
    (home / "memory").mkdir(parents=True)
    (home / "memory" / "notes.md").write_text("alpha SIGNAL beta", encoding="utf-8")
    (home / "client_local.py").write_text("SECRET_TOKEN = 'sk-xyz'", encoding="utf-8")
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    proj = tmp_path / "proj"; proj.mkdir()
    ctx = tools.ToolContext(project_root=proj, confirm=lambda p: True,
                            render_diff=lambda *a: None, render_command=lambda c: None)
    return home, ctx


def test_grep_resolves_luban_alias(env):
    home, ctx = env
    out = tools.run_tool("grep", {"pattern": "SIGNAL", "path": "~/.luban/memory"}, ctx)
    assert not out.is_error and "SIGNAL" in out.content  # alias resolved, not "Path not found"


def test_grep_never_exposes_luban_py(env):
    home, ctx = env
    out = tools.run_tool("grep", {"pattern": "SECRET_TOKEN", "path": "~/.luban"}, ctx)
    assert "SECRET_TOKEN" not in out.content  # client_local.py content stays hidden


# ================= E19: config sync + /config =================

def _write_old_config(tmp_path, body):
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_keys_detects_absent_toggles(tmp_path):
    p = _write_old_config(tmp_path, 'platform = "windows"\n')
    miss = config_mod.missing_keys(p)
    assert "web_search" in miss and "subagents" in miss and "thinking" in miss


def test_sync_config_appends_commented_and_preserves(tmp_path):
    p = _write_old_config(tmp_path, 'platform = "windows"\nmodel = "my-model"\n')
    added = config_mod.sync_config(p)
    text = p.read_text(encoding="utf-8")
    assert "web_search" in added and "subagents" in added
    assert 'model = "my-model"' in text  # existing value untouched
    assert "# web_search = false" in text  # appended commented
    assert "model" not in added  # already present, not re-added


def test_sync_config_idempotent(tmp_path):
    p = _write_old_config(tmp_path, 'platform = "mac"\n')
    config_mod.sync_config(p)
    assert config_mod.sync_config(p) == []  # second run adds nothing


def test_sync_config_never_mutates_a_set_value(tmp_path):
    p = _write_old_config(tmp_path, 'platform = "mac"\nweb_search = true\n')
    config_mod.sync_config(p)
    assert "web_search = true" in p.read_text(encoding="utf-8")  # kept as-is
    assert "web_search" not in config_mod.missing_keys(p)


def test_slash_config_prints_effective(monkeypatch):
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[],
                    project="p", title="t", thinking=True, effort="xhigh")
    cli.handle_command("/config", s, cfg=config_mod.Config(platform="mac", subagents=True))
    text = "".join(out)
    assert "effort = xhigh" in text and "subagents = True" in text
