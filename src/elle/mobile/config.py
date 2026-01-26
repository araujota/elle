"""Configuration for ELLE Mobile Gateway.

This module defines the MobileGatewayConfig dataclass and provides
functions for loading configuration from TOML files and environment.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MobileGatewayConfig:
    """Configuration for the Mobile Gateway.

    Attributes:
        enabled: Whether mobile gateway is enabled
        bind_host: Host to bind to (default 0.0.0.0 for LAN access)
        bind_port: Port to bind to
        overlay_host: Optional WireGuard/Tailscale IP to advertise
        pairing_token_ttl_seconds: How long pairing tokens remain valid
        max_paired_devices: Maximum number of paired devices allowed
        default_elevation_ttl_seconds: Default TTL for role elevation
        max_elevation_ttl_seconds: Maximum allowed TTL for elevation
        default_role: Default role for newly paired devices
        internal_api_host: Host of internal API to proxy to
        internal_api_port: Port of internal API to proxy to
        cert_dir: Directory for TLS certificates
        db_path: Path to SQLite database
        audit_db_path: Path to audit log database
        rate_limit_pairing: Max pairing attempts per minute
    """

    enabled: bool = False
    bind_host: str = "0.0.0.0"
    bind_port: int = 8378
    overlay_host: str | None = None
    pairing_token_ttl_seconds: int = 90
    max_paired_devices: int = 10
    default_elevation_ttl_seconds: int = 600  # 10 minutes
    max_elevation_ttl_seconds: int = 3600  # 1 hour
    default_role: str = "mobile_readonly"
    internal_api_host: str = "127.0.0.1"
    internal_api_port: int = 8377
    cert_dir: Path = field(default_factory=lambda: Path("/var/lib/elle/mobile"))
    db_path: Path = field(default_factory=lambda: Path("/var/lib/elle/mobile.db"))
    audit_db_path: Path = field(
        default_factory=lambda: Path("/var/lib/elle/mobile_audit.db")
    )
    rate_limit_pairing: int = 5  # Max pairing attempts per minute


def load_mobile_config(toml_data: dict | None = None) -> MobileGatewayConfig:
    """Load mobile gateway configuration from TOML data and environment.

    Args:
        toml_data: Parsed TOML configuration dictionary.

    Returns:
        MobileGatewayConfig with values from TOML and environment overrides.
    """
    mobile_section = {}
    if toml_data:
        mobile_section = toml_data.get("mobile", {})

    def get_val(key: str, default, env_key: str | None = None):
        """Get value from env, then TOML, then default."""
        env_name = f"ELLE_MOBILE_{env_key or key.upper()}"
        env_val = os.environ.get(env_name)
        if env_val is not None:
            if isinstance(default, bool):
                return env_val.lower() in ("true", "1", "yes")
            if isinstance(default, int):
                try:
                    return int(env_val)
                except ValueError:
                    return default
            return env_val
        return mobile_section.get(key, default)

    return MobileGatewayConfig(
        enabled=get_val("enabled", False),
        bind_host=get_val("bind_host", "0.0.0.0"),
        bind_port=get_val("bind_port", 8378),
        overlay_host=get_val("overlay_host", None),
        pairing_token_ttl_seconds=get_val("pairing_token_ttl_seconds", 90),
        max_paired_devices=get_val("max_paired_devices", 10),
        default_elevation_ttl_seconds=get_val("default_elevation_ttl_seconds", 600),
        max_elevation_ttl_seconds=get_val("max_elevation_ttl_seconds", 3600),
        default_role=get_val("default_role", "mobile_readonly"),
        internal_api_host=get_val("internal_api_host", "127.0.0.1"),
        internal_api_port=get_val("internal_api_port", 8377),
        cert_dir=Path(get_val("cert_dir", "/var/lib/elle/mobile")),
        db_path=Path(get_val("db_path", "/var/lib/elle/mobile.db")),
        audit_db_path=Path(get_val("audit_db_path", "/var/lib/elle/mobile_audit.db")),
        rate_limit_pairing=get_val("rate_limit_pairing", 5),
    )


# Default configuration instance
_mobile_config: MobileGatewayConfig | None = None


def get_mobile_config() -> MobileGatewayConfig:
    """Get the global mobile config, loading if necessary."""
    global _mobile_config
    if _mobile_config is None:
        _mobile_config = load_mobile_config()
    return _mobile_config


def set_mobile_config(config: MobileGatewayConfig) -> None:
    """Set the global mobile config (for testing)."""
    global _mobile_config
    _mobile_config = config


def reset_mobile_config() -> None:
    """Reset the global mobile config (for testing)."""
    global _mobile_config
    _mobile_config = None
