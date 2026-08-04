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
# 38,000 budget; 20,000 MAX_OUTPUT). A measurement is not — a cached-token count, a journal
# size, a profile size all land wherever they land. So: a thousands-separated number that
# does NOT end in at least two zeros is presumed to be field data and blocks the push.
#
# Write the mechanism instead of the measurement. "The cached amount stays flat while the
# uncached amount grows" says everything the numbers did, and discloses nothing.
#
# EVERY TRACKED FILE, not a list of prose files. The first version of this check policed
# three markdown files, on the assumption that "published" meant "release notes". It does
# not: this repo is public, so a code comment and a test docstring are exactly as published
# as the CHANGELOG — and that is where most of the leaked figures actually were. Any list
# of "the public files" will be wrong again the next time a file is added. The tracked set
# IS the published set.
_GROUPED_NUM = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")
# Numbers that are genuinely luban's or the API's, and happen not to be round.
_NOT_MEASUREMENTS = {"4,096"}          # Anthropic's minimum cacheable prefix
# A whole-percent figure is usually a design choice (70% of warn_tokens, ~10% of budget).
# A percentage carried to a DECIMAL is somebody reading it off a real install.
_DECIMAL_PCT = re.compile(r"\b\d+\.\d+\s*%")
# Prose that announces the number beside it came from a real install. The number itself may
# be perfectly round and still be usage data — a call count, a line count, a day count.
_FROM_A_REAL_INSTALL = re.compile(
    r"(on a real install|measured live|in the field (?:it |we |the )?(?:was|found|showed)"
    r"|field measurement (?:found|showed)|measured across|measured:)", re.I)
_SKIP = ("luban/prices.json", "uv.lock")


def find_field_measurements(files: dict) -> list:
    hits = []
    for path, text in files.items():
        if path in _SKIP:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            def hit(what):
                hits.append(f"{path}:{i}: {what}  in: {line.strip()[:70]}")
            for m in _GROUPED_NUM.finditer(line):
                if (m.group() not in _NOT_MEASUREMENTS
                        and not m.group().replace(",", "").endswith("00")):
                    hit(m.group())
            for m in _DECIMAL_PCT.finditer(line):
                hit(m.group())
            m = _FROM_A_REAL_INSTALL.search(line)
            if m and re.search(r"\d", line):
                hit(f'"{m.group()}" beside a figure')
    return hits


def main() -> int:
    measurements = find_field_measurements(_tracked_files_text())
    if measurements:
        print("LEAK: these look like field measurements from a real install, which is")
        print("company usage data. Every tracked file is published — a code comment and")
        print("a test docstring are as public as the CHANGELOG:")
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
