"""Upgrade detection + banner + reconcile directive (v0.5.6 hook).

The .last-version dotfile now lives at the luban-home ROOT (works with memory
off); a legacy memory/.last-version is honored once for migration.
"""
import luban
from luban import changelog, cli, memory, paths


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LUBAN_HOME", str(home))
    paths.luban_home.cache_clear()
    monkeypatch.setattr(memory, "MEMORY_DIR", home / "memory")
    return home


def test_first_run_silent_but_records_at_home_root(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    prev, cur = cli.detect_upgrade()
    assert prev is None and cur == luban.__version__  # first run → no banner
    assert (home / ".last-version").read_text(encoding="utf-8") == luban.__version__


def test_detects_version_change_and_rerecords(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".last-version").write_text("0.0.1", encoding="utf-8")
    prev, cur = cli.detect_upgrade()
    assert prev == "0.0.1" and cur == luban.__version__
    assert (home / ".last-version").read_text(encoding="utf-8") == luban.__version__


def test_same_version_reports_no_change(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".last-version").write_text(luban.__version__, encoding="utf-8")
    prev, cur = cli.detect_upgrade()
    assert prev == cur  # caller treats prev==cur as "no upgrade"


def test_migrates_from_legacy_memory_dotfile(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "memory").mkdir()
    (home / "memory" / ".last-version").write_text("0.4.0", encoding="utf-8")
    prev, _ = cli.detect_upgrade()
    assert prev == "0.4.0"  # seamless transition for existing users
    assert (home / ".last-version").exists()


def test_never_raises_when_unwritable(tmp_path, monkeypatch):
    # point home at a path under a regular file so mkdir/write fail
    blocker = tmp_path / "f"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("LUBAN_HOME", str(blocker / "home"))
    paths.luban_home.cache_clear()
    monkeypatch.setattr(memory, "MEMORY_DIR", blocker / "home" / "memory")
    prev, cur = cli.detect_upgrade()  # must not raise
    assert cur == luban.__version__


def test_survives_non_utf8_state_file(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".last-version").write_bytes(b"\xff\xfe\x00garbage")
    prev, cur = cli.detect_upgrade()  # must not raise
    assert (home / ".last-version").read_text(encoding="utf-8") == luban.__version__


def test_banner_names_versions_and_shows_whats_new():
    section = changelog.section_for(luban.__version__)
    banner = cli.upgrade_banner("0.5.5", section)
    assert "0.5.5" in banner and luban.__version__ in banner
    assert "What's new" in banner


def test_reconcile_directive_targets_the_tracker():
    d = cli.reconcile_directive("0.5.5", changelog.section_for(luban.__version__))
    assert "enhancements.md" in d and "Resolved" in d


def test_dotfile_at_home_root_invisible_to_memory(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "memory").mkdir()
    cli.detect_upgrade()  # writes home/.last-version, not under memory/
    assert luban.__version__ not in memory.recall(luban.__version__)
