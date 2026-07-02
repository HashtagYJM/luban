from luban import config


def test_detect_platform_known():
    assert config.detect_platform() in {"windows", "mac", "linux"}


def test_load_creates_default_on_first_run(tmp_path):
    p = tmp_path / "config.toml"
    assert not p.exists()
    cfg = config.load_config(p)
    assert p.exists()  # auto-written
    assert cfg.platform == config.detect_platform()
    assert 'platform = "' in p.read_text()


def test_load_reads_existing_platform(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "windows"\n')
    assert config.load_config(p).platform == "windows"


def test_load_bad_toml_falls_back(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("this is = = not valid toml [[[\n")
    assert config.load_config(p).platform == config.detect_platform()


def test_load_invalid_platform_falls_back(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "banana"\n')
    assert config.load_config(p).platform == config.detect_platform()
