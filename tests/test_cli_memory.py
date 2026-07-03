from luban import cli


def test_read_project_memory(tmp_path):
    (tmp_path / "LUBAN.md").write_text("Always run pytest.", encoding="utf-8")
    assert cli.read_project_memory(tmp_path) == "Always run pytest."


def test_read_project_memory_missing(tmp_path):
    assert cli.read_project_memory(tmp_path) == ""


def test_read_project_memory_truncated(tmp_path):
    (tmp_path / "LUBAN.md").write_text("x" * 9000, encoding="utf-8")
    got = cli.read_project_memory(tmp_path)
    assert got.endswith("[memory file truncated]")
    assert len(got) <= cli.MEMORY_MAX_CHARS + 30


def test_read_project_memory_binary_never_crashes(tmp_path):
    (tmp_path / "LUBAN.md").write_bytes(b"\xff\xfe\x00bad bytes \x80")
    got = cli.read_project_memory(tmp_path)  # must not raise
    assert isinstance(got, str)


def test_claude_md_fallback(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("From Claude Code file.", encoding="utf-8")
    assert cli.read_project_memory(tmp_path) == "From Claude Code file."


def test_agents_md_fallback(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Cross-tool standard.", encoding="utf-8")
    assert cli.read_project_memory(tmp_path) == "Cross-tool standard."


def test_chain_order_luban_beats_claude(tmp_path):
    (tmp_path / "LUBAN.md").write_text("luban-specific", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude-generic", encoding="utf-8")
    assert cli.read_project_memory(tmp_path) == "luban-specific"


def test_chain_order_claude_beats_agents(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("claude", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    assert cli.read_project_memory(tmp_path) == "claude"


def test_explicit_memory_file_overrides_chain(tmp_path):
    (tmp_path / "LUBAN.md").write_text("chain-first", encoding="utf-8")
    (tmp_path / "NOTES.md").write_text("explicit choice", encoding="utf-8")
    assert cli.read_project_memory(tmp_path, "NOTES.md") == "explicit choice"


def test_explicit_memory_file_missing_is_empty_no_fallback(tmp_path):
    (tmp_path / "LUBAN.md").write_text("chain-first", encoding="utf-8")
    # explicit choice missing -> "" (no silent fallback to the chain)
    assert cli.read_project_memory(tmp_path, "NOTES.md") == ""
