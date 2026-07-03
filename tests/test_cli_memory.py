from luban import cli


def test_read_project_memory(tmp_path):
    (tmp_path / "LUBAN.md").write_text("Always run pytest.", encoding="utf-8")
    assert cli.read_project_memory(tmp_path) == "Always run pytest."


def test_read_project_memory_missing(tmp_path):
    assert cli.read_project_memory(tmp_path) == ""


def test_read_project_memory_truncated(tmp_path):
    (tmp_path / "LUBAN.md").write_text("x" * 9000, encoding="utf-8")
    got = cli.read_project_memory(tmp_path)
    assert got.endswith("[LUBAN.md truncated]")
    assert len(got) <= cli.MEMORY_MAX_CHARS + 30


def test_read_project_memory_binary_never_crashes(tmp_path):
    (tmp_path / "LUBAN.md").write_bytes(b"\xff\xfe\x00bad bytes \x80")
    got = cli.read_project_memory(tmp_path)  # must not raise
    assert isinstance(got, str)
