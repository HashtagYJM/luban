from pathlib import Path

from luban import skills, tools


def _ctx(root: Path) -> tools.ToolContext:
    def no_confirm(prompt: str) -> bool:  # load_skill must never ask
        raise AssertionError("confirm must not be called for load_skill")

    return tools.ToolContext(
        project_root=root, confirm=no_confirm,
        render_diff=lambda *a: None, render_command=lambda *a: None,
    )


def _mk(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


def test_load_skill_returns_body_without_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "proj" / ".luban" / "skills", "conv", "description: c\n\nAlways use uv.")
    out = tools.run_tool("load_skill", {"name": "conv"}, _ctx(tmp_path / "proj"))
    assert not out.is_error
    assert "[skill: conv]" in out.content
    assert "Always use uv." in out.content


def test_load_skill_unknown_lists_available(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "style", "description: s\n\nB.")
    out = tools.run_tool("load_skill", {"name": "nope"}, _ctx(tmp_path / "proj"))
    assert out.is_error
    assert "Unknown skill: nope" in out.content
    assert "style" in out.content


def test_load_skill_unknown_no_skills_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "absent")
    out = tools.run_tool("load_skill", {"name": "x"}, _ctx(tmp_path / "proj"))
    assert out.is_error
    assert "(none)" in out.content


def test_load_skill_in_tools_schema():
    schema = next(t for t in tools.TOOLS if t["name"] == "load_skill")
    assert schema["input_schema"]["required"] == ["name"]
