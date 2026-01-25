"""Configuration loading for elled daemon.

Loads configuration from TOML files with sensible defaults.
Configuration search order:
1. /etc/elle/elle.toml (system-wide)
2. ~/.config/elle/elle.toml (user override)
3. Environment variables (ELLE_*)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Try to import tomllib (Python 3.11+) or fall back to tomli
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore


# Default configuration paths
SYSTEM_CONFIG = Path("/etc/elle/elle.toml")
USER_CONFIG = Path.home() / ".config" / "elle" / "elle.toml"


@dataclass(frozen=True)
class ProbeConfig:
    """Configuration for periodic probes."""

    memory_interval: int = 30  # seconds
    disk_interval: int = 300  # 5 minutes
    network_interval: int = 60  # seconds
    thermal_interval: int = 60  # seconds
    smart_interval: int = 3600  # 1 hour


@dataclass(frozen=True)
class ThresholdConfig:
    """Thresholds for generating alerts."""

    memory_warning_pct: float = 0.85  # Memory pressure threshold
    disk_warning_pct: float = 0.90  # Disk usage threshold
    thermal_warning_c: float = 80.0  # Temperature threshold in Celsius
    network_error_threshold: int = 10  # Error count to trigger alert


@dataclass(frozen=True)
class ApiConfig:
    """Configuration for the REST API."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8377


@dataclass(frozen=True)
class CorrelationConfig:
    """Configuration for event correlation."""

    time_window_sec: int = 600  # 10 minutes
    dedupe_window_sec: int = 60  # 1 minute
    min_events_for_incident: int = 1
    stale_incident_sec: int = 900  # 15 minutes


@dataclass(frozen=True)
class QueueConfig:
    """Configuration for internal queues."""

    raw_queue_size: int = 10000
    event_queue_size: int = 5000


@dataclass(frozen=True)
class EBPFConfig:
    """Configuration for eBPF telemetry."""

    enabled: bool = True  # Master enable/disable for eBPF
    programs: tuple[str, ...] = (  # Programs to load
        "oom",
        "block_io",
        "process",
        "thermal",
        "net_drops",
    )


