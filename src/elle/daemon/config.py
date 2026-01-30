"""Configuration loading for elled daemon.

Loads configuration from TOML files with sensible defaults.
Configuration search order:
1. /etc/elle/elle.toml (system-wide)
2. ~/.config/elle/elle.toml (user override)
3. Environment variables (ELLE_*)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Try to import tomllib (Python 3.11+) or fall back to tomli/toml
try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore[assignment,unused-ignore]


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
class ApiAuthConfig:
    """Configuration for API authentication.

    Controls how the OpenAI-compatible API authenticates requests.
    By default, anonymous access is DISABLED for security.
    Only authenticated requests (session token, UDS peer, or API key) are allowed.
    """

    # Whether to allow anonymous (unauthenticated) requests
    # SECURITY: Disabled by default. Anonymous requests would be readonly only.
    allow_anonymous: bool = False

    # Path to the API keys database
    # If None, API key authentication is disabled
    api_keys_db_path: Path | None = field(default_factory=lambda: Path("/var/lib/elle/api_keys.db"))

    # UID of the elle user (for UDS peer auth)
    # Users with this UID get full access like root
    elle_uid: int | None = None


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
class MobileConfig:
    """Configuration for the Mobile Gateway."""

    enabled: bool = False
    bind_host: str = "0.0.0.0"
    bind_port: int = 8378
    overlay_host: str | None = None
    pairing_token_ttl_seconds: int = 90
    max_paired_devices: int = 10
    default_elevation_ttl_seconds: int = 600  # 10 minutes
    max_elevation_ttl_seconds: int = 3600  # 1 hour
    default_role: str = "mobile_readonly"


@dataclass(frozen=True)
class DaemonLLMConfig:
    """LLM provider configuration for the daemon."""

    provider_type: str = "ollama"
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct-q8_0"
    api_key: str = field(default="", repr=False)
    timeout: float = 120.0
    max_tokens: int = 4096
    temperature: float = 0.7
    fallback_enabled: bool = True
    fallback_host: str = "http://localhost:11434"
    fallback_model: str = "qwen2.5:7b-instruct-q8_0"
    fallback_retry_interval: float = 60.0


@dataclass(frozen=True)
class CloudSyncConfig:
    """Configuration for cloud incident sync retry queue."""

    enabled: bool = True
    retry_initial_delay_sec: float = 30.0
    retry_max_delay_sec: float = 3600.0
    retry_backoff_factor: float = 2.0
    max_retries: int = 20
    queue_max_size: int = 1000
    batch_size: int = 10
    health_check_interval_sec: float = 60.0
    stale_entry_hours: int = 168  # 7 days
    worker_poll_interval_sec: float = 30.0


@dataclass(frozen=True)
class DatabaseConfig:
    """PostgreSQL connection configuration for the daemon.

    Parsed from the ``[database]`` TOML section.
    Defaults to Unix socket with peer authentication.
    """

    host: str = ""
    port: int = 5432
    dbname: str = "elle"
    user: str = "elle"
    password: str = ""
    socket_dir: str = "/var/run/postgresql"
    min_pool_size: int = 2
    max_pool_size: int = 10
    max_idle_sec: float = 300.0
    sslmode: str = "prefer"
    encryption_key_path: str = "/etc/elle/db.key"


@dataclass(frozen=True)
class Config:
    """Complete daemon configuration."""

    # PostgreSQL configuration
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    # Log level
    log_level: str = "INFO"

    # Subsystem configs
    probes: ProbeConfig = field(default_factory=ProbeConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    api_auth: ApiAuthConfig = field(default_factory=ApiAuthConfig)
    correlation: CorrelationConfig = field(default_factory=CorrelationConfig)
    queues: QueueConfig = field(default_factory=QueueConfig)
    ebpf: EBPFConfig = field(default_factory=EBPFConfig)
    mobile: MobileConfig = field(default_factory=MobileConfig)
    llm: DaemonLLMConfig = field(default_factory=DaemonLLMConfig)
    cloud_sync: CloudSyncConfig = field(default_factory=CloudSyncConfig)

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

    # Capability versioning
    capability_versioning_enabled: bool = True  # Auto-regenerate on package upgrade
    package_probe_interval: int = 300  # 5 minutes

    # Capability bootstrap
    capability_bootstrap_enabled: bool = True  # Generate caps for core packages on first run

    # Auto-learn new packages
    auto_learn_new_packages: bool = True  # Generate caps when new packages are installed


def _deep_get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Get nested dictionary value safely."""
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key, default)
        if d is default:
            return default
    return d


