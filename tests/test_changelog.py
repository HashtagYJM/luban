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


def test_every_released_version_has_a_heading_of_its_own():
    """Sections are matched by their `## v<version>` heading, so a release without one is
    invisible to the upgrade hook — its notes are silently attributed to whichever heading
    absorbed them, and anyone upgrading across it re-reads changes they already have.

    This happened: v0.5.22's heading was removed when v0.5.23 was written, so four
    subsections of v0.5.22's work were credited to v0.5.23 for two releases.
    """
    import subprocess
    from pathlib import Path
    try:
        tags = subprocess.run(["git", "tag"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parent.parent, timeout=10)
    except (OSError, subprocess.SubprocessError):  # no git, e.g. an installed wheel
        pytest.skip("git not available")
    if tags.returncode != 0:
        pytest.skip("not a git checkout")
    text = Path("luban/CHANGELOG.md").read_text(encoding="utf-8")
    headed = {changelog._parse_ver(m) for m in
              (changelog._VER_HEAD.match(ln) and changelog._VER_HEAD.match(ln).group(1)
               for ln in text.splitlines()) if m}
    # The file starts at the release that introduced it, so anything older is absent by
    # design. A GAP inside the covered range is the defect.
    oldest = min(headed)
    missing = sorted(t for t in tags.stdout.split()
                     if t.startswith("v") and changelog._parse_ver(t[1:])
                     and changelog._parse_ver(t[1:]) > oldest
                     and changelog._parse_ver(t[1:]) not in headed)
    assert not missing, f"released with no changelog heading of its own: {missing}"
