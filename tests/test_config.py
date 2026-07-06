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


def test_permissions_parsed(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        'platform = "mac"\n[permissions]\nallow = ["run_command:python *"]\ndeny = ["run_command:del *"]\n'
    )
    cfg = config.load_config(p)
    assert cfg.allow == ["run_command:python *"]
    assert cfg.deny == ["run_command:del *"]


def test_permissions_missing_section_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\n')
    cfg = config.load_config(p)
    assert cfg.allow == [] and cfg.deny == []


def test_permissions_non_string_items_dropped(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\n[permissions]\nallow = ["ok", 3]\ndeny = []\n')
    cfg = config.load_config(p)
    assert cfg.allow == ["ok"]


def test_permissions_non_table_never_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\npermissions = "oops"\n')
    cfg = config.load_config(p)
    assert cfg.allow == [] and cfg.deny == []


def test_permissions_string_value_not_iterated_as_chars(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\n[permissions]\nallow = "run_command"\n')
    cfg = config.load_config(p)
    assert cfg.allow == []


def test_memory_file_parsed(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\nmemory_file = "CLAUDE.md"\n')
    assert config.load_config(p).memory_file == "CLAUDE.md"


def test_memory_file_default_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\n')
    assert config.load_config(p).memory_file == ""


def test_memory_file_non_string_ignored(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\nmemory_file = 3\n')
    assert config.load_config(p).memory_file == ""


def test_memory_enabled_default_true(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\n')
    assert config.load_config(p).memory_enabled is True


def test_memory_enabled_false_parsed(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\nmemory_enabled = false\n')
    assert config.load_config(p).memory_enabled is False


def test_memory_enabled_non_bool_ignored(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\nmemory_enabled = "yes"\n')
    assert config.load_config(p).memory_enabled is True


def test_model_key_parsed(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\nmodel = "my-model"\n')
    assert config.load_config(p).model == "my-model"


def test_model_key_default_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\n')
    assert config.load_config(p).model == ""


def test_model_key_non_str_ignored(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('platform = "mac"\nmodel = 3\n')
    assert config.load_config(p).model == ""
