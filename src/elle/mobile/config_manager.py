"""Remote configuration management for ELLE Mobile Gateway.

This module provides read/write access to ELLE configuration from
mobile devices. It handles:
- Serializing frozen dataclass configs to JSON
- Validating and applying config updates
- Writing changes to the user config file
- Signaling the daemon to reload configuration
"""

import logging
import os
import signal
from pathlib import Path
from typing import Any

from elle.daemon.config import Config, load_config
from elle.mobile.models import MobileRole

logger = logging.getLogger(__name__)


# User config file location
USER_CONFIG_PATH = Path.home() / ".config" / "elle" / "elle.toml"

# PID file for daemon
DAEMON_PID_FILE = Path("/var/run/elle/elled.pid")


# Fields that cannot be modified remotely for security reasons
READONLY_FIELDS = frozenset(
    {
        "db_path",
        "incidents_db_path",
        "manvault_db_path",
        "api.host",  # Internal API should stay on localhost
        "api_auth.api_keys_db_path",
        "api_auth.elle_uid",
    }
)

# Fields that require daemon restart (not just reload)
RESTART_REQUIRED_FIELDS = frozenset(
    {
        "api.port",
        "mobile.bind_host",
        "mobile.bind_port",
        "ebpf.enabled",
        "ebpf.programs",
    }
)


class ConfigError(Exception):
    """Error during configuration operation."""

    pass


