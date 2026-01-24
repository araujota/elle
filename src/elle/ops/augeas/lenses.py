"""Augeas lens registry and detection.

Maps file paths to appropriate Augeas lenses for structured parsing.
Provides auto-detection based on file paths and patterns.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# =============================================================================
# Lens Registry
# =============================================================================

# Mapping of file patterns to Augeas lens names
# Patterns support glob-style matching (* for any chars, ** for directories)
LENS_REGISTRY: dict[str, str] = {
    # Filesystem
    "/etc/fstab": "Fstab.lns",
    "/etc/mtab": "Fstab.lns",
    # Network
    "/etc/hosts": "Hosts.lns",
    "/etc/resolv.conf": "Resolv.lns",
    "/etc/nsswitch.conf": "Nsswitch.lns",
    "/etc/networks": "Networks.lns",
    "/etc/protocols": "Protocols.lns",
    "/etc/services": "Services.lns",
    "/etc/ethers": "Ethers.lns",
    # SSH
    "/etc/ssh/sshd_config": "Sshd.lns",
    "/etc/ssh/ssh_config": "Ssh.lns",
    # System
    "/etc/sysctl.conf": "Sysctl.lns",
    "/etc/sysctl.d/*.conf": "Sysctl.lns",
    "/etc/modules": "Modules.lns",
    "/etc/modprobe.d/*.conf": "Modprobe.lns",
    "/etc/environment": "Shellvars.lns",
    "/etc/locale.conf": "Shellvars.lns",
    "/etc/timezone": "Timezone.lns",
    "/etc/hostname": "Hostname.lns",
    # Users/Groups
    "/etc/passwd": "Passwd.lns",
    "/etc/group": "Group.lns",
    "/etc/shadow": "Shadow.lns",
    "/etc/gshadow": "Gshadow.lns",
    "/etc/login.defs": "Login_defs.lns",
    # Sudo
    "/etc/sudoers": "Sudoers.lns",
    "/etc/sudoers.d/*": "Sudoers.lns",
    # NFS
    "/etc/exports": "Exports.lns",
    # Cron
    "/etc/crontab": "Cron.lns",
    "/etc/cron.d/*": "Cron.lns",
    "/var/spool/cron/crontabs/*": "Cron.lns",
    # Package Management
    "/etc/apt/sources.list": "Aptsources.lns",
    "/etc/apt/sources.list.d/*.list": "Aptsources.lns",
    "/etc/yum.conf": "Yum.lns",
    "/etc/yum.repos.d/*.repo": "Yum.lns",
    "/etc/dnf/dnf.conf": "Yum.lns",
    # PAM
    "/etc/pam.d/*": "Pam.lns",
    "/etc/pam.conf": "Pam.lns",
    # Security
    "/etc/security/limits.conf": "Limits.lns",
    "/etc/security/limits.d/*.conf": "Limits.lns",
    "/etc/security/access.conf": "Access.lns",
    "/etc/security/pwquality.conf": "Pwquality.lns",
    # Shells
    "/etc/shells": "Shells.lns",
    "/etc/profile": "Shellvars.lns",
    "/etc/profile.d/*.sh": "Shellvars.lns",
    "/etc/bash.bashrc": "Shellvars_list.lns",
    "/etc/default/*": "Shellvars.lns",
    # Init/Services
    "/etc/inittab": "Inittab.lns",
    "/etc/rc.local": "Shellvars_list.lns",
    # Logging
    "/etc/rsyslog.conf": "Rsyslog.lns",
    "/etc/rsyslog.d/*.conf": "Rsyslog.lns",
    "/etc/logrotate.conf": "Logrotate.lns",
    "/etc/logrotate.d/*": "Logrotate.lns",
    # Misc
    "/etc/aliases": "Aliases.lns",
    "/etc/ldap/ldap.conf": "Ldap.lns",
    "/etc/ntp.conf": "Ntp.lns",
    "/etc/chrony.conf": "Chrony.lns",
    "/etc/postfix/main.cf": "Postfix_main.lns",
    "/etc/postfix/master.cf": "Postfix_master.lns",
    "/etc/nginx/nginx.conf": "Nginx.lns",
    "/etc/nginx/sites-available/*": "Nginx.lns",
    "/etc/nginx/sites-enabled/*": "Nginx.lns",
    "/etc/nginx/conf.d/*.conf": "Nginx.lns",
    "/etc/apache2/apache2.conf": "Httpd.lns",
    "/etc/apache2/sites-available/*": "Httpd.lns",
    "/etc/apache2/sites-enabled/*": "Httpd.lns",
    "/etc/apache2/conf-available/*": "Httpd.lns",
    "/etc/apache2/conf-enabled/*": "Httpd.lns",
    "/etc/httpd/conf/httpd.conf": "Httpd.lns",
    "/etc/httpd/conf.d/*.conf": "Httpd.lns",
    "/etc/mysql/my.cnf": "MySQL.lns",
    "/etc/mysql/mysql.conf.d/*.cnf": "MySQL.lns",
    "/etc/mysql/conf.d/*.cnf": "MySQL.lns",
    "/etc/my.cnf": "MySQL.lns",
    "/etc/my.cnf.d/*.cnf": "MySQL.lns",
    "/etc/php/*/cli/php.ini": "PHP.lns",
    "/etc/php/*/fpm/php.ini": "PHP.lns",
    "/etc/php/*/apache2/php.ini": "PHP.lns",
    "/etc/php.ini": "PHP.lns",
    "/etc/fail2ban/fail2ban.conf": "Fail2ban.lns",
    "/etc/fail2ban/jail.conf": "Fail2ban.lns",
    "/etc/fail2ban/jail.local": "Fail2ban.lns",
    "/etc/fail2ban/jail.d/*.conf": "Fail2ban.lns",
    "/etc/fail2ban/jail.d/*.local": "Fail2ban.lns",
}

# Files that use YAML format (not suitable for Augeas)
YAML_FILES: frozenset[str] = frozenset({
    "/etc/netplan/*.yaml",
    "/etc/netplan/*.yml",
    "/etc/docker/daemon.json",  # JSON but similar handling pattern
    "/etc/containerd/config.toml",  # TOML
    "~/.config/systemd/user/*.service",  # User systemd units
})

# Config file domains for categorization
DOMAIN_REGISTRY: dict[str, tuple[str, ...]] = {
    "fstab": ("/etc/fstab", "/etc/mtab"),
    "network": (
        "/etc/hosts",
        "/etc/resolv.conf",
        "/etc/nsswitch.conf",
        "/etc/networks",
    ),
    "ssh": ("/etc/ssh/sshd_config", "/etc/ssh/ssh_config"),
    "sysctl": ("/etc/sysctl.conf", "/etc/sysctl.d/*.conf"),
    "cron": ("/etc/crontab", "/etc/cron.d/*"),
    "apt": ("/etc/apt/sources.list", "/etc/apt/sources.list.d/*.list"),
    "pam": ("/etc/pam.d/*", "/etc/pam.conf"),
    "security": (
        "/etc/security/limits.conf",
        "/etc/security/limits.d/*.conf",
        "/etc/sudoers",
        "/etc/sudoers.d/*",
    ),
    "nginx": (
        "/etc/nginx/nginx.conf",
        "/etc/nginx/sites-available/*",
        "/etc/nginx/sites-enabled/*",
        "/etc/nginx/conf.d/*.conf",
    ),
    "apache": (
        "/etc/apache2/apache2.conf",
        "/etc/apache2/sites-available/*",
        "/etc/apache2/sites-enabled/*",
        "/etc/httpd/conf/httpd.conf",
        "/etc/httpd/conf.d/*.conf",
    ),
    "mysql": (
        "/etc/mysql/my.cnf",
        "/etc/mysql/mysql.conf.d/*.cnf",
        "/etc/mysql/conf.d/*.cnf",
        "/etc/my.cnf",
    ),
    "netplan": ("/etc/netplan/*.yaml", "/etc/netplan/*.yml"),
    "logging": (
        "/etc/rsyslog.conf",
        "/etc/rsyslog.d/*.conf",
        "/etc/logrotate.conf",
        "/etc/logrotate.d/*",
    ),
}


# =============================================================================
# Detection Functions
# =============================================================================


def detect_lens(file_path: str | Path) -> str | None:
    """Detect the appropriate Augeas lens for a file.

    Args:
        file_path: Path to the config file.

    Returns:
        Lens name (e.g., "Fstab.lns") or None if not found.
    """
    path_str = str(file_path)

    # Check exact matches first
    if path_str in LENS_REGISTRY:
        return LENS_REGISTRY[path_str]

    # Check glob patterns
    for pattern, lens in LENS_REGISTRY.items():
        if "*" in pattern:
            if fnmatch.fnmatch(path_str, pattern):
                return lens

    logger.debug(f"No lens found for {path_str}")
    return None


def is_yaml_file(file_path: str | Path) -> bool:
    """Check if a file should be handled as YAML instead of Augeas.

    Args:
        file_path: Path to the config file.

    Returns:
        True if the file is a YAML config file.
    """
    path_str = str(file_path)

    # Check patterns
    for pattern in YAML_FILES:
        if fnmatch.fnmatch(path_str, pattern):
            return True

    # Also check by extension for unregistered YAML files
    if path_str.endswith((".yaml", ".yml")):
        return True

    return False


def detect_domain(file_path: str | Path) -> str:
    """Detect the domain category for a file.

    Args:
        file_path: Path to the config file.

    Returns:
        Domain name (e.g., "fstab", "ssh", "network") or "other".
    """
    path_str = str(file_path)

    for domain, patterns in DOMAIN_REGISTRY.items():
        for pattern in patterns:
            if "*" in pattern:
                if fnmatch.fnmatch(path_str, pattern):
                    return domain
            elif path_str == pattern:
                return domain

    return "other"


def get_supported_files() -> list[str]:
    """Get list of all supported config file patterns.

    Returns:
        List of file path patterns that have lens mappings.
    """
    return list(LENS_REGISTRY.keys())


def get_lens_for_domain(domain: str) -> str | None:
    """Get the primary lens for a domain.

    Args:
        domain: Domain name (e.g., "fstab", "ssh").

    Returns:
        Primary lens name or None if domain not found.
    """
    if domain not in DOMAIN_REGISTRY:
        return None

    # Get first pattern for domain and find its lens
    patterns = DOMAIN_REGISTRY[domain]
    if patterns:
        first_pattern = patterns[0]
        return detect_lens(first_pattern.replace("*", "example"))

    return None


def list_lenses() -> list[str]:
    """Get list of all unique lens names in the registry.

    Returns:
        Sorted list of lens names.
    """
    return sorted(set(LENS_REGISTRY.values()))


def list_domains() -> list[str]:
    """Get list of all domain categories.

    Returns:
        Sorted list of domain names.
    """
    return sorted(DOMAIN_REGISTRY.keys())


# =============================================================================
# Lens Path Helpers
# =============================================================================


def get_augeas_path(file_path: str | Path) -> str:
    """Convert a file path to an Augeas tree path.

    Args:
        file_path: System file path (e.g., /etc/fstab).

    Returns:
        Augeas tree path (e.g., /files/etc/fstab).
    """
    return f"/files{file_path}"


def get_file_path(augeas_path: str) -> str:
    """Convert an Augeas tree path to a file path.

    Args:
        augeas_path: Augeas tree path (e.g., /files/etc/fstab).

    Returns:
        System file path (e.g., /etc/fstab).
    """
    if augeas_path.startswith("/files"):
        return augeas_path[6:]  # Remove "/files" prefix
    return augeas_path


def build_path(base: str, *parts: str | int) -> str:
    """Build an Augeas path from components.

    Args:
        base: Base path (e.g., /files/etc/fstab).
        *parts: Path components (strings or integers for indices).

    Returns:
        Combined path (e.g., /files/etc/fstab/1/spec).

    Example:
        >>> build_path("/files/etc/fstab", 1, "spec")
        '/files/etc/fstab/1/spec'
    """
    path = base.rstrip("/")
    for part in parts:
        if isinstance(part, int):
            path = f"{path}/{part}"
        else:
            path = f"{path}/{part}"
    return path


# =============================================================================
# Validation Patterns
# =============================================================================

# Mapping of domains to validation commands
VALIDATION_COMMANDS: dict[str, tuple[str, ...]] = {
    "fstab": ("mount", "-a", "-f", "--fake"),
    "ssh": ("sshd", "-t", "-f"),  # Append file path
    "sysctl": ("sysctl", "--system", "--dry-run"),
    "netplan": ("netplan", "try", "--timeout", "1"),
    "nginx": ("nginx", "-t"),
    "apache": ("apachectl", "configtest"),
    "cron": ("crontab", "-n"),  # Check syntax only (if supported)
}


def get_validation_command(file_path: str | Path) -> tuple[str, ...] | None:
    """Get the validation command for a config file.

    Args:
        file_path: Path to the config file.

    Returns:
        Validation command tuple or None if no validator exists.
    """
    domain = detect_domain(file_path)
    return VALIDATION_COMMANDS.get(domain)


# =============================================================================
# Lens-Specific Path Helpers
# =============================================================================


def fstab_entry_path(index: int = 1) -> str:
    """Get Augeas path for an fstab entry.

    Args:
        index: Entry index (1-based).

    Returns:
        Path to the fstab entry.
    """
    return f"/files/etc/fstab/{index}"


def fstab_field_path(index: int, field: str) -> str:
    """Get Augeas path for an fstab entry field.

    Args:
        index: Entry index (1-based).
        field: Field name (spec, file, vfstype, opt, dump, passno).

    Returns:
        Path to the field.
    """
    return f"/files/etc/fstab/{index}/{field}"


def hosts_entry_path(index: int = 1) -> str:
    """Get Augeas path for a hosts entry.

    Args:
        index: Entry index (1-based).

    Returns:
        Path to the hosts entry.
    """
    return f"/files/etc/hosts/{index}"


def sshd_setting_path(setting: str) -> str:
    """Get Augeas path for an sshd_config setting.

    Args:
        setting: Setting name (e.g., PermitRootLogin).

    Returns:
        Path to the setting.
    """
    return f"/files/etc/ssh/sshd_config/{setting}"


def sysctl_setting_path(setting: str) -> str:
    """Get Augeas path for a sysctl setting.

    Args:
        setting: Setting name (e.g., net.ipv4.ip_forward).

    Returns:
        Path to the setting.
    """
    return f"/files/etc/sysctl.conf/{setting}"
