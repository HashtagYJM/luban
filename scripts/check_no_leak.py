"""Pre-push guard: fail if internal identifiers appear in tracked files."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FORBIDDEN = ["dimsum_lite", "ApolloAnthropic", "Apollo", "UAT"]
SELF_EXCLUDE = {"scripts/check_no_leak.py", "tests/test_no_leak.py"}


def find_forbidden(files_text: dict[str, str]) -> list[str]:
    hits = []
    for path, text in files_text.items():
        if any(tok in text for tok in FORBIDDEN):
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
