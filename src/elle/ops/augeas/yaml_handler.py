"""YAML configuration file handler.

Handles YAML config files (like netplan) that Augeas cannot process.
Uses ruamel.yaml for round-trip preservation of formatting and comments.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Lazy-loaded ruamel.yaml
_ruamel: Any = None


def _get_ruamel() -> Any:
    """Get the ruamel.yaml module, raising if unavailable."""
    global _ruamel
    if _ruamel is None:
        try:
            from ruamel.yaml import YAML

            _ruamel = YAML
        except ImportError as e:
            raise ImportError(
                "ruamel.yaml is not installed. Install with: pip install ruamel.yaml"
            ) from e
    return _ruamel


def is_available() -> bool:
    """Check if ruamel.yaml is available.

    Returns:
        True if ruamel.yaml can be imported.
    """
    try:
        _get_ruamel()
        return True
    except ImportError:
        return False


class YAMLChange(BaseModel):
    """Record of a YAML change."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Dot-separated path to the changed key")
    old_value: Any = Field(default=None, description="Previous value")
    new_value: Any = Field(default=None, description="New value")
    operation: str = Field(default="set", description="Operation type")


class YAMLEditResult(BaseModel):
    """Result of a YAML edit operation."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether the edit succeeded")
    file_path: str = Field(description="Path to the edited file")
    changes: tuple[YAMLChange, ...] = Field(
        default_factory=tuple,
        description="Changes made",
    )
    error: str | None = Field(default=None, description="Error message if failed")


class YAMLHandler:
    """Handle YAML config files with formatting preservation.

    Uses ruamel.yaml for round-trip parsing that preserves:
    - Comments
    - Ordering of keys
    - Indentation style
    - Quote style

    Usage:
        handler = YAMLHandler()
        data = handler.load("/etc/netplan/config.yaml")
        handler.set_path(data, "network.ethernets.eth0.dhcp4", True)
        handler.save("/etc/netplan/config.yaml", data)
    """

    def __init__(
        self,
        preserve_quotes: bool = True,
        default_flow_style: bool = False,
        indent: int = 2,
    ) -> None:
        """Initialize the YAML handler.

        Args:
            preserve_quotes: Preserve quote style in round-trip.
            default_flow_style: Use flow style for sequences/mappings.
            indent: Default indentation level.
        """
        YAML = _get_ruamel()
        self._yaml = YAML()
        self._yaml.preserve_quotes = preserve_quotes
        self._yaml.default_flow_style = default_flow_style
        self._yaml.indent(mapping=indent, sequence=indent, offset=indent)

        # Track changes for diff
        self._changes: list[YAMLChange] = []

    def load(self, file_path: str | Path) -> dict[str, Any]:
        """Load a YAML file.

        Args:
            file_path: Path to the YAML file.

        Returns:
            Parsed YAML data as a dictionary.

        Raises:
            FileNotFoundError: If file doesn't exist.
            yaml.YAMLError: If parsing fails.
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"YAML file not found: {file_path}")

        with open(file_path) as f:
            data = self._yaml.load(f)

        if data is None:
            return {}

        return dict(data) if hasattr(data, "items") else data

    def save(self, file_path: str | Path, data: dict[str, Any]) -> None:
        """Save data to a YAML file.

        Args:
            file_path: Path to save to.
            data: Data to save.
        """
        file_path = Path(file_path)

        with open(file_path, "w") as f:
            self._yaml.dump(data, f)

        logger.info(f"Saved YAML file: {file_path}")

    def dumps(self, data: dict[str, Any]) -> str:
        """Serialize data to YAML string.

        Args:
            data: Data to serialize.

        Returns:
            YAML formatted string.
        """
        import io

        stream = io.StringIO()
        self._yaml.dump(data, stream)
        return stream.getvalue()

    def get_path(self, data: dict[str, Any], path: str) -> Any:
        """Get value at a dot-separated path.

        Args:
            data: YAML data dictionary.
            path: Dot-separated path (e.g., "network.ethernets.eth0.dhcp4").

        Returns:
            Value at the path, or None if not found.

        Example:
            >>> handler.get_path(data, "network.ethernets.eth0.dhcp4")
            True
        """
        parts = path.split(".")
        current = data

        for part in parts:
            if current is None:
                return None

            # Handle list indices
            if part.isdigit():
                idx = int(part)
                if isinstance(current, (list, tuple)) and idx < len(current):
                    current = current[idx]
                else:
                    return None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None

        return current

    def set_path(
        self,
        data: dict[str, Any],
        path: str,
        value: Any,
        create_parents: bool = True,
    ) -> None:
        """Set value at a dot-separated path.

        Args:
            data: YAML data dictionary (modified in place).
            path: Dot-separated path.
            value: Value to set.
            create_parents: Create intermediate dictionaries if needed.

        Raises:
            KeyError: If path doesn't exist and create_parents is False.

        Example:
            >>> handler.set_path(data, "network.ethernets.eth0.dhcp4", False)
        """
        parts = path.split(".")
        current = data

        # Record old value for change tracking
        old_value = self.get_path(data, path)

        # Navigate to parent
        for part in parts[:-1]:
            if part.isdigit():
                idx = int(part)
                if isinstance(current, list):
                    if idx >= len(current):
                        raise KeyError(f"Index {idx} out of range in {path}")
                    current = current[idx]
                else:
                    raise KeyError(f"Cannot index non-list at {part} in {path}")
            elif isinstance(current, dict):
                if part not in current:
                    if create_parents:
                        current[part] = {}
                    else:
                        raise KeyError(f"Key {part} not found in {path}")
                current = current[part]
            else:
                raise KeyError(f"Cannot navigate through {type(current)} at {part}")

        # Set the final value
        final_key = parts[-1]
        if final_key.isdigit():
            idx = int(final_key)
            if isinstance(current, list):
                if idx >= len(current):
                    current.append(value)
                else:
                    current[idx] = value
            else:
                raise KeyError(f"Cannot set list index on {type(current)}")
        else:
            current[final_key] = value

        # Track change
        self._changes.append(
            YAMLChange(
                path=path,
                old_value=old_value,
                new_value=value,
                operation="set",
            )
        )

        logger.debug(f"Set {path} = {value}")

    def delete_path(self, data: dict[str, Any], path: str) -> bool:
        """Delete a key at a dot-separated path.

        Args:
            data: YAML data dictionary (modified in place).
            path: Dot-separated path to delete.

        Returns:
            True if key was deleted, False if not found.
        """
        parts = path.split(".")
        current = data

        # Record old value for change tracking
        old_value = self.get_path(data, path)

        # Navigate to parent
        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                if idx < len(current):
                    current = current[idx]
                else:
                    return False
            else:
                return False

        # Delete the final key
        final_key = parts[-1]
        if isinstance(current, dict) and final_key in current:
            del current[final_key]

            # Track change
            self._changes.append(
                YAMLChange(
                    path=path,
                    old_value=old_value,
                    new_value=None,
                    operation="delete",
                )
            )

            logger.debug(f"Deleted {path}")
            return True
        elif isinstance(current, list) and final_key.isdigit():
            idx = int(final_key)
            if idx < len(current):
                del current[idx]

                self._changes.append(
                    YAMLChange(
                        path=path,
                        old_value=old_value,
                        new_value=None,
                        operation="delete",
                    )
                )
                return True

        return False

    def merge(
        self,
        data: dict[str, Any],
        updates: dict[str, Any],
        path: str | None = None,
    ) -> None:
        """Deep merge updates into data.

        Args:
            data: Base YAML data (modified in place).
            updates: Updates to merge.
            path: Optional base path for tracking changes.
        """

        def _merge(base: dict, update: dict, current_path: str = "") -> None:
            for key, value in update.items():
                key_path = f"{current_path}.{key}" if current_path else key

                if (
                    key in base
                    and isinstance(base[key], dict)
                    and isinstance(value, dict)
                ):
                    # Recursive merge for nested dicts
                    _merge(base[key], value, key_path)
                else:
                    old_value = base.get(key)
                    base[key] = value

                    self._changes.append(
                        YAMLChange(
                            path=key_path,
                            old_value=old_value,
                            new_value=value,
                            operation="set",
                        )
                    )

        _merge(data, updates, path or "")

    def get_changes(self) -> list[YAMLChange]:
        """Get list of tracked changes.

        Returns:
            List of changes made since handler was created or cleared.
        """
        return list(self._changes)

    def clear_changes(self) -> None:
        """Clear the tracked changes list."""
        self._changes.clear()

    def validate_path(self, path: str) -> bool:
        """Check if a path is syntactically valid.

        Args:
            path: Dot-separated path.

        Returns:
            True if path syntax is valid.
        """
        if not path:
            return False

        parts = path.split(".")
        for part in parts:
            if not part:
                return False
            # Allow alphanumeric, underscore, hyphen, and digits
            if not all(c.isalnum() or c in "_-" for c in part):
                return False

        return True