@dataclass(frozen=True)
class Config:
    """Complete daemon configuration."""

    # Database paths
    db_path: Path = field(default_factory=lambda: Path("/var/lib/elle/elle.db"))
    incidents_db_path: Path = field(default_factory=lambda: Path("/var/lib/elle/incidents.db"))
    manvault_db_path: Path = field(default_factory=lambda: Path("/var/lib/elle/manvault.db"))

    # Log level
    log_level: str = "INFO"

    # Subsystem configs
    probes: ProbeConfig = field(default_factory=ProbeConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    correlation: CorrelationConfig = field(default_factory=CorrelationConfig)
    queues: QueueConfig = field(default_factory=QueueConfig)
    ebpf: EBPFConfig = field(default_factory=EBPFConfig)

    # Feature flags
    journal_enabled: bool = True
    kernel_enabled: bool = True
    probes_enabled: bool = True
    ebpf_enabled: bool = True

    # New telemetry source flags
    docker_enabled: bool = True  # Docker events watcher
    inotify_enabled: bool = True  # File change monitoring via inotify
    wireguard_enabled: bool = True  # WireGuard handshake monitoring
    port_probe_enabled: bool = True  # Port listener detection probe

    # Inotify configuration
    inotify_watch_paths: tuple[str, ...] = (
        "/root/.ssh/authorized_keys",
        "/etc/ssh/sshd_config",
    )

    # Port probe configuration
    port_probe_interval: int = 60  # seconds


def _deep_get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Get nested dictionary value safely."""
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key, default)  # type: ignore
        if d is default:
            return default
    return d


def _load_toml(path: Path) -> dict[str, Any]:
    """Load TOML file if it exists."""
    if tomllib is None:
        return {}
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _get_env(key: str, default: Any = None) -> Any:
    """Get environment variable with ELLE_ prefix."""
    value = os.environ.get(f"ELLE_{key.upper()}")
    if value is None:
        return default
    # Type coercion based on default type
    if isinstance(default, bool):
        return value.lower() in ("true", "1", "yes")
    if isinstance(default, int):
        try:
            return int(value)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except ValueError:
            return default
    return value


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from files and environment.

    Args:
        config_path: Optional explicit config file path.

    Returns:
        Loaded Config object.
    """
    # Merge configuration sources
    data: dict[str, Any] = {}

    # Load system config first
    system_data = _load_toml(SYSTEM_CONFIG)
    data.update(system_data)

    # Override with user config
    user_data = _load_toml(USER_CONFIG)
    _merge_dict(data, user_data)

    # Override with explicit config path
    if config_path:
        explicit_data = _load_toml(config_path)
        _merge_dict(data, explicit_data)

    # Build config object
    daemon_section = data.get("daemon", {})

    # Database paths
    db_path = Path(daemon_section.get("db_path", "/var/lib/elle/elle.db"))
    incidents_db_path = Path(
        daemon_section.get("incidents_db_path", "/var/lib/elle/incidents.db")
    )
    manvault_db_path = Path(
        daemon_section.get("manvault_db_path", "/var/lib/elle/manvault.db")
    )

    # Override with environment
    db_path = Path(_get_env("DB_PATH", str(db_path)))
    log_level = _get_env("LOG_LEVEL", daemon_section.get("log_level", "INFO"))

    # Probe config
    probes_section = _deep_get(data, "daemon", "probes", default={})
    probes = ProbeConfig(
        memory_interval=_get_env(
            "PROBE_MEMORY_INTERVAL",
            probes_section.get("memory_interval", 30),
        ),
        disk_interval=_get_env(
            "PROBE_DISK_INTERVAL",
            probes_section.get("disk_interval", 300),
        ),
        network_interval=_get_env(
            "PROBE_NETWORK_INTERVAL",
            probes_section.get("network_interval", 60),
        ),
        thermal_interval=_get_env(
            "PROBE_THERMAL_INTERVAL",
            probes_section.get("thermal_interval", 60),
        ),
        smart_interval=_get_env(
            "PROBE_SMART_INTERVAL",
            probes_section.get("smart_interval", 3600),
        ),
    )

    # Threshold config
    thresholds_section = _deep_get(data, "daemon", "thresholds", default={})
    thresholds = ThresholdConfig(
        memory_warning_pct=thresholds_section.get("memory_warning_pct", 0.85),
        disk_warning_pct=thresholds_section.get("disk_warning_pct", 0.90),
        thermal_warning_c=thresholds_section.get("thermal_warning_c", 80.0),
        network_error_threshold=thresholds_section.get("network_error_threshold", 10),
    )

    # API config
    api_section = _deep_get(data, "daemon", "api", default={})
    api_enabled = _get_env("API_ENABLED", api_section.get("enabled", True))
    api = ApiConfig(
        enabled=api_enabled,
        host=_get_env("API_HOST", api_section.get("host", "127.0.0.1")),
        port=_get_env("API_PORT", api_section.get("port", 8377)),
    )

    # Correlation config
    correlation_section = _deep_get(data, "daemon", "correlation", default={})
    correlation = CorrelationConfig(
        time_window_sec=correlation_section.get("time_window_sec", 600),
        dedupe_window_sec=correlation_section.get("dedupe_window_sec", 60),
        min_events_for_incident=correlation_section.get("min_events_for_incident", 1),
        stale_incident_sec=correlation_section.get("stale_incident_sec", 900),
    )

    # Queue config
    queues_section = _deep_get(data, "daemon", "queues", default={})
    queues = QueueConfig(
        raw_queue_size=queues_section.get("raw_queue_size", 10000),
        event_queue_size=queues_section.get("event_queue_size", 5000),
    )

    # Feature flags
    journal_enabled = _get_env(
        "JOURNAL_ENABLED",
        daemon_section.get("journal_enabled", True),
    )
    kernel_enabled = _get_env(
        "KERNEL_ENABLED",
        daemon_section.get("kernel_enabled", True),
    )
    probes_enabled = _get_env(
        "PROBES_ENABLED",
        daemon_section.get("probes_enabled", True),
    )
    ebpf_enabled = _get_env(
        "EBPF_ENABLED",
        daemon_section.get("ebpf_enabled", True),
    )

    # New telemetry source flags
    docker_enabled = _get_env(
        "DOCKER_ENABLED",
        daemon_section.get("docker_enabled", True),
    )
    inotify_enabled = _get_env(
        "INOTIFY_ENABLED",
        daemon_section.get("inotify_enabled", True),
    )
    wireguard_enabled = _get_env(
        "WIREGUARD_ENABLED",
        daemon_section.get("wireguard_enabled", True),
    )
    port_probe_enabled = _get_env(
        "PORT_PROBE_ENABLED",
        daemon_section.get("port_probe_enabled", True),
    )

    # Inotify watch paths
    inotify_section = _deep_get(data, "daemon", "inotify", default={})
    inotify_watch_paths = inotify_section.get("watch_paths", [
        "/root/.ssh/authorized_keys",
        "/etc/ssh/sshd_config",
    ])
    if isinstance(inotify_watch_paths, list):
        inotify_watch_paths = tuple(inotify_watch_paths)

    # Port probe interval
    port_probe_section = _deep_get(data, "daemon", "port_probe", default={})
    port_probe_interval = _get_env(
        "PORT_PROBE_INTERVAL",
        port_probe_section.get("interval", 60),
    )

    # eBPF config
    ebpf_section = _deep_get(data, "daemon", "ebpf", default={})
    ebpf_programs = ebpf_section.get(
        "programs",
        ["oom", "block_io", "process", "thermal", "net_drops"],
    )
    ebpf = EBPFConfig(
        enabled=_get_env("EBPF_ENABLED", ebpf_section.get("enabled", True)),
        programs=tuple(ebpf_programs),
    )

    return Config(
        db_path=db_path,
        incidents_db_path=incidents_db_path,
        manvault_db_path=manvault_db_path,
        log_level=log_level,
        probes=probes,
        thresholds=thresholds,
        api=api,
        correlation=correlation,
        queues=queues,
        journal_enabled=journal_enabled,
        kernel_enabled=kernel_enabled,
        probes_enabled=probes_enabled,
        ebpf=ebpf,
        ebpf_enabled=ebpf_enabled,
        docker_enabled=docker_enabled,
        inotify_enabled=inotify_enabled,
        wireguard_enabled=wireguard_enabled,
        port_probe_enabled=port_probe_enabled,
        inotify_watch_paths=inotify_watch_paths,
        port_probe_interval=port_probe_interval,
    )


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Recursively merge override into base dict."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge_dict(base[key], value)
        else:
            base[key] = value


# Default config singleton
_config: Config | None = None


def get_config() -> Config:
    """Get the global config, loading if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: Config) -> None:
    """Set the global config (for testing)."""
    global _config
    _config = config


def reset_config() -> None:
    """Reset the global config (for testing)."""
    global _config
    _config = None
