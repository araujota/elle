"""Domain handlers for configuration generation.

Provides specialized handlers for different configuration domains,
each with domain-specific validation and schema hints.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from elle.rag.confgen.models import (
    ConfigDomain,
    ConfigFileType,
    ConfigGenValidation,
    ConfigOp,
    ValidationIssue,
)


# =============================================================================
# Abstract Base Handler
# =============================================================================


class DomainHandler(ABC):
    """Abstract base class for domain-specific configuration handlers.

    Each domain handler provides:
    - File pattern matching for auto-detection
    - Schema hints for structured files
    - Domain-specific validation
    - Default templates for new files
    """

    @property
    @abstractmethod
    def domain(self) -> ConfigDomain:
        """Return the domain identifier."""
        ...

    @property
    @abstractmethod
    def file_patterns(self) -> tuple[str, ...]:
        """Return glob patterns for files in this domain."""
        ...

    @property
    def file_type(self) -> ConfigFileType:
        """Return the default file type for this domain."""
        return "text"

    def matches_path(self, path: Path | str) -> bool:
        """Check if a path matches this domain's file patterns."""
        path = Path(path)
        for pattern in self.file_patterns:
            if path.match(pattern):
                return True
        return False

    def get_schema_hint(self, file_path: Path | str) -> dict[str, Any] | None:
        """Get a schema hint for the file type.

        Override in subclasses to provide structured file schemas.
        """
        return None

    def validate_operations(
        self,
        ops: tuple[ConfigOp, ...],
        current_content: str | None = None,
    ) -> ConfigGenValidation:
        """Validate operations for this domain.

        Override in subclasses for domain-specific validation.
        """
        issues: list[ValidationIssue] = []

        for op in ops:
            # Check for empty paths
            if not op.path:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message="Operation has empty path",
                        path=op.path,
                    )
                )

            # Check for dangerous patterns in values
            if op.value:
                if self._has_shell_injection(op.value):
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            message="Potential shell injection in value",
                            path=op.path,
                            suggestion="Escape or quote special characters",
                        )
                    )

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in issues),
            issues=tuple(issues),
        )

    def validate_content(self, content: str) -> ConfigGenValidation:
        """Validate generated content for this domain.

        Override in subclasses for domain-specific validation.
        """
        return ConfigGenValidation(valid=True)

    def get_template(self) -> str | None:
        """Get a template for new files in this domain.

        Override in subclasses to provide default templates.
        """
        return None

    def get_validation_command(self, file_path: Path | str) -> str | None:
        """Get a command to validate the configuration.

        Override in subclasses to provide validation commands.
        """
        return None

    def _has_shell_injection(self, value: str) -> bool:
        """Check for potential shell injection patterns."""
        dangerous_patterns = [
            r"\$\(.*\)",  # Command substitution $(...)
            r"`.*`",  # Backtick command substitution
            r";\s*\w+",  # Command chaining
            r"\|\s*\w+",  # Pipe to command
            r"&&\s*\w+",  # And command
            r"\|\|\s*\w+",  # Or command
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, value):
                return True
        return False


# =============================================================================
# Network Domain
# =============================================================================


class NetplanHandler(DomainHandler):
    """Handler for netplan network configuration."""

    @property
    def domain(self) -> ConfigDomain:
        return "network"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return (
            "/etc/netplan/*.yaml",
            "/etc/netplan/*.yml",
        )

    @property
    def file_type(self) -> ConfigFileType:
        return "yaml"

    def get_schema_hint(self, file_path: Path | str) -> dict[str, Any] | None:
        return {
            "network": {
                "version": 2,
                "renderer": "networkd|NetworkManager",
                "ethernets": {
                    "<interface>": {
                        "dhcp4": "true|false",
                        "addresses": ["<ip>/<prefix>"],
                        "routes": [{"to": "<destination>", "via": "<gateway>"}],
                        "nameservers": {"addresses": ["<dns>"]},
                    }
                },
            }
        }

    def validate_content(self, content: str) -> ConfigGenValidation:
        """Validate netplan YAML content."""
        issues: list[ValidationIssue] = []

        try:
            import yaml
        except ImportError:
            # YAML not available, skip validation
            return ConfigGenValidation(valid=True)

        try:
            data = yaml.safe_load(content)

            if not isinstance(data, dict):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message="Content is not a valid YAML mapping",
                    )
                )
            elif "network" not in data:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message="Missing required 'network' key",
                        suggestion="Add 'network:' as the root key",
                    )
                )
            else:
                network = data["network"]
                if network.get("version") not in (2, None):
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            message=f"Unexpected network version: {network.get('version')}",
                            suggestion="Use version 2 for modern netplan",
                        )
                    )

        except yaml.YAMLError as e:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"Invalid YAML syntax: {e}",
                )
            )

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in issues),
            issues=tuple(issues),
            syntax_valid=not any(
                i.severity == "error" and "syntax" in i.message.lower()
                for i in issues
            ),
        )

    def get_template(self) -> str | None:
        return """network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:
      dhcp4: true
"""

    def get_validation_command(self, file_path: Path | str) -> str | None:
        return "netplan try"


