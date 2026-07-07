"""The bundled changelog reader (offline upgrade-notes source)."""
import luban
from luban import changelog


def test_read_changelog_ships_and_is_readable():
    text = changelog.read_changelog()
    assert text and "# luban changelog" in text


def test_section_for_current_version_is_present():
    section = changelog.section_for(luban.__version__)
    assert section  # the release being cut must document itself


def test_section_for_extracts_only_that_version():
    sample = (
        "# luban changelog\n\n"
        "## v9.9.9 — newest\n- new thing\n- another\n\n"
        "## v9.9.8 — older\n- old thing\n"
    )
    got = changelog.section_for("9.9.9", text=sample)
    assert "new thing" in got and "another" in got
    assert "old thing" not in got  # stops at the next heading
    assert "## v9.9.9" not in got  # heading line itself excluded


def test_section_for_missing_version_is_empty():
    assert changelog.section_for("0.0.0-nope") == ""


def test_read_never_raises(monkeypatch):
    # even if the resource is missing, callers get "" not an exception
    monkeypatch.setattr(
        changelog.importlib.resources, "files",
        lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError("x")),
    )
    assert changelog.read_changelog() == ""
