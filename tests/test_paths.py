"""Tests for the relocatable-home resolver (luban.paths) and its wiring."""
import importlib

import pytest

from luban import paths


@pytest.fixture(autouse=True)
def _clear_cache():
    paths.luban_home.cache_clear()
    yield
    paths.luban_home.cache_clear()


def test_default_is_dot_luban(monkeypatch):
    monkeypatch.delenv("LUBAN_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/someone")
    paths.luban_home.cache_clear()
    assert paths.luban_home() == (paths.Path("/home/someone") / ".luban").resolve()


def test_env_override(monkeypatch, tmp_path):
    target = tmp_path / "OneDrive" / "luban"
    target.mkdir(parents=True)
    monkeypatch.setenv("LUBAN_HOME", str(target))
    paths.luban_home.cache_clear()
    assert paths.luban_home() == target.resolve()


def test_env_tilde_expansion(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LUBAN_HOME", "~/synced-luban")
    paths.luban_home.cache_clear()
    assert paths.luban_home() == (tmp_path / "synced-luban").resolve()


def test_result_is_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("LUBAN_HOME", str(tmp_path / "a"))
    paths.luban_home.cache_clear()
    first = paths.luban_home()
    # changing the env after first resolution must NOT change the answer —
    # single source of truth for the whole process
    monkeypatch.setenv("LUBAN_HOME", str(tmp_path / "b"))
    assert paths.luban_home() == first
    paths.luban_home.cache_clear()
    assert paths.luban_home() == (tmp_path / "b").resolve()


def test_env_is_read_only_from_environment(monkeypatch, tmp_path):
    """The home must come from os.environ, never a positional/argument channel."""
    import inspect

    src = inspect.getsource(paths.luban_home)
    assert "os.environ" in src
    # no filesystem-config or project-file reads sneak in
    assert "config" not in src.lower()
    assert "read_text" not in src


def test_relocation_lands_memory_writes_under_new_home(monkeypatch, tmp_path):
    """Integration: with LUBAN_HOME set and caches cleared, a fresh import of the
    modules roots every constant under the new home — nothing under real ~/.luban."""
    new_home = tmp_path / "cloud" / "luban"
    monkeypatch.setenv("LUBAN_HOME", str(new_home))
    paths.luban_home.cache_clear()

    # reimport the path-owning modules so their module-level constants recompute
    from luban import memory, sessions, config, skills, audit, client, custom_tools, tools
    for mod in (memory, sessions, config, skills, audit, client, custom_tools, tools):
        importlib.reload(mod)

    resolved = new_home.resolve()
    assert memory.MEMORY_DIR == resolved / "memory"
    assert memory.SOUL_PATH == resolved / "SOUL.md"
    assert sessions.SESSIONS_DIR == resolved / "sessions"
    assert config.CONFIG_DIR == resolved
    assert skills.GLOBAL_SKILLS_DIR == resolved / "skills"
    assert audit.AUDIT_PATH == resolved / "audit.jsonl"
    assert client.USER_CLIENT_PATH == resolved / "client_local.py"
    assert custom_tools.DEFAULT_PATH == resolved / "tools_local.py"
    assert tools.LUBAN_HOME == resolved

    # actually write a fact and prove it lands under the relocated home
    memory.ensure_scaffold()
    memory.remember("k", "a durable fact", "the body")
    assert (resolved / "memory").is_dir()
    assert any((resolved / "memory").glob("*.md"))

    # restore module state for the rest of the suite
    paths.luban_home.cache_clear()
    for mod in (memory, sessions, config, skills, audit, client, custom_tools, tools):
        importlib.reload(mod)


def test_jail_still_holds_with_relocated_home(monkeypatch, tmp_path):
    """The file-tool jail keys off the resolved home; relocation must not weaken it."""
    from luban import tools

    home = tmp_path / "cloud" / "luban"
    home.mkdir(parents=True)
    monkeypatch.setattr(tools, "LUBAN_HOME", home)
    proj = tmp_path / "proj"
    proj.mkdir()

    # a .py write under the relocated home is still blocked
    with pytest.raises(ValueError):
        tools.resolve_tool_path(proj, str(home / "tools_local.py"), writing=True)
    # a path outside both project and home is still rejected
    with pytest.raises(ValueError):
        tools.resolve_tool_path(proj, str(tmp_path / "elsewhere" / "x.md"), writing=True)
    # a legit memory file under the relocated home is allowed
    ok = tools.resolve_tool_path(proj, str(home / "memory" / "note.md"), writing=True)
    assert ok == (home / "memory" / "note.md").resolve()
