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


def _mk_folder(directory, name, text):
    d = directory / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")


FRONT = '---\nname: quant_research\ndescription: "Systematic equity research helpers."\n---\n\nUse the internal data loaders.\n'


def test_folder_skill_discovered_with_frontmatter_description(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk_folder(tmp_path / "g", "quant_research", FRONT)
    got = skills.list_skills(tmp_path / "proj")
    assert got == [{
        "name": "quant_research",
        "description": "Systematic equity research helpers.",
        "scope": "global",
    }]


def test_frontmatter_description_capped_at_240(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk_folder(tmp_path / "g", "long", f"---\ndescription: {'d' * 400}\n---\nBody.")
    got = skills.list_skills(tmp_path / "proj")
    assert got[0]["description"] == "d" * 240


def test_frontmatter_without_description_falls_back_to_body_line(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk_folder(tmp_path / "g", "nodesc", "---\nname: nodesc\n---\n\nFirst body line.\nMore.")
    got = skills.list_skills(tmp_path / "proj")
    assert got[0]["description"] == "First body line."


def test_flat_file_wins_over_folder_same_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "dup", "description: flat wins\n\nFLAT")
    _mk_folder(tmp_path / "g", "dup", "---\ndescription: folder\n---\nFOLDER")
    got = skills.list_skills(tmp_path / "proj")
    assert len(got) == 1 and got[0]["description"] == "flat wins"
    assert skills.load_skill("dup", tmp_path / "proj") == "FLAT"


def test_load_folder_skill_prepends_folder_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk_folder(tmp_path / "g", "quant_research", FRONT)
    body = skills.load_skill("quant_research", tmp_path / "proj")
    assert body.startswith("(Skill folder: ")
    assert str(tmp_path / "g" / "quant_research") in body
    assert "Use the internal data loaders." in body


def test_project_folder_skill_shadows_global(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk_folder(tmp_path / "g", "s", "---\ndescription: global\n---\nG")
    _mk_folder(tmp_path / "proj" / ".luban" / "skills", "s", "---\ndescription: project\n---\nP")
    got = skills.list_skills(tmp_path / "proj")
    assert got[0]["scope"] == "project" and got[0]["description"] == "project"
    assert skills.load_skill("s", tmp_path / "proj").endswith("P")


def test_load_skill_rejects_traversal_names(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    (tmp_path / "secret.md").write_text("description: s\n\nSECRET", encoding="utf-8")
    for bad in ("../secret", "a/b", "a\\b", "..", "", "d:foo", "C:evil", "x:"):
        assert skills.load_skill(bad, tmp_path / "proj") is None


def test_load_skill_rejects_colon_names_before_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    _mk(tmp_path / "g", "d:foo", "description: s\n\nBODY")  # legal filename on POSIX
    assert skills.load_skill("d:foo", tmp_path / "proj") is None


def test_non_utf8_skill_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", tmp_path / "g")
    (tmp_path / "g").mkdir(parents=True)
    (tmp_path / "g" / "ansi.md").write_bytes(b"description: caf\xe9 rules\n\nBody")
    got = skills.list_skills(tmp_path / "proj")
    assert len(got) == 1 and got[0]["name"] == "ansi"
    assert skills.load_skill("ansi", tmp_path / "proj") is not None
