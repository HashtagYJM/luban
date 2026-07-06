import luban
from luban import cli, memory


def _mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "m")
    return tmp_path / "m"


def test_nudge_first_run_silent_but_records(tmp_path, monkeypatch):
    mem = _mem(tmp_path, monkeypatch)
    assert cli.version_nudge() == ""
    assert (mem / ".last-version").read_text(encoding="utf-8") == luban.__version__


def test_nudge_on_version_change(tmp_path, monkeypatch):
    mem = _mem(tmp_path, monkeypatch)
    mem.mkdir(parents=True)
    (mem / ".last-version").write_text("0.0.1", encoding="utf-8")
    note = cli.version_nudge()
    assert f"0.0.1 -> {luban.__version__}" in note
    assert "enhancements.md" in note and "Resolved" in note
    assert (mem / ".last-version").read_text(encoding="utf-8") == luban.__version__


def test_nudge_same_version_silent(tmp_path, monkeypatch):
    mem = _mem(tmp_path, monkeypatch)
    mem.mkdir(parents=True)
    (mem / ".last-version").write_text(luban.__version__, encoding="utf-8")
    assert cli.version_nudge() == ""


def test_nudge_never_raises_when_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "m"
    blocker.write_text("a file where the dir should be", encoding="utf-8")
    monkeypatch.setattr(memory, "MEMORY_DIR", blocker / "memory")
    assert cli.version_nudge() == ""  # mkdir fails under a file -> silent


def test_last_version_dotfile_invisible_to_memory(tmp_path, monkeypatch):
    mem = _mem(tmp_path, monkeypatch)
    mem.mkdir(parents=True)
    (mem / ".last-version").write_text("0.0.1", encoding="utf-8")
    assert "0.0.1" not in memory.recall("0.0.1")  # *.md globs skip the dotfile
