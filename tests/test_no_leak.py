import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_leak_guard_flags_forbidden(tmp_path):
    # The guard function should detect a forbidden token in provided text.
    from scripts.check_no_leak import find_forbidden
    hits = find_forbidden({"bad.py": "from dimsum_lite import x", "ok.py": "print(1)"})
    assert "bad.py" in hits


def test_leak_guard_clean():
    from scripts.check_no_leak import find_forbidden
    assert find_forbidden({"ok.py": "print('hello')"}) == []
