from __future__ import annotations

import difflib

from rich.console import Console
from rich.text import Text

_console = Console()


def unified_diff_text(path: str, old: str, new: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(diff)


def render_diff(path: str, old: str, new: str) -> None:
    for line in unified_diff_text(path, old, new).splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            _console.print(Text(line, style="green"))
        elif line.startswith("-") and not line.startswith("---"):
            _console.print(Text(line, style="red"))
        elif line.startswith("@@"):
            _console.print(Text(line, style="cyan"))
        else:
            _console.print(Text(line, style="dim"))