class HostsHandler(DomainHandler):
    """Handler for /etc/hosts configuration."""

    @property
    def domain(self) -> ConfigDomain:
        return "hosts"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return ("/etc/hosts",)

    @property
    def file_type(self) -> ConfigFileType:
        return "augeas"

    def validate_operations(
        self,
        ops: tuple[ConfigOp, ...],
        current_content: str | None = None,
    ) -> ConfigGenValidation:
        """Validate hosts file operations."""
        issues: list[ValidationIssue] = []

        ip_pattern = re.compile(
            r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
            r"|^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$"
            r"|^::1$|^127\.0\.0\.1$"
        )

        for op in ops:
            if op.kind == "set" and op.value:
                # Check if setting an IP address
                if "ipaddr" in op.path.lower() or "canonical" not in op.path.lower():
                    if not ip_pattern.match(op.value) and op.value not in ("localhost",):
                        # Might be a hostname, which is fine
                        pass

        base_validation = super().validate_operations(ops, current_content)
        all_issues = list(base_validation.issues) + issues

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in all_issues),
            issues=tuple(all_issues),
        )


# =============================================================================
# Filesystem Domain
# =============================================================================


class FstabHandler(DomainHandler):
    """Handler for /etc/fstab configuration."""

    @property
    def domain(self) -> ConfigDomain:
        return "filesystem"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return ("/etc/fstab",)

    @property
    def file_type(self) -> ConfigFileType:
        return "augeas"

    def validate_operations(
        self,
        ops: tuple[ConfigOp, ...],
        current_content: str | None = None,
    ) -> ConfigGenValidation:
        """Validate fstab operations."""
        issues: list[ValidationIssue] = []

        critical_mounts = ("/", "/boot", "/home", "/var", "/usr")

        for op in ops:
            # Warn about modifying critical mounts
            if op.kind == "rm":
                for mount in critical_mounts:
                    if mount in op.path:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=f"Removing critical mount point: {mount}",
                                path=op.path,
                                suggestion="Ensure this is intentional",
                            )
                        )

            # Validate mount options
            if op.kind == "set" and "opt" in op.path.lower() and op.value:
                valid_options = {
                    "defaults", "rw", "ro", "noatime", "nodiratime",
                    "relatime", "noexec", "nosuid", "nodev", "user",
                    "users", "auto", "noauto", "sync", "async",
                    "dirsync", "nofail", "x-systemd.automount",
                }
                for opt in op.value.split(","):
                    opt = opt.strip().split("=")[0]
                    if opt and opt not in valid_options and not opt.startswith("x-"):
                        issues.append(
                            ValidationIssue(
                                severity="info",
                                message=f"Uncommon mount option: {opt}",
                                path=op.path,
                            )
                        )

        base_validation = super().validate_operations(ops, current_content)
        all_issues = list(base_validation.issues) + issues

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in all_issues),
            issues=tuple(all_issues),
        )

    def get_validation_command(self, file_path: Path | str) -> str | None:
        return "mount -a --fake"


# =============================================================================
# SSH Domain
# =============================================================================


