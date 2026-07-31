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


# A release note is PUBLIC, and the user's usage figures are company data every bit as
# much as an employer's name is. Scrubbing for names missed that entirely: three releases
# went out carrying session counts, token totals, cache hit rates and spend measured on a
# real corporate install.
#
# The tell is roundness. luban's own design constants are round (150,000 warn_tokens;
# 38,000 budget; 20,000 MAX_OUTPUT). A measurement is not: 12,957 cached tokens, a
# 29,446-char journal, a 6,810-char profile. So: a thousands-separated number in a public
# release note that does NOT end in at least two zeros is presumed to be field data and
# blocks the push.
#
# Write the mechanism instead of the measurement. "The cached amount stays flat while the
# uncached amount grows" says everything the numbers did, and discloses nothing.
_PUBLIC_PROSE = ("luban/CHANGELOG.md", "README.md", "docs/memory-architecture.md")
_GROUPED_NUM = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")


def find_field_measurements(files: dict) -> list:
    hits = []
    for path, text in files.items():
        if path not in _PUBLIC_PROSE:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in _GROUPED_NUM.finditer(line):
                if not m.group().replace(",", "").endswith("00"):
                    hits.append(f"{path}:{i}: {m.group()}  in: {line.strip()[:70]}")
    return hits


def main() -> int:
    measurements = find_field_measurements(_tracked_files_text())
    if measurements:
        print("LEAK: unrounded figures in public prose — these look like field")
        print("measurements from a real install, which is company usage data:")
        for h in measurements:
            print(f"  - {h}")
        print("Describe the MECHANISM, not the measurement.")
        return 1
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