class ConfigManager:
    """Manages remote configuration access.

    Provides methods to read the current configuration and apply
    updates from mobile devices.
    """

    def __init__(self):
        """Initialize the config manager."""
        self._config: Config | None = None

    def get_config(self) -> Config:
        """Get the current configuration.

        Returns:
            Current Config object.
        """
        if self._config is None:
            self._config = load_config()
        return self._config

    def reload_config(self) -> Config:
        """Reload configuration from disk.

        Returns:
            Reloaded Config object.
        """
        self._config = load_config()
        return self._config

    def get_serialized_config(self, role: MobileRole) -> dict[str, Any]:
        """Get configuration serialized for mobile client.

        Args:
            role: The mobile device's effective role.

        Returns:
            Configuration as nested dictionary, filtered by role.

        Raises:
            ConfigError: If role is not allowed to view config.
        """
        if role != MobileRole.MOBILE_OPERATOR:
            raise ConfigError("Configuration access requires operator role")

        config = self.get_config()

        return {
            "daemon": {
                "log_level": config.log_level,
                "journal_enabled": config.journal_enabled,
                "kernel_enabled": config.kernel_enabled,
                "probes_enabled": config.probes_enabled,
                "ebpf_enabled": config.ebpf_enabled,
                "docker_enabled": config.docker_enabled,
                "inotify_enabled": config.inotify_enabled,
                "wireguard_enabled": config.wireguard_enabled,
                "port_probe_enabled": config.port_probe_enabled,
                "port_probe_interval": config.port_probe_interval,
                "capability_versioning_enabled": config.capability_versioning_enabled,
                "capability_bootstrap_enabled": config.capability_bootstrap_enabled,
                "auto_learn_new_packages": config.auto_learn_new_packages,
                "package_probe_interval": config.package_probe_interval,
                "inotify_watch_paths": list(config.inotify_watch_paths),
            },
            "probes": {
                "memory_interval": config.probes.memory_interval,
                "disk_interval": config.probes.disk_interval,
                "network_interval": config.probes.network_interval,
                "thermal_interval": config.probes.thermal_interval,
                "smart_interval": config.probes.smart_interval,
            },
            "thresholds": {
                "memory_warning_pct": config.thresholds.memory_warning_pct,
                "disk_warning_pct": config.thresholds.disk_warning_pct,
                "thermal_warning_c": config.thresholds.thermal_warning_c,
                "network_error_threshold": config.thresholds.network_error_threshold,
            },
            "api": {
                "enabled": config.api.enabled,
                "host": config.api.host,  # Read-only but visible
                "port": config.api.port,
            },
            "mobile": {
                "enabled": config.mobile.enabled,
                "bind_host": config.mobile.bind_host,
                "bind_port": config.mobile.bind_port,
                "overlay_host": config.mobile.overlay_host,
                "pairing_token_ttl_seconds": config.mobile.pairing_token_ttl_seconds,
                "max_paired_devices": config.mobile.max_paired_devices,
                "default_elevation_ttl_seconds": config.mobile.default_elevation_ttl_seconds,
                "max_elevation_ttl_seconds": config.mobile.max_elevation_ttl_seconds,
                "default_role": config.mobile.default_role,
            },
            "ebpf": {
                "enabled": config.ebpf.enabled,
                "programs": list(config.ebpf.programs),
            },
        }

    def update_config(
        self,
        updates: dict[str, Any],
        role: MobileRole,
    ) -> dict[str, Any]:
        """Update configuration with provided values.

        Args:
            updates: Nested dictionary of configuration updates.
            role: The mobile device's effective role.

        Returns:
            Dictionary with status and any required actions.

        Raises:
            ConfigError: If validation fails or role is not allowed.
        """
        if role != MobileRole.MOBILE_OPERATOR:
            raise ConfigError("Configuration changes require operator role")

        # Validate updates
        validation_errors = self._validate_updates(updates)
        if validation_errors:
            raise ConfigError(f"Validation failed: {'; '.join(validation_errors)}")

        # Check for readonly fields
        readonly_violations = self._check_readonly_fields(updates)
        if readonly_violations:
            raise ConfigError(f"Cannot modify readonly fields: {', '.join(readonly_violations)}")

        # Check if restart is required
        restart_required = self._check_restart_required(updates)

        # Write to user config file
        self._write_config(updates)

        # Signal daemon to reload
        reload_success = self._signal_daemon_reload()

        # Reload our cached config
        self.reload_config()

        return {
            "status": "updated",
            "reload_success": reload_success,
            "restart_required": restart_required,
            "message": (
                "Configuration updated. Daemon restart required for some changes."
                if restart_required
                else "Configuration updated and reloaded."
            ),
        }

    def _validate_updates(self, updates: dict[str, Any]) -> list[str]:
        """Validate configuration updates.

        Args:
            updates: Proposed updates.

        Returns:
            List of validation error messages.
        """
        errors = []

        # Validate daemon section
        if "daemon" in updates:
            daemon = updates["daemon"]
            if "log_level" in daemon:
                valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
                if daemon["log_level"].upper() not in valid_levels:
                    errors.append(f"Invalid log_level: {daemon['log_level']}")

            if "port_probe_interval" in daemon and daemon["port_probe_interval"] < 10:
                errors.append("port_probe_interval must be at least 10 seconds")

            if "package_probe_interval" in daemon and daemon["package_probe_interval"] < 60:
                errors.append("package_probe_interval must be at least 60 seconds")

        # Validate probes section
        if "probes" in updates:
            probes = updates["probes"]
            for key in ["memory_interval", "disk_interval", "network_interval", "thermal_interval", "smart_interval"]:
                if key in probes and probes[key] < 5:
                    errors.append(f"{key} must be at least 5 seconds")

        # Validate thresholds section
        if "thresholds" in updates:
            thresholds = updates["thresholds"]
            for key in ["memory_warning_pct", "disk_warning_pct"]:
                if key in thresholds:
                    val = thresholds[key]
                    if not 0 < val <= 1:
                        errors.append(f"{key} must be between 0 and 1")

            if "thermal_warning_c" in thresholds:
                val = thresholds["thermal_warning_c"]
                if not 30 <= val <= 120:
                    errors.append("thermal_warning_c must be between 30 and 120")

        # Validate mobile section
        if "mobile" in updates:
            mobile = updates["mobile"]
            if "bind_port" in mobile:
                port = mobile["bind_port"]
                if not 1024 <= port <= 65535:
                    errors.append("mobile bind_port must be between 1024 and 65535")

            if "max_paired_devices" in mobile and mobile["max_paired_devices"] < 1:
                errors.append("max_paired_devices must be at least 1")

            if "pairing_token_ttl_seconds" in mobile:
                ttl = mobile["pairing_token_ttl_seconds"]
                if not 30 <= ttl <= 300:
                    errors.append("pairing_token_ttl_seconds must be between 30 and 300")

            if "default_role" in mobile:
                valid_roles = {"mobile_readonly", "mobile_operator"}
                if mobile["default_role"] not in valid_roles:
                    errors.append(f"Invalid default_role: {mobile['default_role']}")

        return errors

    def _check_readonly_fields(self, updates: dict[str, Any]) -> list[str]:
        """Check for attempts to modify readonly fields.

        Args:
            updates: Proposed updates.

        Returns:
            List of readonly field names that were attempted.
        """
        violations = []

        def check_nested(data: dict, prefix: str = "") -> None:
            for key, value in data.items():
                full_key = f"{prefix}.{key}" if prefix else key
                if full_key in READONLY_FIELDS:
                    violations.append(full_key)
                elif isinstance(value, dict):
                    check_nested(value, full_key)

        check_nested(updates)
        return violations

    def _check_restart_required(self, updates: dict[str, Any]) -> bool:
        """Check if updates require daemon restart.

        Args:
            updates: Proposed updates.

        Returns:
            True if restart is required.
        """

        def check_nested(data: dict, prefix: str = "") -> bool:
            for key, value in data.items():
                full_key = f"{prefix}.{key}" if prefix else key
                if full_key in RESTART_REQUIRED_FIELDS:
                    return True
                if isinstance(value, dict) and check_nested(value, full_key):
                    return True
            return False

        return check_nested(updates)

    def _write_config(self, updates: dict[str, Any]) -> None:
        """Write configuration updates to user config file.

        Args:
            updates: Configuration updates to write.
        """
        # Ensure config directory exists
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Load existing config or start fresh
        existing = {}
        if USER_CONFIG_PATH.exists():
            try:
                import tomllib

                with open(USER_CONFIG_PATH, "rb") as f:
                    existing = tomllib.load(f)
            except ImportError:
                try:
                    import tomli as tomllib

                    with open(USER_CONFIG_PATH, "rb") as f:
                        existing = tomllib.load(f)
                except ImportError:
                    logger.warning("No TOML library available, starting fresh config")

        # Merge updates
        self._merge_dict(existing, updates)

        # Write back as TOML
        toml_content = self._dict_to_toml(existing)
        USER_CONFIG_PATH.write_text(toml_content)

        logger.info("Configuration written to %s", USER_CONFIG_PATH)

    def _merge_dict(self, base: dict, override: dict) -> None:
        """Recursively merge override into base.

        Args:
            base: Base dictionary to modify.
            override: Override values.
        """
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_dict(base[key], value)
            else:
                base[key] = value

    def _dict_to_toml(self, data: dict, indent: int = 0) -> str:
        """Convert dictionary to TOML string.

        Args:
            data: Dictionary to convert.
            indent: Current indentation level.

        Returns:
            TOML-formatted string.
        """
        lines = []
        tables = []

        for key, value in data.items():
            if isinstance(value, dict):
                # Collect tables for later
                tables.append((key, value))
            elif isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            elif isinstance(value, str):
                lines.append(f'{key} = "{value}"')
            elif isinstance(value, (list, tuple)):
                if all(isinstance(v, str) for v in value):
                    items = ", ".join(f'"{v}"' for v in value)
                    lines.append(f"{key} = [{items}]")
                else:
                    items = ", ".join(str(v) for v in value)
                    lines.append(f"{key} = [{items}]")
            elif value is None:
                # Skip None values
                pass
            else:
                lines.append(f"{key} = {value}")

        # Add tables
        for table_name, table_data in tables:
            lines.append("")
            lines.append(f"[{table_name}]")
            lines.append(self._dict_to_toml(table_data, indent + 1))

        return "\n".join(lines)

    def _signal_daemon_reload(self) -> bool:
        """Signal the daemon to reload configuration.

        Returns:
            True if signal was sent successfully.
        """
        if not DAEMON_PID_FILE.exists():
            logger.warning("Daemon PID file not found, cannot signal reload")
            return False

        try:
            pid = int(DAEMON_PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGHUP)
            logger.info("Sent SIGHUP to daemon (PID %d)", pid)
            return True
        except (ValueError, OSError) as e:
            logger.error("Failed to signal daemon: %s", e)
            return False


# Singleton instance
_config_manager: ConfigManager | None = None


def get_config_manager() -> ConfigManager:
    """Get the global config manager instance.

    Returns:
        ConfigManager singleton.
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
