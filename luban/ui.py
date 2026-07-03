"""Terminal rendering — standard library only (no third-party deps).

Colors use ANSI escape codes. On Windows 10+ the console needs virtual-terminal
processing enabled once for them to render; we do that at import. Color is
suppressed when stdout is not a TTY (e.g. piped/redirected) so captured output
stays clean.
"""
from __future__ import annotations

import difflib
import sys

_RESET = "\033[0m"


def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass  # no color is fine; never crash the UI over it


_enable_windows_ansi()
_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}{_RESET}" if _COLOR else text


def _emit(text: str) -> None:
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        sys.stdout.write(text.encode(enc, errors="replace").decode(enc))
    sys.stdout.flush()


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
            _emit(_c(line, "32") + "\n")  # green
        elif line.startswith("-") and not line.startswith("---"):
            _emit(_c(line, "31") + "\n")  # red
        elif line.startswith("@@"):
            _emit(_c(line, "36") + "\n")  # cyan
        else:
            _emit(_c(line, "2") + "\n")  # dim


def ask_confirm(prompt: str, input_fn=input) -> str:
    raw = input_fn(f"{prompt} [y]es/[n]o/[a]ll: ").strip().lower()
    if raw in ("y", "yes"):
        return "yes"
    if raw in ("a", "all"):
        return "all"
    return "no"


def print_text(text: str) -> None:
    _emit(text)


def print_thinking(text: str) -> None:
    # Reasoning/thinking output, dim + italic so it reads as secondary.
    _emit(_c(text, "2;3"))


def render_command(command: str) -> None:
    _emit(_c(f"$ {command}", "33") + "\n")  # yellow
