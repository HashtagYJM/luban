from luban import skills


def _mk(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


def test_list_global_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "style", "description: my style rules\n\nUse ruff.")
    got = skills.list_skills(tmp_path / "proj")
    assert got == [{"name": "style", "description": "my style rules", "scope": "global"}]


def test_list_project_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "proj" / ".luban" / "skills", "conv", "description: project conventions\n\nBody.")
    got = skills.list_skills(tmp_path / "proj")
    assert got == [{"name": "conv", "description": "project conventions", "scope": "project"}]


def test_project_shadows_global(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "style", "description: global version\n\nG.")
    _mk(tmp_path / "proj" / ".luban" / "skills", "style", "description: project version\n\nP.")
    got = skills.list_skills(tmp_path / "proj")
    assert len(got) == 1
    assert got[0]["description"] == "project version"
    assert got[0]["scope"] == "project"


def test_description_fallback_first_line_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "long", "x" * 200 + "\nrest of body")
    got = skills.list_skills(tmp_path / "proj")
    assert got[0]["description"] == "x" * 80


def test_load_skill_strips_description_line(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "style", "description: my rules\n\nUse ruff.\nLine two.")
    assert skills.load_skill("style", tmp_path / "proj") == "Use ruff.\nLine two."


def test_load_skill_without_description_line_returns_full_text(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "raw", "Just the body.\nMore.")
    assert skills.load_skill("raw", tmp_path / "proj") == "Just the body.\nMore."


def test_load_skill_project_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "style", "description: g\n\nGLOBAL")
    _mk(tmp_path / "proj" / ".luban" / "skills", "style", "description: p\n\nPROJECT")
    assert skills.load_skill("style", tmp_path / "proj") == "PROJECT"


def test_load_skill_unknown_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    assert skills.load_skill("nope", tmp_path / "proj") is None


def test_unreadable_skill_skipped_with_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "good", "description: ok\n\nB.")
    (tmp_path / "g" / "bad.md").mkdir()  # a directory named *.md -> read_text raises
    got = skills.list_skills(tmp_path / "proj")
    assert [s["name"] for s in got] == ["good"]
    assert "skipping" in capsys.readouterr().err


def test_no_skill_dirs_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "absent")
    assert skills.list_skills(tmp_path / "proj") == []
