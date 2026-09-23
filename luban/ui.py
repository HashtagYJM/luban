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


def input_pending() -> bool:
    """Whether more typed or pasted input is already waiting. A paste arrives as many
    lines at once; without this each line was submitted as its own turn. A terminal
    only: piped input (`luban < script`) is one line per entry by construction, and
    joining it would swallow a confirmation answer into the prompt before it."""
    try:
        if not sys.stdin.isatty():
            return False
        if sys.platform == "win32":
            import msvcrt
            return bool(msvcrt.kbhit())
        import select
        return bool(select.select([sys.stdin], [], [], 0.03)[0])
    except Exception:
        return False


BLOCK = '"""'


def read_prompt(prompt: str, input_fn=input, pending=input_pending) -> str:
    """One prompt, however many lines it has.

    Two ways in. A paste: lines already waiting when the first is read belong to it. And
    deliberate composition: a line that is exactly three double quotes opens a block that
    the same line closes — the only way to type a multi-line prompt by hand."""
    first = input_fn(prompt)
    if first.strip() == BLOCK:
        lines = []
        while True:
            line = input_fn("... ")
            if line.strip() == BLOCK:
                return "\n".join(lines)
            lines.append(line)
    lines = [first]
    while pending():
        try:
            lines.append(input_fn(""))
        except EOFError:
            break
    return "\n".join(lines)


def print_text(text: str) -> None:
    _emit(text)


def print_thinking(text: str) -> None:
    # Reasoning/thinking output, dim + italic so it reads as secondary.
    _emit(_c(text, "2;3"))


def render_command(command: str) -> None:
    _emit(_c(f"$ {command}", "33") + "\n")  # yellow