class SshdHandler(DomainHandler):
    """Handler for SSH daemon configuration."""

    @property
    def domain(self) -> ConfigDomain:
        return "ssh"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return (
            "/etc/ssh/sshd_config",
            "/etc/ssh/sshd_config.d/*.conf",
        )

    @property
    def file_type(self) -> ConfigFileType:
        return "augeas"

    def validate_operations(
        self,
        ops: tuple[ConfigOp, ...],
        current_content: str | None = None,
    ) -> ConfigGenValidation:
        """Validate sshd_config operations."""
        issues: list[ValidationIssue] = []

        security_settings = {
            "PermitRootLogin": ("no", "prohibit-password"),
            "PasswordAuthentication": ("no",),
            "PermitEmptyPasswords": ("no",),
            "X11Forwarding": ("no",),
        }

        for op in ops:
            if op.kind == "set" and op.value:
                setting = op.path.split("/")[-1] if "/" in op.path else op.path

                # Warn about security-sensitive settings
                if setting in security_settings:
                    recommended = security_settings[setting]
                    if op.value.lower() not in [r.lower() for r in recommended]:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=f"Security setting {setting}={op.value} may reduce security",
                                path=op.path,
                                suggestion=f"Recommended: {' or '.join(recommended)}",
                            )
                        )

                # Check for valid Port values
                if setting == "Port":
                    try:
                        port = int(op.value)
                        if port < 1 or port > 65535:
                            issues.append(
                                ValidationIssue(
                                    severity="error",
                                    message=f"Invalid port number: {port}",
                                    path=op.path,
                                )
                            )
                        elif port < 1024:
                            issues.append(
                                ValidationIssue(
                                    severity="info",
                                    message=f"Privileged port {port} requires root",
                                    path=op.path,
                                )
                            )
                    except ValueError:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message=f"Port must be a number: {op.value}",
                                path=op.path,
                            )
                        )

        base_validation = super().validate_operations(ops, current_content)
        all_issues = list(base_validation.issues) + issues

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in all_issues),
            issues=tuple(all_issues),
        )

    def get_validation_command(self, file_path: Path | str) -> str | None:
        return "sshd -t"


# =============================================================================
# Firewall Domain
# =============================================================================


class UfwHandler(DomainHandler):
    """Handler for UFW firewall configuration."""

    @property
    def domain(self) -> ConfigDomain:
        return "firewall"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return (
            "/etc/ufw/ufw.conf",
            "/etc/ufw/user.rules",
            "/etc/ufw/user6.rules",
        )

    @property
    def file_type(self) -> ConfigFileType:
        return "text"

    def get_validation_command(self, file_path: Path | str) -> str | None:
        return "ufw status verbose"


# =============================================================================
# Service Domain
# =============================================================================