# =============================================================================
# Netplan-specific helpers
# =============================================================================


class NetplanHandler(YAMLHandler):
    """Specialized handler for netplan configuration files.

    Provides convenience methods for common netplan operations.
    """

    def __init__(self) -> None:
        """Initialize the netplan handler."""
        super().__init__(indent=2)

    def get_interface(
        self,
        data: dict[str, Any],
        name: str,
        iface_type: str = "ethernets",
    ) -> dict[str, Any] | None:
        """Get configuration for a network interface.

        Args:
            data: Netplan data.
            name: Interface name (e.g., "eth0").
            iface_type: Interface type ("ethernets", "wifis", "bonds", etc.).

        Returns:
            Interface configuration dict, or None if not found.
        """
        return self.get_path(data, f"network.{iface_type}.{name}")

    def set_interface(
        self,
        data: dict[str, Any],
        name: str,
        config: dict[str, Any],
        iface_type: str = "ethernets",
    ) -> None:
        """Set configuration for a network interface.

        Args:
            data: Netplan data (modified in place).
            name: Interface name.
            config: Interface configuration.
            iface_type: Interface type.
        """
        self.set_path(data, f"network.{iface_type}.{name}", config)

    def set_dhcp(
        self,
        data: dict[str, Any],
        interface: str,
        dhcp4: bool | None = None,
        dhcp6: bool | None = None,
        iface_type: str = "ethernets",
    ) -> None:
        """Configure DHCP for an interface.

        Args:
            data: Netplan data (modified in place).
            interface: Interface name.
            dhcp4: Enable/disable DHCPv4.
            dhcp6: Enable/disable DHCPv6.
            iface_type: Interface type.
        """
        base_path = f"network.{iface_type}.{interface}"

        if dhcp4 is not None:
            self.set_path(data, f"{base_path}.dhcp4", dhcp4)
        if dhcp6 is not None:
            self.set_path(data, f"{base_path}.dhcp6", dhcp6)

    def set_static_ip(
        self,
        data: dict[str, Any],
        interface: str,
        addresses: list[str],
        gateway4: str | None = None,
        nameservers: list[str] | None = None,
        iface_type: str = "ethernets",
    ) -> None:
        """Configure static IP for an interface.

        Args:
            data: Netplan data (modified in place).
            interface: Interface name.
            addresses: List of IP addresses with CIDR (e.g., ["192.168.1.10/24"]).
            gateway4: IPv4 gateway address.
            nameservers: List of DNS servers.
            iface_type: Interface type.
        """
        base_path = f"network.{iface_type}.{interface}"

        self.set_path(data, f"{base_path}.dhcp4", False)
        self.set_path(data, f"{base_path}.addresses", addresses)

        if gateway4:
            self.set_path(data, f"{base_path}.routes", [
                {"to": "default", "via": gateway4}
            ])

        if nameservers:
            self.set_path(data, f"{base_path}.nameservers.addresses", nameservers)

    def ensure_structure(self, data: dict[str, Any]) -> None:
        """Ensure netplan has basic structure.

        Args:
            data: Netplan data (modified in place).
        """
        if "network" not in data:
            data["network"] = {}
        if "version" not in data["network"]:
            data["network"]["version"] = 2

    def get_renderer(self, data: dict[str, Any]) -> str | None:
        """Get the network renderer.

        Args:
            data: Netplan data.

        Returns:
            Renderer name ("networkd" or "NetworkManager") or None.
        """
        return self.get_path(data, "network.renderer")

    def set_renderer(
        self,
        data: dict[str, Any],
        renderer: str = "networkd",
    ) -> None:
        """Set the network renderer.

        Args:
            data: Netplan data (modified in place).
            renderer: Renderer name ("networkd" or "NetworkManager").
        """
        self.set_path(data, "network.renderer", renderer)


