"""User-editable config at ~/.luban/config.toml.

Read with the stdlib `tomllib` (no dependency). On first run the file is
created with the auto-detected platform; users can edit it afterwards. Built
to grow — more keys (model, auto, stream) can be added later.
"""
from __future__ import annotations

import platform as _platform
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".luban"
CONFIG_PATH = CONFIG_DIR / "config.toml"

_VALID_PLATFORMS = {"windows", "mac", "linux"}


@dataclass
class Config:
    platform: str


def detect_platform() -> str:
    """Map platform.system() to luban's short platform names."""
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        _platform.system(), _platform.system().lower()
    )


def _default_text(plat: str) -> str:
    return (
        "# ~/.luban/config.toml — luban settings (edit me)\n"
        f'platform = "{plat}"   # windows | mac | linux\n'
    )


def write_default(path: Path = CONFIG_PATH) -> str:
    """Create the config file with the detected platform. Returns the platform."""
    plat = detect_platform()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_default_text(plat))
    return plat


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load config, auto-creating it on first run. Never raises on a bad file."""
    if not path.exists():
        return Config(platform=write_default(path))
    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        data = {}
    plat = data.get("platform") or detect_platform()
    if plat not in _VALID_PLATFORMS:
        plat = detect_platform()
    return Config(platform=plat)
