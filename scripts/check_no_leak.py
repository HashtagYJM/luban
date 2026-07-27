"""Pre-push guard: fail if internal identifiers appear in tracked files."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Distinctive internal identifiers that must never reach the public repo. "Apollo"
# also covers ApolloOpenAI/ApolloAnthropic. The second group was added after handling
# colleagues' internal study docs — libraries, project names, and credential handles
# that would be damaging to leak and are unlikely to collide with real luban code.
FORBIDDEN = [
    "dimsum_lite", "ApolloAnthropic", "Apollo",
    "bar_library", "bar_toolkit", "gaia_core_data", "rrp_macro",
    "sinnpack", "thepack",
]
# Short acronyms must match as WHOLE WORDS. As a bare substring "UAT" hides inside
# ordinary English — gradUATe, evalUATe, sitUATe — and a guard that cries wolf on
# normal prose is a guard people start ignoring. The long identifiers above stay
# substrings so they still catch ApolloOpenAI, bar_toolkit_x, etc.
FORBIDDEN_WORDS = ["UAT"]
_WORD_RX = re.compile(r"\b(" + "|".join(FORBIDDEN_WORDS) + r")\b")
SELF_EXCLUDE = {"scripts/check_no_leak.py", "tests/test_no_leak.py"}


def find_forbidden(files_text: dict[str, str]) -> list[str]:
    hits = []
    for path, text in files_text.items():
        if any(tok in text for tok in FORBIDDEN) or _WORD_RX.search(text):
            hits.append(path)
    return hits


def _tracked_files_text() -> dict[str, str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    result = {}
    for path in out:
        if path in SELF_EXCLUDE:
            continue
        try:
            result[path] = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return result


def main() -> int:
    hits = find_forbidden(_tracked_files_text())
    if hits:
        print("LEAK: internal identifiers found in tracked files:")
        for h in hits:
            print(f"  - {h}")
        print("Move these into gitignored client_local.py / docs before pushing.")
        return 1
    print("No internal identifiers in tracked files. Safe to push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
