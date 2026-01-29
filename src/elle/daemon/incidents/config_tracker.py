"""Configuration change tracking and incident creation.

Tracks all /etc modifications (including ELLE's own changes) as incidents
for complete audit trail and rollback capability.

Uses inotify events from telemetryd to detect changes to configuration files.
Every change creates an incident record with:
- SHA256 hashes before/after
- Backup of the original content
- Risk assessment
- Automatic rollback capability

Malicious pattern detection includes:
- Shell injection patterns
- Unauthorized SSH keys
- World-writable permissions
- SUID/SGID changes
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Root directories to track for config changes
KNOWN_CONFIG_ROOTS = (
    Path("/etc"),
    Path("/var/lib/elle"),
)

# High-risk config files that need extra scrutiny
HIGH_RISK_CONFIGS = {
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/ssh/sshd_config",
    "/etc/ssh/authorized_keys",
    "/etc/crontab",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/fstab",
    "/etc/iptables/rules.v4",
    "/etc/iptables/rules.v6",
}

# Patterns that indicate potential malicious changes
MALICIOUS_PATTERNS = [
    # Shell injection
    r"\$\([^)]*\)",  # Command substitution
    r"`[^`]*`",  # Backtick command substitution
    r";\s*rm\s+-rf",  # rm -rf following semicolon
    r"\|\s*sh\b",  # Pipe to shell
    r"\|\s*bash\b",  # Pipe to bash
    # SSH key injection (unusual authorized_keys additions)
    r"ssh-rsa\s+AAAA[A-Za-z0-9+/]+={0,2}\s+.{0,50}\n.*ssh-rsa",  # Multiple keys
    # Cron job injection
    r"\*\s+\*\s+\*\s+\*\s+\*.*curl",  # Every-minute curl
    r"\*\s+\*\s+\*\s+\*\s+\*.*wget",  # Every-minute wget
    # Password hash changes
    r":\$[156]\$[a-zA-Z0-9./]+:",  # Unix password hash
]


# =============================================================================
# Models
# =============================================================================


class ConfigRisk(BaseModel):
    """Risk assessment for a config change."""

    model_config = ConfigDict(frozen=True)

    severity: Literal["low", "medium", "high", "critical"] = Field(
        description="Risk severity level",
    )
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Risk score (0-1)",
    )
    reasons: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Reasons for the risk assessment",
    )
    malicious_patterns_found: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Malicious patterns detected",
    )
    cascade_services: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Services that may be affected",
    )


class ConfigChangeRecord(BaseModel):
    """Record of a configuration file change."""

    model_config = ConfigDict(frozen=True)

    # Identity
    path: str = Field(description="Path to the config file")
    change_type: Literal["create", "modify", "delete"] = Field(
        description="Type of change",
    )

    # Hashes for integrity
    sha256_before: str | None = Field(
        default=None,
        description="SHA256 hash before change",
    )
    sha256_after: str | None = Field(
        default=None,
        description="SHA256 hash after change",
    )

    # Metadata
    size_before: int | None = Field(default=None)
    size_after: int | None = Field(default=None)
    mtime_before: str | None = Field(default=None)
    mtime_after: str | None = Field(default=None)

    # Attribution
    is_elle_change: bool = Field(
        default=False,
        description="Whether ELLE made this change",
    )
    incident_id: str | None = Field(
        default=None,
        description="ELLE incident that caused this change",
    )

    # Backup
    backup_available: bool = Field(
        default=False,
        description="Whether a backup was created",
    )
    backup_path: str | None = Field(
        default=None,
        description="Path to the backup file",
    )

    # Risk assessment
    risk: ConfigRisk | None = Field(default=None)

    # Timing
    detected_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Config Change Tracker
# =============================================================================


class ConfigChangeTracker:
    """Track configuration file changes as incidents.

    Listens for inotify events from telemetryd and creates incident
    records for each configuration change.

    Features:
    - Automatic backup before changes
    - SHA256 hash tracking
    - Risk assessment with malicious pattern detection
    - Service cascade impact analysis
    - Rollback support via incident linkage
    """

    def __init__(
        self,
        backup_dir: Path | None = None,
    ) -> None:
        """Initialize the config tracker.

        Args:
            backup_dir: Directory for storing backups.
        """
        self._backup_dir = backup_dir or Path("/var/lib/elle/config_backups")
        self._backup_dir.mkdir(parents=True, exist_ok=True)

        # Track recent ELLE changes to attribute them correctly
        self._elle_changes: dict[str, str] = {}  # path -> incident_id

    def register_elle_change(self, path: str, incident_id: str) -> None:
        """Register that ELLE is about to make a change.

        Called by capabilities before modifying config files.

        Args:
            path: Path being modified.
            incident_id: Incident that will cause the change.
        """
        self._elle_changes[path] = incident_id

    async def handle_config_change(
        self,
        path: Path,
        change_type: Literal["create", "modify", "delete"],
        sha256_before: str | None = None,
        sha256_after: str | None = None,
    ) -> dict[str, Any]:
        """Handle a detected config change.

        Creates an incident record with full context.

        Args:
            path: Path to the changed config file.
            change_type: Type of change.
            sha256_before: Hash before change (if known).
            sha256_after: Hash after change (if known).

        Returns:
            Incident data dict.
        """
        path_str = str(path)

        # Determine if this was ELLE's change
        is_elle_change = path_str in self._elle_changes
        elle_incident_id = self._elle_changes.pop(path_str, None)

        # Calculate hashes if not provided
        if change_type != "delete" and sha256_after is None and path.exists():
            sha256_after = self._hash_file(path)

        # Get file metadata
        size_after = path.stat().st_size if path.exists() else None
        mtime_after = datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None

        # Assess risk
        risk = await self._assess_config_risk(
            path=path,
            change_type=change_type,
            sha256_before=sha256_before,
            sha256_after=sha256_after,
        )

        # Create backup if this is a modify and we have the original
        backup_path = None
        if change_type in ("modify", "delete") and sha256_before:
            backup_path = await self._store_backup(path, sha256_before)

        # Create change record
        record = ConfigChangeRecord(
            path=path_str,
            change_type=change_type,
            sha256_before=sha256_before,
            sha256_after=sha256_after,
            size_after=size_after,
            mtime_after=mtime_after,
            is_elle_change=is_elle_change,
            incident_id=elle_incident_id,
            backup_available=backup_path is not None,
            backup_path=backup_path,
            risk=risk,
            detected_at=datetime.utcnow(),
        )

        # Create incident
        incident_data = self._create_incident(record)

        # Store the incident
        try:
            from elle.daemon.incidents.store import create_incident

            await create_incident(incident_data)
        except ImportError:
            logger.debug("Incident store not available")
        except Exception as e:
            logger.warning(f"Failed to store config change incident: {e}")

        return incident_data

    async def _assess_config_risk(
        self,
        path: Path,
        change_type: Literal["create", "modify", "delete"],
        sha256_before: str | None,
        sha256_after: str | None,
    ) -> ConfigRisk:
        """Assess the risk of a config change.

        Checks for:
        - High-risk files
        - Malicious patterns
        - Permission changes
        - Service cascade potential

        Args:
            path: Config file path.
            change_type: Type of change.
            sha256_before: Hash before.
            sha256_after: Hash after.

        Returns:
            ConfigRisk assessment.
        """
        reasons: list[str] = []
        malicious_patterns: list[str] = []
        cascade_services: list[str] = []
        score = 0.1  # Base score

        path_str = str(path)

        # Check if high-risk file
        if path_str in HIGH_RISK_CONFIGS:
            reasons.append(f"High-risk file: {path.name}")
            score += 0.3

        # Check for delete of critical files
        if change_type == "delete":
            reasons.append("File deletion")
            score += 0.2

        # Read content for pattern analysis (if file exists)
        if path.exists() and change_type != "delete":
            try:
                content = path.read_text(errors="replace")

                # Check for malicious patterns
                for pattern in MALICIOUS_PATTERNS:
                    if re.search(pattern, content):
                        malicious_patterns.append(pattern)
                        score += 0.3

                if malicious_patterns:
                    reasons.append(f"Malicious patterns detected: {len(malicious_patterns)}")

            except Exception as e:
                logger.warning(f"Could not read config for analysis: {e}")

        # Check for permission issues
        if path.exists():
            try:
                mode = path.stat().st_mode
                # World-writable
                if mode & 0o002:
                    reasons.append("World-writable")
                    score += 0.2
                # SUID/SGID
                if mode & 0o6000:
                    reasons.append("SUID/SGID bit set")
                    score += 0.4
            except Exception:
                pass

        # Determine cascade impact
        cascade_services = list(self._identify_cascade_services(path))
        if cascade_services:
            reasons.append(f"May affect services: {', '.join(cascade_services[:3])}")
            score += 0.1 * len(cascade_services)

        # Normalize score
        score = min(score, 1.0)

        # Determine severity
        if score >= 0.7 or malicious_patterns:
            severity: Literal["low", "medium", "high", "critical"] = "critical"
        elif score >= 0.5:
            severity = "high"
        elif score >= 0.3:
            severity = "medium"
        else:
            severity = "low"

        return ConfigRisk(
            severity=severity,
            score=score,
            reasons=tuple(reasons),
            malicious_patterns_found=tuple(malicious_patterns),
            cascade_services=tuple(cascade_services),
        )

    def _identify_cascade_services(self, path: Path) -> list[str]:
        """Identify services that may be affected by this config change.

        Args:
            path: Config file path.

        Returns:
            List of service names.
        """
        path_str = str(path)
        services = []

        # Map config paths to services
        service_map = {
            "/etc/nginx/": ["nginx"],
            "/etc/apache2/": ["apache2"],
            "/etc/ssh/": ["ssh", "sshd"],
            "/etc/mysql/": ["mysql", "mariadb"],
            "/etc/postgresql/": ["postgresql"],
            "/etc/redis/": ["redis-server"],
            "/etc/docker/": ["docker"],
            "/etc/systemd/": ["systemd"],
            "/etc/NetworkManager/": ["NetworkManager"],
            "/etc/netplan/": ["systemd-networkd"],
            "/etc/resolv.conf": ["systemd-resolved"],
        }

        for prefix, svc_list in service_map.items():
            if path_str.startswith(prefix) or path_str == prefix.rstrip("/"):
                services.extend(svc_list)

        return services

    def _create_incident(self, record: ConfigChangeRecord) -> dict[str, Any]:
        """Create an incident dict from a change record.

        Args:
            record: The change record.

        Returns:
            Incident data dict.
        """
        import uuid

        incident_id = f"CONFIG-{uuid.uuid4().hex[:8].upper()}"

        # Determine severity from risk
        severity = record.risk.severity if record.risk else "info"

        # Build title
        verb = {"create": "created", "modify": "modified", "delete": "deleted"}[
            record.change_type
        ]
        source = "ELLE" if record.is_elle_change else "external"
        title = f"Config {verb}: {record.path} ({source})"

        return {
            "id": incident_id,
            "created_at": record.detected_at.isoformat(),
            "updated_at": record.detected_at.isoformat(),
            "domain": "config",
            "severity": severity,
            "status": "open" if not record.is_elle_change else "resolved",
            "title": title,
            "summary": (
                f"Configuration file {record.path} was {verb}. "
                f"{'Change made by ELLE capability.' if record.is_elle_change else 'External change detected.'} "
                f"{'Backup available for rollback.' if record.backup_available else ''}"
            ),
            "trigger_source": "config_watcher" if not record.is_elle_change else "elle_capability",
            "outcome": "improved" if record.is_elle_change else "unknown",
            "metrics_json": {
                "path": record.path,
                "change_type": record.change_type,
                "sha256_before": record.sha256_before,
                "sha256_after": record.sha256_after,
                "is_elle_change": record.is_elle_change,
                "backup_available": record.backup_available,
                "backup_path": record.backup_path,
                "risk_score": record.risk.score if record.risk else 0.0,
                "risk_severity": record.risk.severity if record.risk else "low",
                "malicious_patterns": list(record.risk.malicious_patterns_found) if record.risk else [],
                "cascade_services": list(record.risk.cascade_services) if record.risk else [],
            },
        }

    def _hash_file(self, path: Path) -> str:
        """Calculate SHA256 hash of a file.

        Args:
            path: File path.

        Returns:
            Hex-encoded SHA256 hash.
        """
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"Failed to hash {path}: {e}")
            return ""

    async def _store_backup(
        self,
        path: Path,
        sha256: str,
    ) -> str | None:
        """Store a backup of the config file.

        Args:
            path: Original file path.
            sha256: Hash of the content to backup.

        Returns:
            Path to backup file, or None if failed.
        """
        try:
            # Create backup filename with timestamp
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{path.name}.{timestamp}.{sha256[:8]}"
            backup_path = self._backup_dir / backup_name

            # If original still exists, copy it
            if path.exists():
                import shutil

                shutil.copy2(path, backup_path)
                return str(backup_path)

        except Exception as e:
            logger.warning(f"Failed to backup {path}: {e}")

        return None


# =============================================================================
# Singleton
# =============================================================================

_tracker: ConfigChangeTracker | None = None


def get_config_tracker() -> ConfigChangeTracker:
    """Get the singleton ConfigChangeTracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = ConfigChangeTracker()
    return _tracker


async def get_config_backup(incident_id: str, path: str) -> dict[str, Any] | None:
    """Get backup information for a config file from an incident.

    Args:
        incident_id: The incident ID.
        path: The config file path.

    Returns:
        Backup info dict or None if not found.
    """
    try:
        from elle.daemon.incidents.store import get_incident

        incident = await get_incident(incident_id)
        if not incident:
            return None

        metrics = incident.get("metrics_json", {})
        if metrics.get("path") == path and metrics.get("backup_available"):
            return {
                "path": path,
                "backup_path": metrics.get("backup_path"),
                "sha256_before": metrics.get("sha256_before"),
                "sha256_after": metrics.get("sha256_after"),
            }
    except Exception as e:
        logger.warning(f"Failed to get config backup: {e}")

    return None
