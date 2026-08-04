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


# ---------------- field measurements are company data, everywhere ----------------

def test_the_guard_covers_source_and_tests_not_just_release_notes():
    """The first version policed three markdown files, on the assumption that "published"
    meant "release notes". This repo is public: a code comment and a test docstring are as
    published as the CHANGELOG, and that is where most of the leaked figures actually were.
    """
    from scripts.check_no_leak import find_field_measurements
    for path in ("luban/agent.py", "tests/test_usage_accounting.py",
                 "scripts/whatever.py", "docs/memory-architecture.md"):
        assert find_field_measurements({path: "spent 1,319,849 tokens"}), path


def test_it_catches_the_shapes_that_actually_leaked():
    from scripts.check_no_leak import find_field_measurements
    leaked = [
        "Measured: 1,319,849 of 1,954,702 tokens across 63 calls",  # unrounded totals
        "was 103,006 tokens per call, 89.5% of it history",         # decimal percentage
        "a warning per command — 18 lines on a real install",       # round, but usage data
        "E31, measured live: it was 29,446 chars",
    ]
    for line in leaked:
        assert find_field_measurements({"luban/x.py": line}), line


def test_it_does_not_cry_wolf_on_luban_own_constants():
    """A guard that fires on the design constants gets switched off."""
    from scripts.check_no_leak import find_field_measurements
    fine = [
        "ALWAYS_ON_BUDGET = 38,000 chars against a 150,000 working budget",
        "MAX_OUTPUT is 20,000 and RECALL_MAX is 8,000",
        "Opus 4.8 needs ~4,096 tokens to cache anything",   # the API's own minimum
        "hold always-on near 10% of the budget; fold at 70%",
        "a 1h write costs 2x input against 1.25x",
    ]
    for line in fine:
        assert not find_field_measurements({"luban/x.py": line}), line
