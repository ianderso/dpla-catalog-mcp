"""Settings from the environment and from ``.env`` in the working directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from dpla_catalog_mcp.config import ConfigError, load_config

from .conftest import TEST_KEY

VARIABLES = ("DPLA_API_KEY", "DPLA_CACHE_DIR", "DPLA_TIMEOUT", "DPLA_MIN_INTERVAL")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_the_key_is_required_and_the_message_says_how_to_get_one():
    with pytest.raises(ConfigError, match="DPLA_API_KEY is not set") as caught:
        load_config()
    assert "api.dp.la/v2/api_key" in str(caught.value)


def test_defaults(monkeypatch):
    monkeypatch.setenv("DPLA_API_KEY", TEST_KEY)
    cfg = load_config()
    assert cfg.api_key == TEST_KEY
    assert cfg.timeout == 30.0
    assert cfg.min_interval == 0.25
    assert cfg.cache_dir == Path.home() / ".cache" / "dpla-catalog-mcp"


def test_every_setting_is_read(monkeypatch, tmp_path):
    monkeypatch.setenv("DPLA_API_KEY", f"  {TEST_KEY}\n")
    monkeypatch.setenv("DPLA_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("DPLA_TIMEOUT", "15")
    monkeypatch.setenv("DPLA_MIN_INTERVAL", "1")
    cfg = load_config()
    assert cfg.api_key == TEST_KEY
    assert cfg.cache_dir == tmp_path / "c"
    assert (cfg.timeout, cfg.min_interval) == (15.0, 1.0)


def test_the_placeholder_is_not_a_key(monkeypatch):
    monkeypatch.setenv("DPLA_API_KEY", "your-key-here")
    with pytest.raises(ConfigError, match="placeholder"):
        load_config()


@pytest.mark.parametrize(
    "bad",
    [TEST_KEY[:-1], TEST_KEY + "x", f'"{TEST_KEY}"', TEST_KEY[:-1] + "!", "Bearer " + TEST_KEY],
)
def test_a_value_not_shaped_like_a_key_is_refused_without_repeating_it(monkeypatch, bad):
    monkeypatch.setenv("DPLA_API_KEY", bad)
    with pytest.raises(ConfigError, match="not shaped like a DPLA key") as caught:
        load_config()
    assert bad.strip('"') not in str(caught.value) and TEST_KEY[:12] not in str(caught.value)


def test_the_key_is_not_in_the_configs_repr(monkeypatch):
    monkeypatch.setenv("DPLA_API_KEY", TEST_KEY)
    assert TEST_KEY not in repr(load_config())


@pytest.mark.parametrize("raw", ["thirty", "0", "-5", "nan", "inf"])
def test_an_unusable_timeout_names_the_variable(monkeypatch, raw):
    monkeypatch.setenv("DPLA_API_KEY", TEST_KEY)
    monkeypatch.setenv("DPLA_TIMEOUT", raw)
    with pytest.raises(ConfigError, match="DPLA_TIMEOUT"):
        load_config()


def test_the_interval_may_be_zero_but_not_negative(monkeypatch):
    monkeypatch.setenv("DPLA_API_KEY", TEST_KEY)
    monkeypatch.setenv("DPLA_MIN_INTERVAL", "0")
    assert load_config().min_interval == 0.0
    monkeypatch.setenv("DPLA_MIN_INTERVAL", "-1")
    with pytest.raises(ConfigError, match="DPLA_MIN_INTERVAL"):
        load_config()


def test_dot_env_in_the_working_directory_is_read(tmp_path):
    (tmp_path / ".env").write_text(f"DPLA_API_KEY={TEST_KEY}\nDPLA_TIMEOUT=7\n")
    cfg = load_config()
    assert (cfg.api_key, cfg.timeout) == (TEST_KEY, 7.0)


def test_the_environment_wins_over_dot_env(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"DPLA_API_KEY={TEST_KEY}\nDPLA_TIMEOUT=7\n")
    monkeypatch.setenv("DPLA_TIMEOUT", "9")
    assert load_config().timeout == 9.0
