from luban import ui


def test_unified_diff_add_and_remove():
    text = ui.unified_diff_text("f.py", "a\nb\n", "a\nc\n")
    assert "-b" in text and "+c" in text
    assert "f.py" in text


def test_unified_diff_new_file():
    text = ui.unified_diff_text("f.py", "", "hello\n")
    assert "+hello" in text
