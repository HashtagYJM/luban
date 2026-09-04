"""E46 — a loaded skill lived only in the message list, so every shrink revoked it.

`load_skill` returned the body as an ordinary tool result and nothing recorded that a
skill was in force. Fold and compact replace messages with a model-written summary, so
whether the skill survived was the summarizer's judgement — and when the name went, the
successor ran the work without the method the user's own USER.md makes mandatory.

These tests state the invariant: a load is session state, and the seed a shrink writes
names it VERBATIM, from code, with no summarizer involved.
"""
import json
from pathlib import Path

from conftest import FakeBlock, FakeClient, FakeMessage

from luban import agent, cli, hooks, sessions, skills as skills_mod, tools


def _session(**over):
    kw = dict(model="m", max_tokens=100, auto=True, stream=False, project="/projA",
              messages=[
                  {"role": "user", "content": "run the study"},
                  {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
              ],
              session_id="2026-09-04-1000-abcd", created="2026-09-04T10:00:00",
              title="run the study")
    kw.update(over)
    return cli.Session(**kw)


def _skill(root: Path, name: str, body: str = "Do it this way.") -> None:
    d = root / ".luban" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"description: how to do it\n\n{body}\n", encoding="utf-8")


# ---------------------------------------------------------------- (a) recording ----

def test_loading_a_skill_records_it_on_the_session(tmp_path):
    _skill(tmp_path, "quant-research")
    loaded = []
    ctx = tools.ToolContext(
        project_root=tmp_path,
        confirm=lambda p: True,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
        record_skill=loaded.append,
    )
    out = tools._load_skill({"name": "quant-research"}, ctx)
    assert not out.is_error
    assert loaded == ["quant-research"]


def test_a_failed_load_records_nothing(tmp_path):
    loaded = []
    ctx = tools.ToolContext(
        project_root=tmp_path,
        confirm=lambda p: True,
        render_diff=lambda p, o, n: None,
        render_command=lambda c: None,
        record_skill=loaded.append,
    )
    out = tools._load_skill({"name": "nope"}, ctx)
    assert out.is_error
    assert loaded == []


def test_the_session_keeps_one_entry_per_skill(tmp_path):
    s = _session()
    ctx = cli.build_tool_context(s, tmp_path, None)
    ctx.record_skill("quant-research")
    ctx.record_skill("quant-research")
    ctx.record_skill("wiki-ingest")
    assert s.skills_loaded == ["quant-research", "wiki-ingest"]


# -------------------------------------------------------------- (b) persistence ----

def test_loaded_skills_survive_save_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    s = _session(skills_loaded=["quant-research"])
    cli.save_session(s)
    back = cli.Session(model="m", max_tokens=1, auto=True, stream=False, project="/projA")
    cli.restore_session(back, sessions.load(s.session_id, sessions_dir=tmp_path))
    assert back.skills_loaded == ["quant-research"]


# ------------------------------------------------------------------- (c) shrink ----

def test_compact_seed_names_the_loaded_skills_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    fc = FakeClient([FakeMessage([FakeBlock("text", text="THE SUMMARY")], "end_turn")])
    s = _session(skills_loaded=["quant-research"])
    cli.compact_session(s, fc)
    seed = s.messages[0]["content"]
    assert "quant-research" in seed
    assert "load_skill" in seed, "the successor must be told how to get the body back"
    assert s.skills_loaded == ["quant-research"], "a compact does not un-load a skill"


def test_a_new_thread_is_not_governed_by_the_old_threads_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    s = _session(skills_loaded=["quant-research"])
    cli.handle_command("/new", s)
    assert s.skills_loaded == []


def test_compact_seed_says_nothing_when_no_skill_was_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    fc = FakeClient([FakeMessage([FakeBlock("text", text="THE SUMMARY")], "end_turn")])
    s = _session()
    cli.compact_session(s, fc)
    assert "load_skill" not in s.messages[0]["content"]


def test_the_skill_line_does_not_depend_on_the_summary(tmp_path, monkeypatch):
    """The whole point: the summarizer's output can lose the name, the seed cannot."""
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    fc = FakeClient([FakeMessage([FakeBlock("text", text="a summary mentioning nothing")],
                                 "end_turn")])
    s = _session(skills_loaded=["quant-research"])
    cli.compact_session(s, fc)
    assert "quant-research" in s.messages[0]["content"]


def test_fold_seed_names_the_loaded_skills(tmp_path, monkeypatch):
    seed = cli.loaded_skills_line(["quant-research", "wiki-ingest"])
    assert "quant-research" in seed and "wiki-ingest" in seed
    assert cli.loaded_skills_line([]) == ""


def test_an_oversized_skill_body_is_stubbed_as_a_skill_not_as_output():
    """The third shrink path. A skill body IS a tool result, so the oversized-result stub
    reaches it — and "tool output dropped" is true and useless about an instruction."""
    big = "x" * 500
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "content": f"[skill: quant-research]\n{big}"}]},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    freed = cli.shrink_oversized_results(messages, 100, "sess-1")
    stub = messages[0]["content"][0]["content"]
    assert freed > 0
    assert "quant-research" in stub and "load_skill" in stub
    assert "STILL IN FORCE" in stub


def test_an_ordinary_oversized_result_still_points_at_the_transcript():
    messages = [
        {"role": "user", "content": [{"type": "tool_result", "content": "y" * 500}]},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    cli.shrink_oversized_results(messages, 100, "sess-1")
    assert "sess-1.json" in messages[0]["content"][0]["content"]


def test_the_skill_header_is_parsed_by_the_module_that_writes_it():
    assert tools.skill_result_name("[skill: quant-research]\nbody") == "quant-research"
    assert tools.skill_result_name("ordinary output") == ""


# ------------------------------------------------------------------ (d) /context ----

def test_context_report_names_the_loaded_skills(tmp_path, monkeypatch):
    import luban.config as config_mod
    cfg = config_mod.Config(platform="mac")
    cfg.memory_enabled = False
    s = _session(skills_loaded=["quant-research"])
    text = cli.context_report(s, cfg, tmp_path)
    assert "quant-research" in text


# --------------------------------------------------------------- (e) hook payload ----

def test_the_hook_payload_carries_the_session_id(tmp_path):
    data = hooks.payload("post_tool_use", tmp_path, "load_skill", {"name": "x"},
                         session_id="2026-09-04-1000-abcd")
    assert data["session_id"] == "2026-09-04-1000-abcd"
    env = hooks._environment(data)
    assert env["LUBAN_SESSION_ID"] == "2026-09-04-1000-abcd"


def test_a_payload_without_a_session_id_omits_it(tmp_path):
    data = hooks.payload("session_start", tmp_path)
    assert "session_id" not in data
    assert "LUBAN_SESSION_ID" not in hooks._environment(data)
