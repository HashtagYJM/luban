"""v0.5.7 E10: the ~/.luban alias must resolve to the (relocated) LUBAN_HOME, not
the OS home — so the file tools can reach luban's own files when home is synced."""
import pytest

from luban import tools


@pytest.fixture()
def relocated(tmp_path, monkeypatch):
    """OS home and LUBAN_HOME point at DIFFERENT dirs — the setup that broke."""
    os_home = tmp_path / "os_home"
    (os_home / ".luban").mkdir(parents=True)  # empty OS-home .luban (a decoy)
    reloc = tmp_path / "OneDrive" / ".luban"
    reloc.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(os_home))
    monkeypatch.setenv("USERPROFILE", str(os_home))  # Windows ~
    monkeypatch.setattr(tools, "LUBAN_HOME", reloc)
    proj = tmp_path / "proj"
    proj.mkdir()
    return os_home, reloc, proj


def _ctx(proj):
    return tools.ToolContext(
        project_root=proj, confirm=lambda p: True,
        render_diff=lambda p, o, n: None, render_command=lambda c: None,
    )


def test_tilde_alias_resolves_to_relocated_home(relocated):
    os_home, reloc, proj = relocated
    got = tools.resolve_tool_path(proj, "~/.luban/memory/enhancements.md", writing=True)
    assert got == (reloc / "memory" / "enhancements.md").resolve()
    assert str(os_home) not in str(got)  # NOT the OS home


def test_tilde_backslash_form_also_resolves(relocated):
    _, reloc, proj = relocated
    got = tools.resolve_tool_path(proj, r"~\.luban\memory\note.md", writing=True)
    assert got == (reloc / "memory" / "note.md").resolve()


def test_bare_tilde_luban_resolves_to_home_root(relocated):
    _, reloc, proj = relocated
    assert tools.resolve_tool_path(proj, "~/.luban") == reloc.resolve()


def test_write_file_to_relocated_home_end_to_end(relocated):
    _, reloc, proj = relocated
    out = tools.run_tool(
        "write_file",
        {"path": "~/.luban/memory/enh.md", "content": "E10 → fixed 中文"},
        _ctx(proj),
    )
    assert not out.is_error, out.content
    assert (reloc / "memory" / "enh.md").read_text(encoding="utf-8") == "E10 → fixed 中文"


def test_py_guard_still_holds_under_alias(relocated):
    _, reloc, proj = relocated
    with pytest.raises(ValueError):
        tools.resolve_tool_path(proj, "~/.luban/tools_local.py", writing=True)


def test_non_luban_tilde_still_uses_os_home(relocated):
    """~/somewhere-else must NOT be captured by the alias — and stays out of jail."""
    _, reloc, proj = relocated
    with pytest.raises(ValueError):
        tools.resolve_tool_path(proj, "~/Documents/secret.md", writing=True)


def test_default_home_unchanged(tmp_path, monkeypatch):
    """When LUBAN_HOME is NOT relocated, ~/.luban behaves exactly as before."""
    os_home = tmp_path / "h"
    (os_home / ".luban").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(os_home))
    monkeypatch.setenv("USERPROFILE", str(os_home))
    monkeypatch.setattr(tools, "LUBAN_HOME", os_home / ".luban")
    proj = tmp_path / "p"; proj.mkdir()
    got = tools.resolve_tool_path(proj, "~/.luban/SOUL.md")
    assert got == (os_home / ".luban" / "SOUL.md").resolve()