class SystemdHandler(DomainHandler):
    """Handler for systemd unit files."""

    @property
    def domain(self) -> ConfigDomain:
        return "service"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return (
            "/etc/systemd/system/*.service",
            "/etc/systemd/system/*.timer",
            "/etc/systemd/system/*.socket",
            "/etc/systemd/system/*.mount",
            "/lib/systemd/system/*.service",
            "~/.config/systemd/user/*.service",
        )

    @property
    def file_type(self) -> ConfigFileType:
        return "systemd"

    def get_schema_hint(self, file_path: Path | str) -> dict[str, Any] | None:
        return {
            "[Unit]": {
                "Description": "<service description>",
                "After": "<dependency>",
                "Requires": "<hard dependency>",
                "Wants": "<soft dependency>",
            },
            "[Service]": {
                "Type": "simple|forking|oneshot|notify|dbus",
                "ExecStart": "<command>",
                "ExecStop": "<stop command>",
                "Restart": "no|always|on-failure|on-success",
                "User": "<user>",
                "Group": "<group>",
                "WorkingDirectory": "<path>",
            },
            "[Install]": {
                "WantedBy": "multi-user.target",
            },
        }

    def get_template(self) -> str | None:
        return """[Unit]
Description=My Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/myservice
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

    def get_validation_command(self, file_path: Path | str) -> str | None:
        path = Path(file_path)
        return f"systemd-analyze verify {path.name}"


# =============================================================================
# Cron Domain
# =============================================================================


class CronHandler(DomainHandler):
    """Handler for cron configuration."""

    @property
    def domain(self) -> ConfigDomain:
        return "cron"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return (
            "/etc/crontab",
            "/etc/cron.d/*",
            "/var/spool/cron/crontabs/*",
        )

    @property
    def file_type(self) -> ConfigFileType:
        return "text"

    def validate_content(self, content: str) -> ConfigGenValidation:
        """Validate cron syntax."""
        issues: list[ValidationIssue] = []

        cron_pattern = re.compile(
            r"^[\*\d,\-\/]+\s+"  # minute
            r"[\*\d,\-\/]+\s+"  # hour
            r"[\*\d,\-\/]+\s+"  # day of month
            r"[\*\d,\-\/]+\s+"  # month
            r"[\*\d,\-\/]+\s+"  # day of week
            r".*$"  # command
        )

        for i, line in enumerate(content.split("\n"), 1):
            line = line.strip()
            if not line or line.startswith("#") or "=" in line:
                continue

            if not cron_pattern.match(line):
                # Check if it's a crontab with user field
                parts = line.split()
                if len(parts) >= 7:
                    # Might be /etc/crontab format with user field
                    continue
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=f"Line {i} may not be valid cron syntax",
                        suggestion="Use format: * * * * * command",
                    )
                )

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in issues),
            issues=tuple(issues),
        )


# =============================================================================
# General Domain
# =============================================================================


class GeneralHandler(DomainHandler):
    """Fallback handler for unknown configuration domains."""

    @property
    def domain(self) -> ConfigDomain:
        return "general"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return ("*",)

    @property
    def file_type(self) -> ConfigFileType:
        return "text"


# =============================================================================
# Handler Registry
# =============================================================================

def _build_handlers() -> dict[ConfigDomain, DomainHandler]:
    """Build the handler registry.

    Lazy loading to avoid circular imports.
    """
    handlers: dict[ConfigDomain, DomainHandler] = {
        "network": NetplanHandler(),
        "hosts": HostsHandler(),
        "filesystem": FstabHandler(),
        "ssh": SshdHandler(),
        "firewall": UfwHandler(),
        "service": SystemdHandler(),
        "cron": CronHandler(),
        "general": GeneralHandler(),
    }

    # Add Docker handler
    try:
        from elle.rag.confgen.docker import DockerHandler
        handlers["docker"] = DockerHandler()
    except ImportError:
        pass

    # Add WireGuard handler
    try:
        from elle.rag.confgen.wireguard import WireguardHandler
        handlers["wireguard"] = WireguardHandler()
    except ImportError:
        pass

    return handlers


_handlers: dict[ConfigDomain, DomainHandler] | None = None


def _get_handlers() -> dict[ConfigDomain, DomainHandler]:
    """Get the handler registry (lazy initialization)."""
    global _handlers
    if _handlers is None:
        _handlers = _build_handlers()
    return _handlers


def get_handler(domain: ConfigDomain) -> DomainHandler:
    """Get the handler for a domain.

    Args:
        domain: Configuration domain.

    Returns:
        The domain handler.
    """
    handlers = _get_handlers()
    return handlers.get(domain, handlers["general"])


def detect_domain(path: Path | str) -> ConfigDomain:
    """Detect the domain from a file path.

    Args:
        path: Path to the configuration file.

    Returns:
        The detected domain.
    """
    path = Path(path)
    handlers = _get_handlers()

    for domain, handler in handlers.items():
        if domain != "general" and handler.matches_path(path):
            return domain

    return "general"


def detect_file_type(path: Path | str) -> ConfigFileType:
    """Detect the file type from a file path.

    Args:
        path: Path to the configuration file.

    Returns:
        The detected file type.
    """
    path = Path(path)

    # Check extension first
    suffix = path.suffix.lower()
    extension_map: dict[str, ConfigFileType] = {
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".toml": "toml",
        ".md": "markdown",
        ".markdown": "markdown",
        ".service": "systemd",
        ".timer": "systemd",
        ".socket": "systemd",
        ".mount": "systemd",
    }

    if suffix in extension_map:
        return extension_map[suffix]

    # Check domain-specific patterns
    domain = detect_domain(path)
    handler = get_handler(domain)
    return handler.file_type
