"""Tests for mobile configuration manager."""

from unittest.mock import MagicMock, patch

import pytest

from elle.mobile.config_manager import (
    READONLY_FIELDS,
    RESTART_REQUIRED_FIELDS,
    ConfigError,
    ConfigManager,
)
from elle.mobile.models import MobileRole


class TestConfigManager:
    """Tests for ConfigManager class."""

    def test_get_serialized_config_requires_operator(self):
        """Test that readonly role cannot access config."""
        manager = ConfigManager()

        with pytest.raises(ConfigError) as exc_info:
            manager.get_serialized_config(MobileRole.MOBILE_READONLY)

        assert "operator role" in str(exc_info.value)

    @patch("elle.mobile.config_manager.load_config")
    def test_get_serialized_config_returns_dict(self, mock_load):
        """Test that operator can get serialized config."""
        # Create a mock config
        mock_config = MagicMock()
        mock_config.log_level = "INFO"
        mock_config.port_probe_interval = 60
        mock_config.capability_versioning_enabled = True
        mock_config.capability_bootstrap_enabled = True
        mock_config.auto_learn_new_packages = True
        mock_config.package_probe_interval = 300
        mock_config.inotify_watch_paths = ["/etc/ssh/sshd_config"]

        mock_config.probes.memory_interval = 30
        mock_config.probes.disk_interval = 300
        mock_config.probes.network_interval = 60
        mock_config.probes.thermal_interval = 60
        mock_config.probes.smart_interval = 3600

        mock_config.thresholds.memory_warning_pct = 0.85
        mock_config.thresholds.disk_warning_pct = 0.90
        mock_config.thresholds.thermal_warning_c = 80.0
        mock_config.thresholds.network_error_threshold = 10

        mock_config.api.enabled = True
        mock_config.api.host = "127.0.0.1"
        mock_config.api.port = 8377

        mock_config.mobile.enabled = True
        mock_config.mobile.bind_host = "0.0.0.0"
        mock_config.mobile.bind_port = 8378
        mock_config.mobile.overlay_host = None
        mock_config.mobile.pairing_token_ttl_seconds = 90
        mock_config.mobile.max_paired_devices = 10
        mock_config.mobile.default_elevation_ttl_seconds = 600
        mock_config.mobile.max_elevation_ttl_seconds = 3600
        mock_config.mobile.default_role = "mobile_readonly"

        mock_config.ebpf.enabled = True
        mock_config.ebpf.programs = ["oom", "block_io"]

        mock_load.return_value = mock_config

        manager = ConfigManager()
        result = manager.get_serialized_config(MobileRole.MOBILE_OPERATOR)

        assert "daemon" in result
        assert "probes" in result
        assert "thresholds" in result
        assert "api" in result
        assert "mobile" in result
        assert "ebpf" in result

        assert result["daemon"]["log_level"] == "INFO"
        assert result["probes"]["memory_interval"] == 30
        assert result["mobile"]["bind_port"] == 8378

    def test_update_config_requires_operator(self):
        """Test that readonly role cannot update config."""
        manager = ConfigManager()

        with pytest.raises(ConfigError) as exc_info:
            manager.update_config({"daemon": {}}, MobileRole.MOBILE_READONLY)

        assert "operator role" in str(exc_info.value)