# =============================================================================
# Module-level convenience functions
# =============================================================================


def load_yaml(file_path: str | Path) -> dict[str, Any]:
    """Load a YAML file (convenience function).

    Args:
        file_path: Path to the YAML file.

    Returns:
        Parsed YAML data.
    """
    handler = YAMLHandler()
    return handler.load(file_path)


def save_yaml(file_path: str | Path, data: dict[str, Any]) -> None:
    """Save data to a YAML file (convenience function).

    Args:
        file_path: Path to save to.
        data: Data to save.
    """
    handler = YAMLHandler()
    handler.save(file_path, data)


def get_yaml_value(
    file_path: str | Path,
    path: str,
) -> Any:
    """Get a value from a YAML file by path (convenience function).

    Args:
        file_path: Path to the YAML file.
        path: Dot-separated path.

    Returns:
        Value at the path.
    """
    handler = YAMLHandler()
    data = handler.load(file_path)
    return handler.get_path(data, path)


def set_yaml_value(
    file_path: str | Path,
    path: str,
    value: Any,
) -> None:
    """Set a value in a YAML file by path (convenience function).

    Args:
        file_path: Path to the YAML file.
        path: Dot-separated path.
        value: Value to set.
    """
    handler = YAMLHandler()
    data = handler.load(file_path)
    handler.set_path(data, path, value)
    handler.save(file_path, data)
