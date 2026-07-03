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


def ask_confirm(prompt: str, input_fn=input) -> str:
    raw = input_fn(f"{prompt} [y]es/[n]o/[a]ll: ").strip().lower()
    if raw in ("y", "yes"):
        return "yes"
    if raw in ("a", "all"):
        return "all"
    return "no"


def print_text(text: str) -> None:
    _console.print(text, end="", soft_wrap=True)


def print_thinking(text: str) -> None:
    # Reasoning/thinking output, rendered dim so it reads as secondary to the answer.
    _console.print(Text(text, style="dim italic"), end="", soft_wrap=True)


def render_command(command: str) -> None:
    _console.print(Text(f"$ {command}", style="yellow"))
