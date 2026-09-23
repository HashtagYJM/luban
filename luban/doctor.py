"""`luban --doctor` — check what startup needs, and say how to fix what is missing.

Offline by default: the interpreter, the home, the config file, and the client adapter's
contract. `--doctor --probe` adds ONE small request through the adapter, because that is
the only way to learn whether the gateway answers — and it is the only check that spends
a token, so it is never implied.
"""
from __future__ import annotations

import os
import sys
import time
import tomllib
from pathlib import Path

from luban import client as client_mod, config as config_mod, paths

MIN_PYTHON = (3, 11)


def _line(ok: bool, what: str, fix: str = "") -> str:
    return f"  {'ok  ' if ok else 'FAIL'}  {what}" + (f"\n        → {fix}" if fix and not ok else "")


def _adapter_path() -> tuple[Path | None, str]:
    override = os.environ.get("LUBAN_CLIENT_LOCAL")
    if override:
        return (Path(override), "LUBAN_CLIENT_LOCAL") if Path(override).exists() else (
            None, f"LUBAN_CLIENT_LOCAL points at {override}, which does not exist")
    if client_mod.USER_CLIENT_PATH.exists():
        return client_mod.USER_CLIENT_PATH, "luban home"
    return None, ""


def run(probe: bool = False, model: str | None = None, out=print) -> int:
    """Print one line per check. Returns the process exit code: 0 when everything passed."""
    failed = 0

    def report(ok: bool, what: str, fix: str = "") -> bool:
        nonlocal failed
        failed += not ok
        out(_line(ok, what, fix))
        return ok

    out("luban doctor")
    v = sys.version_info
    report(v[:2] >= MIN_PYTHON, f"Python {v.major}.{v.minor}.{v.micro} at {sys.executable}",
           "luban needs Python 3.11 or newer; install it and reinstall the wheel into that "
           "interpreter's environment.")

    home = paths.luban_home()
    if report(home.is_dir(), f"luban home: {home}",
              "Run `luban` once to create it, or check LUBAN_HOME / `luban --set-home`."):
        probe_file = home / ".doctor-write-test"
        try:
            probe_file.write_text("ok", encoding="utf-8")
            probe_file.unlink()
            report(True, "luban home is writable")
        except OSError as exc:
            report(False, f"luban home is not writable ({exc})",
                   "Sessions, memory and the audit log live here; fix the folder's "
                   "permissions or point LUBAN_HOME somewhere writable.")

    cfg_path = config_mod.CONFIG_PATH
    if cfg_path.exists():
        try:
            tomllib.loads(cfg_path.read_text(encoding="utf-8", errors="replace"))
            report(True, f"config: {cfg_path}")
            for warning in config_mod.config_warnings(cfg_path):
                out(f"        note: {warning.strip()}")
        except tomllib.TOMLDecodeError as exc:
            report(False, f"config does not parse: {exc}",
                   f"Fix the line named above in {cfg_path}; until then every setting "
                   f"in it is ignored and defaults apply.")
    else:
        out(f"  --    config: none yet — `luban` writes defaults to {cfg_path} on first run")

    path, where = _adapter_path()
    client = None
    if path is None and where:
        report(False, where, "Fix or unset LUBAN_CLIENT_LOCAL.")
    elif path is None and client_mod._in_package_local() is None:
        example = Path(client_mod.__file__).with_name("client_local.example.py")
        report(False, "client adapter: none found",
               f"Copy {example} to {client_mod.USER_CLIENT_PATH} and make its "
               f"build_client() return your client.")
    else:
        label = f"{path} (from {where})" if path else "in-package client_local.py"
        try:
            provider = client_mod._load_provider()
            report(True, f"client adapter loads: {label}")
        except Exception as exc:
            provider = None
            report(False, f"client adapter fails to import: {type(exc).__name__}: {exc}",
                   "The error is inside your client_local.py — a missing package or a typo "
                   "there. Run it with python directly to see the full traceback.")
        if provider is not None:
            if report(callable(getattr(provider, "build_client", None)),
                      "adapter defines build_client()",
                      "Add a build_client() function that returns your client."):
                try:
                    client = client_mod.get_client()
                    report(hasattr(getattr(client, "messages", None), "create"),
                           "build_client() returns a client with .messages.create",
                           "build_client() must return an Anthropic-compatible client.")
                except NotImplementedError:
                    report(False, "build_client() is still the example's placeholder",
                           "Edit build_client() to return your organization's client.")
                except Exception as exc:
                    report(False, f"build_client() raised {type(exc).__name__}: {exc}",
                           "Usually credentials or an environment variable the client "
                           "reads; the message above is from your client, not luban.")
            if getattr(provider, "build_openai_client", None):
                out("  --    second provider: build_openai_client() defined (gpt-* models)")

    if probe:
        if client is None:
            report(False, "connection probe skipped: no working client")
        else:
            cfg = config_mod.load_config()
            model = model or cfg.model or client_mod.DEFAULT_MODEL
            t0 = time.monotonic()
            try:
                msg = client_mod.create_turn(
                    client, model=model, max_tokens=64, system="",
                    messages=[{"role": "user", "content": "Reply with the single word OK."}],
                    tools=[])
                text = "".join(getattr(b, "text", "") for b in msg.content).strip()
                report(bool(text), f"{model} answered in {time.monotonic() - t0:.1f}s: "
                       f"{text[:40]!r}", "The gateway returned no text; try --no-stream "
                       "or another --model.")
            except Exception as exc:
                report(False, f"{model} request failed: {type(exc).__name__}: {exc}",
                       "Check the model id (`/model` lists them) and the gateway's "
                       "credentials.")
    else:
        out("  --    connection not tested (offline check). `luban --doctor --probe` sends "
            "one short request.")

    out("all checks passed." if not failed else f"{failed} check(s) failed.")
    return 1 if failed else 0
