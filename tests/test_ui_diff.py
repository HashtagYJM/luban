from luban import ui


def test_unified_diff_add_and_remove():
    text = ui.unified_diff_text("f.py", "a\nb\n", "a\nc\n")
    assert "-b" in text and "+c" in text
    assert "f.py" in text


def test_unified_diff_new_file():
    text = ui.unified_diff_text("f.py", "", "hello\n")
    assert "+hello" in text


def test_render_diff_no_ansi_when_not_tty(capsys):
    # Under pytest stdout is captured (not a TTY): color must be suppressed,
    # so no escape codes leak into the output.
    ui.render_diff("f.py", "a\nb\n", "a\nc\n")
    out = capsys.readouterr().out
    assert "\033[" not in out
    assert "-b" in out and "+c" in out


def test_render_command_plain(capsys):
    ui.render_command("ls -la")
    out = capsys.readouterr().out
    assert out.strip() == "$ ls -la"
    assert "\033[" not in out


def test_print_text_passthrough_no_markup_eaten(capsys):
    # Plain stdout write must not interpret brackets (a real risk with markup
    # renderers): model text like "[done]" must survive verbatim.
    ui.print_text("result: [done] (0 errors)")
    assert capsys.readouterr().out == "result: [done] (0 errors)"