class TestConfigValidation:
    """Tests for configuration validation."""

    def test_validate_log_level(self):
        """Test log level validation."""
        manager = ConfigManager()

        # Valid log level
        errors = manager._validate_updates({"daemon": {"log_level": "DEBUG"}})
        assert len(errors) == 0

        # Invalid log level
        errors = manager._validate_updates({"daemon": {"log_level": "INVALID"}})
        assert len(errors) == 1
        assert "log_level" in errors[0]

    def test_validate_probe_intervals(self):
        """Test probe interval validation."""
        manager = ConfigManager()

        # Valid intervals
        errors = manager._validate_updates({"probes": {"memory_interval": 30}})
        assert len(errors) == 0

        # Invalid interval (too low)
        errors = manager._validate_updates({"probes": {"memory_interval": 2}})
        assert len(errors) == 1
        assert "memory_interval" in errors[0]

    def test_validate_thresholds(self):
        """Test threshold validation."""
        manager = ConfigManager()

        # Valid threshold
        errors = manager._validate_updates({"thresholds": {"memory_warning_pct": 0.85}})
        assert len(errors) == 0

        # Invalid threshold (out of range)
        errors = manager._validate_updates({"thresholds": {"memory_warning_pct": 1.5}})
        assert len(errors) == 1
        assert "memory_warning_pct" in errors[0]

        # Invalid thermal threshold
        errors = manager._validate_updates({"thresholds": {"thermal_warning_c": 200}})
        assert len(errors) == 1
        assert "thermal_warning_c" in errors[0]

    def test_validate_mobile_port(self):
        """Test mobile port validation."""
        manager = ConfigManager()

        # Valid port
        errors = manager._validate_updates({"mobile": {"bind_port": 8378}})
        assert len(errors) == 0

        # Invalid port (privileged)
        errors = manager._validate_updates({"mobile": {"bind_port": 80}})
        assert len(errors) == 1
        assert "bind_port" in errors[0]

    def test_validate_mobile_role(self):
        """Test mobile default role validation."""
        manager = ConfigManager()

        # Valid role
        errors = manager._validate_updates({"mobile": {"default_role": "mobile_readonly"}})
        assert len(errors) == 0

        # Invalid role
        errors = manager._validate_updates({"mobile": {"default_role": "admin"}})
        assert len(errors) == 1
        assert "default_role" in errors[0]


class TestReadonlyFields:
    """Tests for readonly field protection."""

    def test_check_readonly_fields(self):
        """Test that readonly fields are detected."""
        manager = ConfigManager()

        # Non-readonly field
        violations = manager._check_readonly_fields({"daemon": {"log_level": "DEBUG"}})
        assert len(violations) == 0

        # Readonly field
        violations = manager._check_readonly_fields({"api": {"host": "0.0.0.0"}})
        assert "api.host" in violations


class TestRestartRequired:
    """Tests for restart-required field detection."""

    def test_check_restart_required(self):
        """Test that restart-required fields are detected."""
        manager = ConfigManager()

        # Non-restart field
        requires_restart = manager._check_restart_required({"daemon": {"log_level": "DEBUG"}})
        assert not requires_restart

        # Restart-required field
        requires_restart = manager._check_restart_required({"mobile": {"bind_port": 8379}})
        assert requires_restart


class TestConfigSerialization:
    """Tests for TOML serialization."""

    def test_dict_to_toml_simple(self):
        """Test simple TOML serialization."""
        manager = ConfigManager()

        data = {
            "enabled": True,
            "port": 8378,
            "name": "test",
        }
        result = manager._dict_to_toml(data)

        assert "enabled = true" in result
        assert "port = 8378" in result
        assert 'name = "test"' in result

    def test_dict_to_toml_nested(self):
        """Test nested TOML serialization."""
        manager = ConfigManager()

        data = {
            "daemon": {
                "log_level": "INFO",
            }
        }
        result = manager._dict_to_toml(data)

        assert "[daemon]" in result
        assert 'log_level = "INFO"' in result

    def test_dict_to_toml_list(self):
        """Test list serialization in TOML."""
        manager = ConfigManager()

        data = {
            "programs": ["oom", "block_io"],
            "ports": [80, 443],
        }
        result = manager._dict_to_toml(data)

        assert 'programs = ["oom", "block_io"]' in result
        assert "ports = [80, 443]" in result


class TestConfigConstants:
    """Tests for configuration constants."""

    def test_readonly_fields_contains_sensitive_paths(self):
        """Test that sensitive fields are marked readonly."""
        assert "db_path" in READONLY_FIELDS
        assert "api.host" in READONLY_FIELDS

    def test_restart_required_fields_contains_port_changes(self):
        """Test that port changes require restart."""
        assert "api.port" in RESTART_REQUIRED_FIELDS
        assert "mobile.bind_port" in RESTART_REQUIRED_FIELDS
