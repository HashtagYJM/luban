"""Always-on context: cap overruns are LOUD (C1), USER_MAX raised (C2), templates
declare their budget (C3), _HYGIENE teaches write-routing (C4), /config shows the
budget (C5). From the identity-session spec 2026-07-09."""
import pytest

from luban import cli, config as config_mod, memory, tools


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "SOUL_PATH", tmp_path / "SOUL.md")
    monkeypatch.setattr(memory, "USER_PATH", tmp_path / "USER.md")
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    (tmp_path / "memory").mkdir()
    return tmp_path


# ---- C2: the field bug — a real 3,158-char USER.md was silently truncated ----

def test_user_max_is_peer_of_soul_max():
    assert memory.USER_MAX == 4000 == memory.SOUL_MAX


def test_field_bug_3158_char_user_md_now_passes_through_whole(mem):
    profile = "## About me\n" + ("x" * 3_146)  # 3,158 chars total
    assert len(profile) == 3158
    (mem / "USER.md").write_text(profile, encoding="utf-8")
    out = memory.read_user()
    assert "truncated" not in out
    assert len(out) == 3158  # the tail (hard rules / Environment) is no longer dropped


def test_over_cap_still_truncates_and_marks(mem):
    (mem / "USER.md").write_text("## About me\n" + "y" * 5000, encoding="utf-8")
    out = memory.read_user()
    assert "[USER.md truncated]" in out  # caps still exist — they're just visible now


# ---- C1: the human is TOLD, not just the model ----

def test_cap_warning_names_file_size_and_dropped_amount(mem):
    (mem / "USER.md").write_text("## About me\n" + "z" * 5000, encoding="utf-8")
    warns = memory.cap_warnings(memory.always_on_usage())
    assert len(warns) == 1
    w = warns[0]
    # "## About me\n" (12) + 5000 = 5,012 chars; 1,012 over the 4,000 cap
    assert "USER.md" in w and "5,012" in w and "4,000" in w and "1,012" in w
    assert "NOT being sent to the model" in w


def test_no_warning_when_within_cap(mem):
    (mem / "USER.md").write_text("## About me\nshort profile", encoding="utf-8")
    assert memory.cap_warnings(memory.always_on_usage()) == []


def test_always_on_usage_covers_every_layer(mem):
    labels = [lbl for lbl, _, _, _ in memory.always_on_usage()]
    assert labels == ["SOUL.md", "USER.md", "memory index", "journal"]


def test_cli_usage_includes_project_memory_file(tmp_path, mem):
    (tmp_path / "CLAUDE.md").write_text("project rules", encoding="utf-8")
    cfg = config_mod.Config(platform="mac")
    usage = cli.always_on_usage(tmp_path, cfg)
    # head-biased like SOUL/USER, so it IS warnable
    assert ("CLAUDE.md", len("project rules"), cli.MEMORY_MAX_CHARS, True) in usage


# ---- C3: templates declare the budget, and suppression SURVIVES the change ----

def test_templates_state_their_budget():
    assert "4,000 characters" in memory._SOUL_TEMPLATE
    assert "4,000 characters" in memory._USER_TEMPLATE


def test_untouched_scaffold_still_suppressed_after_template_change(mem):
    """The C3 landmine: _is_untouched used to exact-match the template text, so
    editing the template would spray every existing user's scaffold into the prompt."""
    memory.ensure_scaffold()
    assert memory._is_untouched(memory.read_soul()) is True
    assert memory._is_untouched(memory.read_user()) is True
    # an OLDER template (no budget line) must still count as untouched
    old = ("<!-- USER.md — who luban is working with. -->\n\n"
           "## About me\n<!-- your name, role, team -->\n")
    assert memory._is_untouched(old) is True
    block = memory.bootstrap_block()
    assert "About me" not in block  # scaffold not injected as noise


def test_authored_content_is_not_suppressed(mem):
    (mem / "USER.md").write_text("## About me\nI am a quant.", encoding="utf-8")
    assert memory._is_untouched(memory.read_user()) is False
    assert "I am a quant." in memory.bootstrap_block()


# ---- C4: write-routing ----

def test_hygiene_teaches_write_routing():
    h = memory._HYGIENE
    assert "WHERE TO WRITE" in h
    assert "EDIT USER.md" in h and "SOUL.md" in h and "a skill" in h
    assert "NEVER store always-on behavior as a recallable fact" in h


def test_bootstrap_order_unchanged(mem):
    (mem / "SOUL.md").write_text("## How I should work\nbe terse", encoding="utf-8")
    (mem / "USER.md").write_text("## About me\nquant", encoding="utf-8")
    memory.remember("k", "d", "body")
    block = memory.bootstrap_block()
    assert block.index("be terse") < block.index("quant") < block.index("Long-term memory index")


# ---- C5: /config shows the budget ----

def test_slash_config_shows_always_on_budget(tmp_path, mem, monkeypatch):
    (mem / "USER.md").write_text("## About me\n" + "q" * 5000, encoding="utf-8")
    out = []
    monkeypatch.setattr(cli.ui, "print_text", lambda t: out.append(t))
    s = cli.Session(model="m", max_tokens=100, auto=True, stream=False, messages=[],
                    project="p", title="t")
    ctx = tools.ToolContext(project_root=tmp_path, confirm=lambda p: True,
                            render_diff=lambda *a: None, render_command=lambda c: None)
    cli.handle_command("/config", s, ctx=ctx, cfg=config_mod.Config(platform="mac"))
    text = "".join(out)
    assert "always-on context" in text and "USER.md: 5,012/4,000" in text
    assert "OVER CAP" in text
