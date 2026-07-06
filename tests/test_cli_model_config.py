import tomllib
from pathlib import Path

import pytest

import luban
from luban import cli, client, config as config_mod


def test_version_matches_pyproject():
    data = tomllib.loads(
        (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert luban.__version__ == data["project"]["version"]


def test_version_flag_prints_and_exits(capsys):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(["--version"])
    assert e.value.code == 0
    assert luban.__version__ in capsys.readouterr().out


def test_resolve_model_flag_wins():
    cfg = config_mod.Config(platform="mac", model="cfg-model")
    assert cli.resolve_model("flag-model", cfg) == "flag-model"


def test_resolve_model_config_over_default():
    cfg = config_mod.Config(platform="mac", model="cfg-model")
    assert cli.resolve_model(None, cfg) == "cfg-model"


def test_resolve_model_builtin_default():
    cfg = config_mod.Config(platform="mac")
    assert cli.resolve_model(None, cfg) == client.DEFAULT_MODEL


def test_model_flag_default_is_none():
    assert cli.parse_args([]).model is None