def _load_toml(path: Path) -> dict[str, Any]:
    """Load TOML file if it exists."""
    if not path.exists():
        return {}
    try:
        if tomllib is not None:
            with open(path, "rb") as f:
                result: dict[str, Any] = tomllib.load(f)
        else:
            import toml

            result = toml.load(str(path))
        return result
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

    # Database configuration (PostgreSQL)
    db_section = data.get("database", {})
    database = DatabaseConfig(
        host=_get_env("DB_HOST", db_section.get("host", "")),
        port=_get_env("DB_PORT", db_section.get("port", 5432)),
        dbname=_get_env("DB_NAME", db_section.get("dbname", "elle")),
        user=_get_env("DB_USER", db_section.get("user", "elle")),
        password=os.environ.get("ELLE_DB_PASSWORD", db_section.get("password", "")),
        socket_dir=db_section.get("socket_dir", "/var/run/postgresql"),
        min_pool_size=db_section.get("min_pool_size", 2),
        max_pool_size=db_section.get("max_pool_size", 10),
        max_idle_sec=db_section.get("max_idle_sec", 300.0),
        sslmode=db_section.get("sslmode", "prefer"),
        encryption_key_path=db_section.get("encryption_key_path", "/etc/elle/db.key"),
    )

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

    # API auth config
    api_auth_section = _deep_get(data, "daemon", "api_auth", default={})
    api_keys_db_path_str = api_auth_section.get("api_keys_db_path", "/var/lib/elle/api_keys.db")
    api_auth = ApiAuthConfig(
        allow_anonymous=_get_env(
            "API_AUTH_ALLOW_ANONYMOUS",
            api_auth_section.get("allow_anonymous", True),
        ),
        api_keys_db_path=(Path(api_keys_db_path_str) if api_keys_db_path_str else None),
        elle_uid=api_auth_section.get("elle_uid"),
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
    inotify_watch_paths = inotify_section.get(
        "watch_paths",
        [
            "/root/.ssh/authorized_keys",
            "/etc/ssh/sshd_config",
        ],
    )
    if isinstance(inotify_watch_paths, list):
        inotify_watch_paths = tuple(inotify_watch_paths)

    # Port probe interval
    port_probe_section = _deep_get(data, "daemon", "port_probe", default={})
    port_probe_interval = _get_env(
        "PORT_PROBE_INTERVAL",
        port_probe_section.get("interval", 60),
    )

    # Capability versioning
    capability_versioning_enabled = _get_env(
        "CAPABILITY_VERSIONING_ENABLED",
        daemon_section.get("capability_versioning_enabled", True),
    )
    package_probe_section = _deep_get(data, "daemon", "package_probe", default={})
    package_probe_interval = _get_env(
        "PACKAGE_PROBE_INTERVAL",
        package_probe_section.get("interval", 300),
    )

    # Auto-learn new packages
    auto_learn_new_packages = _get_env(
        "AUTO_LEARN_NEW_PACKAGES",
        daemon_section.get("auto_learn_new_packages", True),
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

    # Mobile gateway config
    mobile_section = _deep_get(data, "mobile", default={})
    mobile = MobileConfig(
        enabled=_get_env("MOBILE_ENABLED", mobile_section.get("enabled", False)),
        bind_host=_get_env("MOBILE_BIND_HOST", mobile_section.get("bind_host", "0.0.0.0")),
        bind_port=_get_env("MOBILE_BIND_PORT", mobile_section.get("bind_port", 8378)),
        overlay_host=mobile_section.get("overlay_host"),
        pairing_token_ttl_seconds=mobile_section.get("pairing_token_ttl_seconds", 90),
        max_paired_devices=mobile_section.get("max_paired_devices", 10),
        default_elevation_ttl_seconds=mobile_section.get("default_elevation_ttl_seconds", 600),
        max_elevation_ttl_seconds=mobile_section.get("max_elevation_ttl_seconds", 3600),
        default_role=mobile_section.get("default_role", "mobile_readonly"),
    )

    # LLM config
    llm_section = data.get("llm", {})
    llm_provider_section = llm_section.get("provider", {})
    llm_fallback_section = llm_section.get("fallback", {})

    # API key from env var takes priority
    llm_api_key = os.environ.get(
        "ELLE_LLM_API_KEY",
        llm_provider_section.get("api_key", ""),
    )

    llm_config = DaemonLLMConfig(
        provider_type=os.environ.get(
            "ELLE_LLM_PROVIDER_TYPE",
            llm_provider_section.get("type", "ollama"),
        ),
        host=os.environ.get(
            "ELLE_LLM_PROVIDER_HOST",
            llm_provider_section.get("host", "http://localhost:11434"),
        ),
        model=os.environ.get(
            "ELLE_LLM_PROVIDER_MODEL",
            llm_provider_section.get("model", "qwen2.5:7b-instruct-q8_0"),
        ),
        api_key=llm_api_key,
        timeout=llm_provider_section.get("timeout", 120.0),
        max_tokens=llm_provider_section.get("max_tokens", 4096),
        temperature=llm_provider_section.get("temperature", 0.7),
        fallback_enabled=_get_env(
            "ELLE_LLM_FALLBACK_ENABLED",
            llm_fallback_section.get("enabled", True),
        ),
        fallback_host=llm_fallback_section.get("host", "http://localhost:11434"),
        fallback_model=llm_fallback_section.get("model", "qwen2.5:7b-instruct-q8_0"),
        fallback_retry_interval=llm_fallback_section.get("retry_interval", 60.0),
    )

    # Cloud sync config
    cloud_sync_section = _deep_get(data, "daemon", "cloud_sync", default={})
    cloud_sync = CloudSyncConfig(
        enabled=_get_env(
            "ELLE_CLOUD_SYNC_ENABLED",
            cloud_sync_section.get("enabled", True),
        ),
        retry_initial_delay_sec=cloud_sync_section.get("retry_initial_delay_sec", 30.0),
        retry_max_delay_sec=cloud_sync_section.get("retry_max_delay_sec", 3600.0),
        retry_backoff_factor=cloud_sync_section.get("retry_backoff_factor", 2.0),
        max_retries=cloud_sync_section.get("max_retries", 20),
        queue_max_size=cloud_sync_section.get("queue_max_size", 1000),
        batch_size=cloud_sync_section.get("batch_size", 10),
        health_check_interval_sec=cloud_sync_section.get("health_check_interval_sec", 60.0),
        stale_entry_hours=cloud_sync_section.get("stale_entry_hours", 168),
        worker_poll_interval_sec=cloud_sync_section.get("worker_poll_interval_sec", 30.0),
    )

    return Config(
        database=database,
        log_level=log_level,
        probes=probes,
        thresholds=thresholds,
        api=api,
        api_auth=api_auth,
        correlation=correlation,
        queues=queues,
        journal_enabled=journal_enabled,
        kernel_enabled=kernel_enabled,
        probes_enabled=probes_enabled,
        ebpf=ebpf,
        ebpf_enabled=ebpf_enabled,
        mobile=mobile,
        llm=llm_config,
        cloud_sync=cloud_sync,
        docker_enabled=docker_enabled,
        inotify_enabled=inotify_enabled,
        wireguard_enabled=wireguard_enabled,
        port_probe_enabled=port_probe_enabled,
        inotify_watch_paths=inotify_watch_paths,
        port_probe_interval=port_probe_interval,
        capability_versioning_enabled=capability_versioning_enabled,
        package_probe_interval=package_probe_interval,
        auto_learn_new_packages=auto_learn_new_packages,
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
