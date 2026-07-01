from luban import ui


def test_ask_confirm_yes():
    assert ui.ask_confirm("go?", input_fn=lambda prompt: "y") == "yes"


def test_ask_confirm_no():
    assert ui.ask_confirm("go?", input_fn=lambda prompt: "n") == "no"


def test_ask_confirm_all():
    assert ui.ask_confirm("go?", input_fn=lambda prompt: "a") == "all"


def test_ask_confirm_default_is_no():
    assert ui.ask_confirm("go?", input_fn=lambda prompt: "") == "no"
