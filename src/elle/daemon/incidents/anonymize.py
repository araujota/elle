"""Incident anonymization for cloud storage.

Produces anonymized versions of incident reports suitable for aggregation
into a global knowledge base while protecting sensitive information:
- System identity (hostnames, IPs)
- Infrastructure details (specific paths, service names)
- Operational secrets (commands with credentials, log snippets)
- User identity (usernames, groups)

Three-tier classification:
- REDACT: Replace with placeholder or hash (hostnames, commands, logs)
- GENERALIZE: Normalize to category (mount points, timestamps)
- PRESERVE: Keep as-is (metrics, version strings, hashes, enums)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentAction,
    IncidentDomain,
    IncidentOutcome,
    IncidentReport,
    IncidentSeverity,
    IncidentStatus,
)

# =============================================================================
# Constants
# =============================================================================

# Well-known services that are safe to preserve (not custom/private)
KNOWN_SERVICES: frozenset[str] = frozenset(
    {
        # Web servers
        "nginx",
        "nginx.service",
        "apache2",
        "apache2.service",
        "httpd",
        "httpd.service",
        # Databases
        "postgresql",
        "postgresql.service",
        "postgres",
        "mysql",
        "mysql.service",
        "mariadb",
        "mariadb.service",
        "redis",
        "redis.service",
        "redis-server",
        "mongodb",
        "mongodb.service",
        "memcached",
        "memcached.service",
        # Container runtime
        "docker",
        "docker.service",
        "containerd",
        "containerd.service",
        "podman",
        "podman.service",
        "kubelet",
        "kubelet.service",
        # System services
        "ssh",
        "sshd",
        "sshd.service",
        "cron",
        "cron.service",
        "systemd-resolved",
        "systemd-networkd",
        "systemd-journald",
        "systemd-logind",
        "systemd-timesyncd",
        "systemd-udevd",
        "NetworkManager",
        "NetworkManager.service",
        # Monitoring
        "prometheus",
        "grafana",
        "grafana-server",
        "node_exporter",
        "telegraf",
        "collectd",
        "datadog-agent",
        # Message queues
        "rabbitmq",
        "rabbitmq-server",
        "kafka",
        "zookeeper",
        # Other common services
        "postfix",
        "dovecot",
        "haproxy",
        "varnish",
        "fail2ban",
        "ufw",
        "iptables",
        "nftables",
        "firewalld",
        "dnsmasq",
        "bind9",
        "named",
        "ntp",
        "ntpd",
        "chrony",
        "chronyd",
        "rsyslog",
        "syslog-ng",
        "journald",
        "unattended-upgrades",
        "apt-daily",
        "apt-daily-upgrade",
        "snapd",
        "packagekit",
        "cups",
        "avahi-daemon",
        "bluetooth",
        "ModemManager",
        "gdm",
        "lightdm",
        "sddm",
        "ollama",
        "ollama.service",
    }
)

# Mount point normalization categories
MOUNT_CATEGORIES: dict[str, str] = {
    "/": "root",
    "/home": "home",
    "/var": "var",
    "/tmp": "tmp",
    "/boot": "boot",
    "/srv": "data",
    "/opt": "opt",
    "/usr": "usr",
    "/mnt": "mnt",
    "/media": "media",
    "/etc": "etc",
}

# Installation-specific secret for consistent hashing
# This should be generated once per installation and stored
_ANON_SECRET: bytes | None = None


def _get_anon_secret() -> bytes:
    """Get or generate the installation-specific anonymization secret."""
    global _ANON_SECRET
    if _ANON_SECRET is None:
        # In production, this should be loaded from a persistent location
        # For now, generate a session-specific secret
        # TODO: Load from /var/lib/elle/anon_secret when available
        _ANON_SECRET = secrets.token_bytes(32)
    return _ANON_SECRET


def set_anon_secret(secret: bytes) -> None:
    """Set the anonymization secret (for testing or initialization)."""
    global _ANON_SECRET
    _ANON_SECRET = secret


# =============================================================================
# Anonymization Context
# =============================================================================


class AnonymizationContext:
    """Maintains consistent anonymization mappings within a single incident.

    Ensures that the same identifier (hostname, path, service) gets the same
    anonymized value throughout a single incident, enabling pattern matching
    while preventing identity correlation.
    """

    def __init__(self, secret: bytes | None = None) -> None:
        """Initialize the anonymization context.

        Args:
            secret: HMAC secret for consistent hashing. If None, uses installation secret.
        """
        self._secret = secret or _get_anon_secret()
        self._hostname_map: dict[str, str] = {}
        self._path_map: dict[str, str] = {}
        self._service_map: dict[str, str] = {}
        self._interface_map: dict[str, str] = {}
        self._device_map: dict[str, str] = {}
        self._user_map: dict[str, str] = {}

    def _hmac_hash(self, value: str, prefix: str = "") -> str:
        """Generate a consistent hash for a value using HMAC.

        Args:
            value: The value to hash.
            prefix: Optional prefix for the result.

        Returns:
            Hashed value with prefix, e.g., "anon-abc123"
        """
        digest = hmac.new(self._secret, value.encode(), hashlib.sha256).hexdigest()[:12]
        return f"{prefix}{digest}" if prefix else digest

    def anonymize_hostname(self, hostname: str) -> str:
        """Anonymize a hostname consistently."""
        if not hostname:
            return ""
        if hostname not in self._hostname_map:
            self._hostname_map[hostname] = self._hmac_hash(hostname, "anon-")
        return self._hostname_map[hostname]

    def anonymize_service_name(self, service: str) -> str:
        """Anonymize a service name, preserving well-known services."""
        if not service:
            return ""
        # Strip .service suffix for comparison
        base_name = service.removesuffix(".service")
        if service in KNOWN_SERVICES or base_name in KNOWN_SERVICES:
            return service
        if service not in self._service_map:
            self._service_map[service] = self._hmac_hash(service, "svc-")
        return self._service_map[service]

    def anonymize_path(self, path: str) -> str:
        """Anonymize a file path to a generalized pattern."""
        if not path:
            return ""
        if path not in self._path_map:
            self._path_map[path] = self._generalize_path(path)
        return self._path_map[path]

    def _generalize_path(self, path: str) -> str:
        """Generalize a path to a pattern."""
        if not path.startswith("/"):
            return self._hmac_hash(path, "path-")

        # Split path into components
        parts = path.split("/")

        # Keep well-known root directories
        if len(parts) > 1:
            root = f"/{parts[1]}"
            if root in MOUNT_CATEGORIES:
                # For well-known config paths, keep the structure but anonymize specifics
                if root in ("/etc", "/var"):
                    # Keep first two levels for config paths
                    if len(parts) > 2:
                        # e.g., /etc/nginx/... -> /etc/nginx/[config]
                        return f"/{parts[1]}/{parts[2]}/[config]"
                    # Just the root, e.g., /etc -> etc/[path]
                    return f"{MOUNT_CATEGORIES[root]}/[path]"
                return f"{MOUNT_CATEGORIES[root]}/[path]"

        return self._hmac_hash(path, "path-")

    def anonymize_interface(self, interface: str) -> str:
        """Anonymize a network interface name."""
        if not interface:
            return ""
        # Preserve generic interface types
        if interface in ("lo", "localhost"):
            return interface
        if interface.startswith(("eth", "ens", "enp", "wlan", "wlp")):
            # Keep the type prefix but anonymize the rest
            for prefix in ("eth", "ens", "enp", "wlan", "wlp"):
                if interface.startswith(prefix):
                    return f"{prefix}N"
        if interface not in self._interface_map:
            idx = len(self._interface_map)
            self._interface_map[interface] = f"if{idx}"
        return self._interface_map[interface]

    def anonymize_device(self, device: str) -> str:
        """Anonymize a device path."""
        if not device:
            return ""
        if device not in self._device_map:
            idx = len(self._device_map)
            self._device_map[device] = f"disk-{idx}"
        return self._device_map[device]

    def anonymize_user(self, user: str) -> str:
        """Anonymize a username."""
        if not user:
            return ""
        # Preserve well-known system users
        system_users = {"root", "nobody", "www-data", "nginx", "postgres", "mysql", "redis"}
        if user in system_users:
            return user
        if user not in self._user_map:
            self._user_map[user] = self._hmac_hash(user, "user-")
        return self._user_map[user]


# =============================================================================
# Anonymized Models
# =============================================================================


class ActionSummary(BaseModel):
    """Summary of actions taken during incident resolution."""

    model_config = ConfigDict(frozen=True)

    total_actions: int = Field(ge=0, default=0, description="Total number of actions")
    successful_actions: int = Field(ge=0, default=0, description="Number of successful actions")
    failed_actions: int = Field(ge=0, default=0, description="Number of failed actions")

    # Counts by kind
    shell_count: int = Field(ge=0, default=0)
    capability_count: int = Field(ge=0, default=0)
    edit_count: int = Field(ge=0, default=0)
    verify_count: int = Field(ge=0, default=0)
    rollback_count: int = Field(ge=0, default=0)
    privileged_count: int = Field(ge=0, default=0)
    gui_count: int = Field(ge=0, default=0)

    # Timing
    total_duration_ms: int = Field(ge=0, default=0, description="Sum of action durations")
    avg_duration_ms: int = Field(ge=0, default=0, description="Average action duration")


class DriftExplanation(BaseModel):
    """Human-readable explanation of a specific surface drift."""

    model_config = ConfigDict(frozen=True)

    surface: str = Field(description="Surface key (e.g., 'service:nginx.service')")
    changed: bool = Field(description="Whether this surface changed")
    explanation: str = Field(default="", description="Human-readable explanation of the change")
    before_value: str | None = Field(default=None, description="Value before (if available)")
    after_value: str | None = Field(default=None, description="Value after (if available)")


class AnonymizedIncidentReport(BaseModel):
    """Cloud-safe anonymized version of IncidentReport.

    Contains enough information for pattern matching and aggregate analysis
    while protecting sensitive system and user information.

    Supports two detail levels:
    - "hashes": Only surface hashes (default, most private)
    - "detailed": Full anonymized control surfaces (opt-in for richer sharing)
    """

    model_config = ConfigDict(frozen=True)

    # Identity (preserved - UUIDs are anonymous by design)
    incident_id: str = Field(description="Original incident UUID")

    # Timestamps (generalized to hour)
    created_at_hour: datetime = Field(description="Creation time rounded to hour")
    updated_at_hour: datetime = Field(description="Update time rounded to hour")

    # Classification (preserved - enum values are safe)
    domain: IncidentDomain = Field(default="other")
    severity: IncidentSeverity = Field(default="warning")
    status: IncidentStatus = Field(default="open")
    outcome: IncidentOutcome = Field(default="unknown")

    # Trigger source (preserved)
    trigger_source: str = Field(default="manual")

    # Similarity fingerprint (preserved - all metrics)
    fingerprint: Fingerprint = Field(default_factory=Fingerprint)

    # Surface hashes (preserved - already anonymous hashes)
    surface_hashes_pre: dict[str, str] | None = Field(default=None)
    surface_hashes_post: dict[str, str] | None = Field(default=None)
    surface_drift: dict[str, bool] = Field(default_factory=dict)

    # Drift explanations (computed from control surfaces when available)
    drift_explanations: tuple[DriftExplanation, ...] = Field(
        default_factory=tuple,
        description="Human-readable drift explanations (requires detailed mode or local data)",
    )

    # Anonymized telemetry (metrics only, no identifiers)
    telemetry_pre: dict[str, Any] | None = Field(default=None)
    telemetry_post: dict[str, Any] | None = Field(default=None)

    # Anonymized control surfaces (optional - only in detailed mode)
    # These provide richer context for matching but require team opt-in
    control_surface_pre: dict[str, Any] | None = Field(
        default=None,
        description="Anonymized control surface before (detailed mode only)",
    )
    control_surface_post: dict[str, Any] | None = Field(
        default=None,
        description="Anonymized control surface after (detailed mode only)",
    )

    # Action summary (counts by kind, success rate)
    action_summary: ActionSummary = Field(default_factory=ActionSummary)

    # Confidence (preserved)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)

    # Resolution time metrics (preserved)
    time_to_mitigate_sec: int | None = Field(default=None)
    time_to_resolve_sec: int | None = Field(default=None)

    # Anonymization metadata
    anonymization_version: str = Field(default="1.0")
    detail_level: str = Field(default="hashes", description="'hashes' or 'detailed'")
    original_hash: str = Field(default="", description="SHA256 of original for deduplication")


# =============================================================================
# Incident Anonymizer
# =============================================================================


class IncidentAnonymizer:
    """Transforms IncidentReport -> AnonymizedIncidentReport.

    Applies the three-tier classification system:
    - REDACT: Replace with placeholder or hash
    - GENERALIZE: Normalize to category
    - PRESERVE: Keep as-is
    """

    def __init__(self, secret: bytes | None = None) -> None:
        """Initialize the anonymizer.

        Args:
            secret: HMAC secret for consistent hashing. If None, uses installation secret.
        """
        self._secret = secret

    def anonymize(
        self,
        incident: IncidentReport,
        actions: list[IncidentAction] | None = None,
        telemetry_pre: Any | None = None,
        telemetry_post: Any | None = None,
        surface_hashes_pre: dict[str, str] | None = None,
        surface_hashes_post: dict[str, str] | None = None,
        control_surface_pre: Any | None = None,
        control_surface_post: Any | None = None,
        detail_level: str = "hashes",
    ) -> AnonymizedIncidentReport:
        """Transform an incident report into its anonymized version.

        Args:
            incident: The original incident report.
            actions: Optional list of incident actions.
            telemetry_pre: Optional pre-state telemetry snapshot.
            telemetry_post: Optional post-state telemetry snapshot.
            surface_hashes_pre: Optional pre-state control surface hashes.
            surface_hashes_post: Optional post-state control surface hashes.
            control_surface_pre: Optional pre-state control surface snapshot.
            control_surface_post: Optional post-state control surface snapshot.
            detail_level: "hashes" (default) or "detailed" for richer cloud sharing.

        Returns:
            AnonymizedIncidentReport suitable for cloud storage.
        """
        ctx = AnonymizationContext(self._secret)

        # Compute hash of original for deduplication
        original_hash = self._compute_incident_hash(incident)

        # Anonymize telemetry snapshots
        anon_telemetry_pre = None
        anon_telemetry_post = None
        if telemetry_pre is not None:
            anon_telemetry_pre = self._anonymize_telemetry(telemetry_pre, ctx)
        if telemetry_post is not None:
            anon_telemetry_post = self._anonymize_telemetry(telemetry_post, ctx)

        # Also try to get telemetry from incident if available
        if anon_telemetry_pre is None and incident.telemetry_pre is not None:
            anon_telemetry_pre = self._anonymize_telemetry(incident.telemetry_pre, ctx)
        if anon_telemetry_post is None and incident.telemetry_post is not None:
            anon_telemetry_post = self._anonymize_telemetry(incident.telemetry_post, ctx)

        # Get control surfaces from incident if not provided
        if control_surface_pre is None:
            control_surface_pre = incident.control_surface_pre
        if control_surface_post is None:
            control_surface_post = incident.control_surface_post

        # Preserve surface hashes as-is (keys are public patterns, values are one-way hashes)
        anon_surface_pre = self._preserve_surface_hashes(surface_hashes_pre)
        anon_surface_post = self._preserve_surface_hashes(surface_hashes_post)

        # Compute action summary
        action_summary = self._compute_action_summary(actions or [])

        # Compute surface drift
        surface_drift: dict[str, bool] = {}
        if anon_surface_pre and anon_surface_post:
            for key in set(anon_surface_pre.keys()) | set(anon_surface_post.keys()):
                pre_hash = anon_surface_pre.get(key)
                post_hash = anon_surface_post.get(key)
                surface_drift[key] = pre_hash != post_hash

        # Generate drift explanations when control surfaces are available
        drift_explanations: list[DriftExplanation] = []
        if control_surface_pre is not None and control_surface_post is not None:
            drift_explanations = self._compute_drift_explanations(
                control_surface_pre, control_surface_post, surface_drift, ctx
            )

        # Anonymize control surfaces for detailed mode
        anon_control_pre = None
        anon_control_post = None
        if detail_level == "detailed":
            if control_surface_pre is not None:
                anon_control_pre = self._anonymize_control_surface(control_surface_pre, ctx)
            if control_surface_post is not None:
                anon_control_post = self._anonymize_control_surface(control_surface_post, ctx)

        return AnonymizedIncidentReport(
            incident_id=incident.incident_id,
            created_at_hour=self._generalize_timestamp(incident.created_at),
            updated_at_hour=self._generalize_timestamp(incident.updated_at),
            domain=incident.domain,
            severity=incident.severity,
            status=incident.status,
            outcome=incident.outcome,
            trigger_source=incident.trigger_source,
            fingerprint=incident.fingerprint,
            surface_hashes_pre=anon_surface_pre,
            surface_hashes_post=anon_surface_post,
            surface_drift=surface_drift if surface_drift else incident.surface_drift,
            drift_explanations=tuple(drift_explanations),
            telemetry_pre=anon_telemetry_pre,
            telemetry_post=anon_telemetry_post,
            control_surface_pre=anon_control_pre,
            control_surface_post=anon_control_post,
            action_summary=action_summary,
            confidence=incident.confidence,
            time_to_mitigate_sec=incident.time_to_mitigate_sec,
            time_to_resolve_sec=incident.time_to_resolve_sec,
            anonymization_version="1.0",
            detail_level=detail_level,
            original_hash=original_hash,
        )

    def _generalize_timestamp(self, dt: datetime) -> datetime:
        """Round timestamp to the hour for temporal anonymization."""
        return dt.replace(minute=0, second=0, microsecond=0)

    def _compute_incident_hash(self, incident: IncidentReport) -> str:
        """Compute a hash of the original incident for deduplication."""
        # Hash key identifying fields
        content = f"{incident.incident_id}:{incident.created_at.isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _anonymize_telemetry(self, telemetry: Any, ctx: AnonymizationContext) -> dict[str, Any]:
        """Anonymize a telemetry snapshot, preserving metrics but removing identifiers.

        Args:
            telemetry: TelemetrySnapshot model or dict.
            ctx: Anonymization context for consistent mapping.

        Returns:
            Dict with anonymized telemetry data.
        """
        # Convert to dict if it's a Pydantic model
        if hasattr(telemetry, "model_dump"):
            data = telemetry.model_dump()
        elif isinstance(telemetry, dict):
            data = telemetry
        else:
            return {}

        result: dict[str, Any] = {}

        # PRESERVE: CPU metrics (all safe numeric values)
        if "cpu" in data:
            result["cpu"] = data["cpu"]

        # PRESERVE: Memory metrics (all safe numeric values)
        if "memory" in data:
            result["memory"] = data["memory"]

        # GENERALIZE: Disk metrics
        if "disk" in data:
            disk_data = data["disk"]
            anon_disk: dict[str, Any] = {
                "io_wait_pct": disk_data.get("io_wait_pct", 0.0),
                "io_errors_1h": disk_data.get("io_errors_1h", 0),
                "psi_some_pct": disk_data.get("psi_some_pct", 0.0),
                "psi_full_pct": disk_data.get("psi_full_pct", 0.0),
            }
            # Anonymize individual disks
            anon_disks = []
            for disk in disk_data.get("disks", []):
                anon_disks.append(
                    {
                        "mount": self._generalize_mount_point(disk.get("mount", "")),
                        "used_pct": disk.get("used_pct", 0.0),
                        "avail_gb": disk.get("avail_gb", 0.0),
                        "device": ctx.anonymize_device(disk.get("device", "")),
                        "read_only": disk.get("read_only", False),
                    }
                )
            anon_disk["disks"] = anon_disks
            result["disk"] = anon_disk

        # GENERALIZE: Network metrics
        if "network" in data:
            net_data = data["network"]
            anon_net: dict[str, Any] = {
                "interfaces_up": net_data.get("interfaces_up", 0),
                "interfaces_down": net_data.get("interfaces_down", 0),
                "default_route_present": net_data.get("default_route_present", False),
                "dns_resolution_ok": net_data.get("dns_resolution_ok", False),
            }
            # Anonymize interfaces
            anon_interfaces = []
            for iface in net_data.get("interfaces", []):
                anon_interfaces.append(
                    {
                        "name": ctx.anonymize_interface(iface.get("name", "")),
                        "state": iface.get("state", "UNKNOWN"),
                        "rx_errors": iface.get("rx_errors", 0),
                        "tx_errors": iface.get("tx_errors", 0),
                    }
                )
            anon_net["interfaces"] = anon_interfaces
            result["network"] = anon_net

        # PRESERVE: Kernel/hardware signals (all safe numeric/boolean values)
        if "kernel" in data:
            result["kernel"] = data["kernel"]

        # GENERALIZE: Service runtime
        if "services" in data:
            anon_services = []
            for svc in data.get("services", []):
                anon_services.append(
                    {
                        "service": ctx.anonymize_service_name(svc.get("service", "")),
                        # REDACT: pid
                        "rss_mb": svc.get("rss_mb", 0),
                        "cpu_pct": svc.get("cpu_pct", 0.0),
                        "active_state": svc.get("active_state", "unknown"),
                        "restart_count": svc.get("restart_count", 0),
                        "last_exit_code": svc.get("last_exit_code"),
                        "cgroup_memory_limit_mb": svc.get("cgroup_memory_limit_mb"),
                    }
                )
            result["services"] = anon_services

        # REDACT: hostname
        # PRESERVE: uptime_sec
        result["uptime_sec"] = data.get("uptime_sec", 0)

        return result

    def _anonymize_control_surface(self, surface: Any, ctx: AnonymizationContext) -> dict[str, Any]:
        """Anonymize a control surface snapshot.

        Args:
            surface: ControlSurfaceSnapshot model or dict.
            ctx: Anonymization context for consistent mapping.

        Returns:
            Dict with anonymized control surface data.
        """
        # Convert to dict if it's a Pydantic model
        if hasattr(surface, "model_dump"):
            data = surface.model_dump()
        elif isinstance(surface, dict):
            data = surface
        else:
            return {}

        result: dict[str, Any] = {}

        # GENERALIZE: Services
        if "services" in data:
            anon_services = []
            for svc in data.get("services", []):
                anon_services.append(
                    {
                        "unit_name": ctx.anonymize_service_name(svc.get("unit_name", "")),
                        "enabled": svc.get("enabled", False),
                        "active_state": svc.get("active_state", "unknown"),
                        "restart_policy": svc.get("restart_policy", ""),
                        # REDACT: user, group
                        "cpu_quota": svc.get("cpu_quota", ""),
                        "memory_max": svc.get("memory_max", ""),
                        "dropin_count": svc.get("dropin_count", 0),
                        # PRESERVE: effective_hash (already anonymous)
                        "effective_hash": svc.get("effective_hash", ""),
                    }
                )
            result["services"] = anon_services

        # GENERALIZE: Configs
        if "configs" in data:
            anon_configs = []
            for cfg in data.get("configs", []):
                anon_configs.append(
                    {
                        "path": ctx.anonymize_path(cfg.get("path", "")),
                        # REDACT: owner
                        "mode": cfg.get("mode", ""),
                        # PRESERVE: sha256 (already anonymous hash)
                        "sha256": cfg.get("sha256", ""),
                        "fragment_count": cfg.get("fragment_count", 0),
                    }
                )
            result["configs"] = anon_configs

        # PRESERVE: packages (package names are public)
        if "packages" in data:
            result["packages"] = data["packages"]

        # PRESERVE: scheduler (hashes only)
        if "scheduler" in data:
            result["scheduler"] = data["scheduler"]

        # PRESERVE: privilege (hashes only)
        if "privilege" in data:
            priv = data["privilege"]
            result["privilege"] = {
                "sudoers_hash": priv.get("sudoers_hash", ""),
                "groups_hash": priv.get("groups_hash", ""),
                "polkit_rules_hash": priv.get("polkit_rules_hash", ""),
                "selinux_mode": priv.get("selinux_mode", "disabled"),
                "apparmor_mode": priv.get("apparmor_mode", "unknown"),
            }

        # PRESERVE: network_control (generic tool names)
        if "network_control" in data:
            result["network_control"] = data["network_control"]

        # PRESERVE: kernel_policy (version and settings)
        if "kernel_policy" in data:
            result["kernel_policy"] = data["kernel_policy"]

        # PRESERVE: hardware (generic specs)
        if "hardware" in data:
            result["hardware"] = data["hardware"]

        return result

    def _preserve_surface_hashes(self, hashes: dict[str, str] | None) -> dict[str, str] | None:
        """Preserve surface hashes as-is.

        Surface hash keys (paths, service names) are public knowledge patterns,
        not sensitive data. The hashes themselves are one-way and can't be
        reversed, making them safe for cloud aggregation while enabling
        cross-installation pattern matching.

        Args:
            hashes: Dict of surface name to hash value.

        Returns:
            Dict with original keys and hash values preserved.
        """
        if hashes is None:
            return None
        # Return a copy to avoid mutation
        return dict(hashes)

    def _compute_drift_explanations(
        self,
        surface_pre: Any,
        surface_post: Any,
        surface_drift: dict[str, bool],
        ctx: AnonymizationContext,
    ) -> list[DriftExplanation]:
        """Compute human-readable drift explanations from control surfaces.

        Args:
            surface_pre: Control surface snapshot before actions.
            surface_post: Control surface snapshot after actions.
            surface_drift: Dict of surface keys to whether they changed.
            ctx: Anonymization context for consistent naming.

        Returns:
            List of DriftExplanation for each drifted surface.
        """
        explanations: list[DriftExplanation] = []

        # Convert to dicts if Pydantic models
        pre_data = surface_pre.model_dump() if hasattr(surface_pre, "model_dump") else (surface_pre or {})
        post_data = surface_post.model_dump() if hasattr(surface_post, "model_dump") else (surface_post or {})

        for surface_key, changed in surface_drift.items():
            explanation = ""
            before_value = None
            after_value = None

            if surface_key.startswith("service:"):
                service_name = surface_key.split(":", 1)[1]
                explanation, before_value, after_value = self._explain_service_drift(
                    service_name, pre_data, post_data, ctx
                )
            elif surface_key.startswith("config:"):
                config_path = surface_key.split(":", 1)[1]
                explanation, before_value, after_value = self._explain_config_drift(
                    config_path, pre_data, post_data, ctx
                )
            elif surface_key == "packages":
                explanation, before_value, after_value = self._explain_package_drift(pre_data, post_data)
            elif surface_key == "scheduler":
                explanation = self._explain_simple_drift("Scheduler", changed)
            elif surface_key == "privilege":
                explanation = self._explain_simple_drift("Privilege configuration", changed)
            elif surface_key == "network_control":
                explanation = self._explain_simple_drift("Network control plane", changed)
            elif surface_key == "kernel_policy":
                explanation = self._explain_simple_drift("Kernel policy", changed)
            elif surface_key == "hardware":
                explanation = self._explain_simple_drift("Hardware identity", changed)
            else:
                explanation = f"{surface_key} {'changed' if changed else 'unchanged'}"

            explanations.append(
                DriftExplanation(
                    surface=surface_key,
                    changed=changed,
                    explanation=explanation,
                    before_value=before_value,
                    after_value=after_value,
                )
            )

        return explanations

    def _explain_service_drift(
        self,
        service_name: str,
        pre_data: dict[str, Any],
        post_data: dict[str, Any],
        ctx: AnonymizationContext,
    ) -> tuple[str, str | None, str | None]:
        """Explain drift in a service surface."""
        pre_services = {s.get("unit_name"): s for s in pre_data.get("services", [])}
        post_services = {s.get("unit_name"): s for s in post_data.get("services", [])}

        pre_svc = pre_services.get(service_name)
        post_svc = post_services.get(service_name)

        anon_name = ctx.anonymize_service_name(service_name)

        if not pre_svc and post_svc:
            return f"Service '{anon_name}' was added", None, "added"
        if pre_svc and not post_svc:
            return f"Service '{anon_name}' was removed", "present", None
        if not pre_svc or not post_svc:
            return f"Service '{anon_name}' changed", None, None

        changes: list[str] = []
        before_parts: list[str] = []
        after_parts: list[str] = []

        if pre_svc.get("enabled") != post_svc.get("enabled"):
            state = "enabled" if post_svc.get("enabled") else "disabled"
            changes.append(f"was {state}")
            before_parts.append(f"enabled={pre_svc.get('enabled')}")
            after_parts.append(f"enabled={post_svc.get('enabled')}")

        if pre_svc.get("restart_policy") != post_svc.get("restart_policy"):
            changes.append(f"restart policy → '{post_svc.get('restart_policy')}'")
            before_parts.append(f"restart={pre_svc.get('restart_policy')}")
            after_parts.append(f"restart={post_svc.get('restart_policy')}")

        if pre_svc.get("memory_max") != post_svc.get("memory_max"):
            changes.append(f"memory limit → '{post_svc.get('memory_max')}'")
            before_parts.append(f"mem={pre_svc.get('memory_max')}")
            after_parts.append(f"mem={post_svc.get('memory_max')}")

        if pre_svc.get("cpu_quota") != post_svc.get("cpu_quota"):
            changes.append(f"CPU quota → '{post_svc.get('cpu_quota')}'")

        if changes:
            explanation = f"Service '{anon_name}': " + ", ".join(changes)
        else:
            explanation = f"Service '{anon_name}' configuration changed"

        before_str = "; ".join(before_parts) if before_parts else None
        after_str = "; ".join(after_parts) if after_parts else None

        return explanation, before_str, after_str

    def _explain_config_drift(
        self,
        config_path: str,
        pre_data: dict[str, Any],
        post_data: dict[str, Any],
        ctx: AnonymizationContext,
    ) -> tuple[str, str | None, str | None]:
        """Explain drift in a config surface."""
        pre_configs = {c.get("path"): c for c in pre_data.get("configs", [])}
        post_configs = {c.get("path"): c for c in post_data.get("configs", [])}

        pre_cfg = pre_configs.get(config_path)
        post_cfg = post_configs.get(config_path)

        anon_path = ctx.anonymize_path(config_path)

        if not pre_cfg and post_cfg:
            return f"Config '{anon_path}' was created", None, "created"
        if pre_cfg and not post_cfg:
            return f"Config '{anon_path}' was deleted", "present", None
        if not pre_cfg or not post_cfg:
            return f"Config '{anon_path}' changed", None, None

        changes: list[str] = []

        if pre_cfg.get("sha256") != post_cfg.get("sha256"):
            changes.append("contents modified")

        if pre_cfg.get("mode") != post_cfg.get("mode"):
            changes.append(f"mode → {post_cfg.get('mode')}")

        if changes:
            explanation = f"Config '{anon_path}': " + ", ".join(changes)
        else:
            explanation = f"Config '{anon_path}' changed"

        return explanation, pre_cfg.get("sha256", "")[:8], post_cfg.get("sha256", "")[:8]

    def _explain_package_drift(
        self,
        pre_data: dict[str, Any],
        post_data: dict[str, Any],
    ) -> tuple[str, str | None, str | None]:
        """Explain drift in package surface."""
        pre_pkgs = pre_data.get("packages", {}).get("packages", [])
        post_pkgs = post_data.get("packages", {}).get("packages", [])

        pre_names = {p.get("name") for p in pre_pkgs}
        post_names = {p.get("name") for p in post_pkgs}

        added = post_names - pre_names
        removed = pre_names - post_names

        changes: list[str] = []
        if added:
            changes.append(f"{len(added)} added")
        if removed:
            changes.append(f"{len(removed)} removed")

        if changes:
            return f"Packages: {', '.join(changes)}", f"{len(pre_names)} pkgs", f"{len(post_names)} pkgs"
        return "Package state changed", None, None

    def _explain_simple_drift(self, name: str, changed: bool) -> str:
        """Simple drift explanation."""
        return f"{name} {'changed' if changed else 'unchanged'}"

    def _generalize_mount_point(self, mount: str) -> str:
        """Normalize a mount point to a category."""
        if not mount:
            return ""
        return MOUNT_CATEGORIES.get(mount, "other")

    def _compute_action_summary(self, actions: list[IncidentAction]) -> ActionSummary:
        """Compute aggregate statistics about actions.

        Args:
            actions: List of incident actions.

        Returns:
            ActionSummary with counts and timing.
        """
        if not actions:
            return ActionSummary()

        total = len(actions)
        successful = sum(1 for a in actions if a.success)
        failed = total - successful

        # Count by kind
        kind_counts: dict[str, int] = {}
        for action in actions:
            kind_counts[action.kind] = kind_counts.get(action.kind, 0) + 1

        # Timing
        durations = [a.duration_ms for a in actions if a.duration_ms is not None]
        total_duration = sum(durations)
        avg_duration = total_duration // len(durations) if durations else 0

        return ActionSummary(
            total_actions=total,
            successful_actions=successful,
            failed_actions=failed,
            shell_count=kind_counts.get("shell", 0),
            capability_count=kind_counts.get("capability", 0),
            edit_count=kind_counts.get("edit", 0),
            verify_count=kind_counts.get("verify", 0),
            rollback_count=kind_counts.get("rollback", 0),
            privileged_count=kind_counts.get("privileged", 0),
            gui_count=kind_counts.get("gui", 0),
            total_duration_ms=total_duration,
            avg_duration_ms=avg_duration,
        )


# =============================================================================
# Convenience Functions
# =============================================================================


def anonymize_incident(
    incident: IncidentReport,
    actions: list[IncidentAction] | None = None,
    telemetry_pre: Any | None = None,
    telemetry_post: Any | None = None,
    surface_hashes_pre: dict[str, str] | None = None,
    surface_hashes_post: dict[str, str] | None = None,
    control_surface_pre: Any | None = None,
    control_surface_post: Any | None = None,
    detail_level: str = "hashes",
) -> AnonymizedIncidentReport:
    """Convenience function to anonymize an incident.

    Args:
        incident: The original incident report.
        actions: Optional list of incident actions.
        telemetry_pre: Optional pre-state telemetry snapshot.
        telemetry_post: Optional post-state telemetry snapshot.
        surface_hashes_pre: Optional pre-state control surface hashes.
        surface_hashes_post: Optional post-state control surface hashes.
        control_surface_pre: Optional pre-state control surface snapshot.
        control_surface_post: Optional post-state control surface snapshot.
        detail_level: "hashes" (default, most private) or "detailed" (richer sharing).

    Returns:
        AnonymizedIncidentReport suitable for cloud storage.
    """
    anonymizer = IncidentAnonymizer()
    return anonymizer.anonymize(
        incident=incident,
        actions=actions,
        telemetry_pre=telemetry_pre,
        telemetry_post=telemetry_post,
        surface_hashes_pre=surface_hashes_pre,
        surface_hashes_post=surface_hashes_post,
        control_surface_pre=control_surface_pre,
        control_surface_post=control_surface_post,
        detail_level=detail_level,
    )
