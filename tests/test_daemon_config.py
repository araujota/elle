"""Tests for daemon configuration loading."""

import os
import tempfile
from pathlib import Path

from elle.daemon.config import (
    ApiConfig,
    Config,
    CorrelationConfig,
    ProbeConfig,
    QueueConfig,
    ThresholdConfig,
    get_config,
    load_config,
    reset_config,
    set_config,
)


class TestProbeConfig:
    """Tests for ProbeConfig defaults."""

    def test_default_intervals(self):
        config = ProbeConfig()
        assert config.memory_interval == 30
        assert config.disk_interval == 300
        assert config.network_interval == 60
        assert config.thermal_interval == 60
        assert config.smart_interval == 3600


class TestThresholdConfig:
    """Tests for ThresholdConfig defaults."""

    def test_default_thresholds(self):
        config = ThresholdConfig()
        assert config.memory_warning_pct == 0.85
        assert config.disk_warning_pct == 0.90
        assert config.thermal_warning_c == 80.0
        assert config.network_error_threshold == 10


class TestApiConfig:
    """Tests for ApiConfig defaults."""

    def test_default_api_config(self):
        config = ApiConfig()
        assert config.enabled is True
        assert config.host == "127.0.0.1"
        assert config.port == 8377


class TestConfig:
    """Tests for main Config class."""

    def test_default_config(self):
        config = Config()
        assert config.log_level == "INFO"
        assert config.database.dbname == "elle"

    def test_nested_configs(self):
        config = Config()
        assert isinstance(config.probes, ProbeConfig)
        assert isinstance(config.thresholds, ThresholdConfig)
        assert isinstance(config.api, ApiConfig)
        assert isinstance(config.correlation, CorrelationConfig)
        assert isinstance(config.queues, QueueConfig)


class TestConfigLoading:
    """Tests for configuration file loading."""

    def test_load_empty_config(self):
        # Should return defaults when no config files exist
        reset_config()
        config = load_config()
        assert isinstance(config, Config)
        assert config.log_level == "INFO"

    def test_load_from_explicit_path(self):
        # Create temporary TOML config
        toml_content = """
[daemon]
log_level = "DEBUG"

[daemon.probes]
memory_interval = 10

[daemon.api]
port = 9999
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write(toml_content)
            config_path = Path(f.name)

        try:
            config = load_config(config_path)
            assert config.log_level == "DEBUG"
            assert config.probes.memory_interval == 10
            assert config.api.port == 9999
        finally:
            config_path.unlink()


class TestEnvironmentOverrides:
    """Tests for environment variable overrides."""

    def test_log_level_override(self):
        reset_config()
        original = os.environ.get("ELLE_LOG_LEVEL")
        try:
            os.environ["ELLE_LOG_LEVEL"] = "DEBUG"
            config = load_config()
            assert config.log_level == "DEBUG"
        finally:
            if original:
                os.environ["ELLE_LOG_LEVEL"] = original
            else:
                os.environ.pop("ELLE_LOG_LEVEL", None)

    def test_api_enabled_override(self):
        reset_config()
        original = os.environ.get("ELLE_API_ENABLED")
        try:
            os.environ["ELLE_API_ENABLED"] = "false"
            config = load_config()
            assert config.api.enabled is False
        finally:
            if original:
                os.environ["ELLE_API_ENABLED"] = original
            else:
                os.environ.pop("ELLE_API_ENABLED", None)

    def test_probe_interval_override(self):
        reset_config()
        original = os.environ.get("ELLE_PROBE_MEMORY_INTERVAL")
        try:
            os.environ["ELLE_PROBE_MEMORY_INTERVAL"] = "5"
            config = load_config()
            assert config.probes.memory_interval == 5
        finally:
            if original:
                os.environ["ELLE_PROBE_MEMORY_INTERVAL"] = original
            else:
                os.environ.pop("ELLE_PROBE_MEMORY_INTERVAL", None)


class TestConfigSingleton:
    """Tests for config singleton behavior."""

    def test_get_config_returns_same_instance(self):
        reset_config()
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_set_config_changes_instance(self):
        reset_config()
        custom_config = Config(log_level="DEBUG")
        set_config(custom_config)
        assert get_config().log_level == "DEBUG"

    def test_reset_config_clears_instance(self):
        reset_config()
        custom_config = Config(log_level="DEBUG")
        set_config(custom_config)
        reset_config()
        # Should now return default
        new_config = get_config()
        assert new_config.log_level == "INFO"
