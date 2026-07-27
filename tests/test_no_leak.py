def test_leak_guard_flags_forbidden(tmp_path):
    # The guard function should detect a forbidden token in provided text.
    from scripts.check_no_leak import find_forbidden
    hits = find_forbidden({"bad.py": "from dimsum_lite import x", "ok.py": "print(1)"})
    assert "bad.py" in hits


def test_leak_guard_clean():
    from scripts.check_no_leak import find_forbidden
    assert find_forbidden({"ok.py": "print('hello')"}) == []


def test_guard_passes_on_own_repo():
    # The guard must return 0 on this repo even though the guard script and
    # this test file contain forbidden tokens as literals (they are self-excluded).
    from scripts.check_no_leak import main
    assert main() == 0


def test_short_acronyms_match_whole_words_only():
    """A bare 3-letter acronym matched as a substring inside ordinary English
    (gradUATe, evalUATe, sitUATe). A guard that cries wolf on prose gets ignored."""
    from scripts.check_no_leak import find_forbidden
    assert find_forbidden({"a.py": "5. GRADUATE the fact"}) == []
    assert find_forbidden({"a.py": "we evaluate the results"}) == []
    assert find_forbidden({"a.py": "situate the code"}) == []
    # a real standalone occurrence is still caught
    assert find_forbidden({"a.py": "env='" + "UAT" + "'"}) == ["a.py"]
