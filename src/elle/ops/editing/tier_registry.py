"""Pattern-to-tier mappings for the 6-Tier Deterministic Editing Stack.

Defines which files and patterns map to which editing tiers.
This registry is the source of truth for tier selection.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from elle.ops.editing.models import TierInfo, TierNumber

# =============================================================================
# Tier 0: Native Override Patterns
# =============================================================================

# Files that support .d/ drop-in directories
TIER0_DROPIN_PATTERNS: tuple[str, ...] = (
    # systemd drop-ins
    "/etc/systemd/system/*.service",
    "/etc/systemd/system/*.timer",
    "/etc/systemd/system/*.socket",
    "/etc/systemd/system/*.path",
    "/etc/systemd/system/*.mount",
    "/etc/systemd/system/*.automount",
    "/etc/systemd/system/*.target",
    "/etc/systemd/system/*.slice",
    "/etc/systemd/system/*.scope",
    "/lib/systemd/system/*.service",
    "/lib/systemd/system/*.timer",
    "/usr/lib/systemd/system/*.service",
    # sudoers
    "/etc/sudoers",
    # apt sources
    "/etc/apt/sources.list",
    # sysctl
    "/etc/sysctl.conf",
    # modprobe
    "/etc/modprobe.conf",
    # logrotate
    "/etc/logrotate.conf",
    # cron
    "/etc/cron.d/*",
    # udev
    "/etc/udev/rules.d/*",
    # rsyslog
    "/etc/rsyslog.conf",
    # profile
    "/etc/profile",
    # limits
    "/etc/security/limits.conf",
    # tmpfiles
    "/etc/tmpfiles.d/*",
)

# Directories that are already drop-in directories
TIER0_DROPIN_DIRECTORIES: tuple[str, ...] = (
    "/etc/sudoers.d/",
    "/etc/apt/sources.list.d/",
    "/etc/sysctl.d/",
    "/etc/modprobe.d/",
    "/etc/systemd/system/*.service.d/",
    "/etc/systemd/system/*.timer.d/",
    "/etc/logrotate.d/",
    "/etc/rsyslog.d/",
    "/etc/profile.d/",
    "/etc/security/limits.d/",
    "/etc/tmpfiles.d/",
    "/etc/udev/rules.d/",
)


# =============================================================================
# Tier 1: Augeas Patterns (inherits from elle.ops.augeas.lenses)
# =============================================================================

# These are files that Augeas has lenses for
# We'll dynamically check against the Augeas lens registry
TIER1_AUGEAS_PATTERNS: tuple[str, ...] = (
    # System config
    "/etc/fstab",
    "/etc/hosts",
    "/etc/passwd",
    "/etc/shadow",
    "/etc/group",
    "/etc/gshadow",
    "/etc/hostname",
    "/etc/resolv.conf",
    "/etc/nsswitch.conf",
    # SSH
    "/etc/ssh/sshd_config",
    "/etc/ssh/ssh_config",
    "~/.ssh/config",
    # Apache
    "/etc/apache2/*.conf",
    "/etc/apache2/**/*.conf",
    "/etc/httpd/*.conf",
    "/etc/httpd/**/*.conf",
    # Nginx
    "/etc/nginx/*.conf",
    "/etc/nginx/**/*.conf",
    # PHP
    "/etc/php/*.ini",
    "/etc/php/**/*.ini",
    # MySQL
    "/etc/mysql/*.cnf",
    "/etc/mysql/**/*.cnf",
    # PostgreSQL
    "/etc/postgresql/**/*.conf",
    # Samba
    "/etc/samba/smb.conf",
    # NFS
    "/etc/exports",
    # Cron
    "/etc/crontab",
    "/etc/cron.d/*",
    # PAM
    "/etc/pam.d/*",
    # GRUB
    "/etc/default/grub",
    # Network
    "/etc/network/interfaces",
    "/etc/network/interfaces.d/*",
    # Sysctl
    "/etc/sysctl.conf",
    "/etc/sysctl.d/*.conf",
    # Logrotate
    "/etc/logrotate.conf",
    "/etc/logrotate.d/*",
)


# =============================================================================
# Tier 2: Structured Data Patterns
# =============================================================================

TIER2_JSON_PATTERNS: tuple[str, ...] = (
    "*.json",
    "*.jsonc",
    ".json",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "composer.json",
    "/etc/docker/daemon.json",
    "*.sublime-settings",
    "*.sublime-project",
    ".prettierrc",
    ".eslintrc",
    ".babelrc",
)

TIER2_YAML_PATTERNS: tuple[str, ...] = (
    "*.yaml",
    "*.yml",
    ".yaml",
    ".yml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.*.yml",
    "docker-compose.*.yaml",
    ".travis.yml",
    ".gitlab-ci.yml",
    "mkdocs.yml",
    "ansible.cfg",
    "/etc/netplan/*.yaml",
    "/etc/kubernetes/**/*.yaml",
    "*.values.yaml",
    "Chart.yaml",
    "values.yaml",
)

TIER2_TOML_PATTERNS: tuple[str, ...] = (
    "*.toml",
    "Cargo.toml",
    "pyproject.toml",
    "poetry.toml",
    "Pipfile",
    ".taplo.toml",
)


# =============================================================================
# Tier 3: Format Editor Patterns
# =============================================================================

TIER3_INI_PATTERNS: tuple[str, ...] = (
    "*.ini",
    "*.cfg",
    "*.conf",
    "setup.cfg",
    "tox.ini",
    ".editorconfig",
    ".gitconfig",
    "~/.gitconfig",
    "~/.config/**/*.ini",
    "~/.config/**/*.conf",
    "/etc/*.conf",
    # OpenStack config
    "/etc/nova/*.conf",
    "/etc/neutron/*.conf",
    "/etc/keystone/*.conf",
    "/etc/glance/*.conf",
    # Systemd unit files (non-drop-in edits)
    "/etc/systemd/**/*.conf",
)

TIER3_XML_PATTERNS: tuple[str, ...] = (
    "*.xml",
    "*.xhtml",
    "*.svg",
    "*.plist",
    "pom.xml",
    "web.xml",
    "beans.xml",
    "persistence.xml",
    "AndroidManifest.xml",
    "/etc/dbus-1/**/*.conf",
    "/etc/fonts/**/*.conf",
)


# =============================================================================
# Tier 4: Document-aware Patterns
# =============================================================================

TIER4_MARKDOWN_PATTERNS: tuple[str, ...] = (
    "*.md",
    "*.markdown",
    "*.mdown",
    "README*",
    "CHANGELOG*",
    "CONTRIBUTING*",
    "LICENSE*",
    "AUTHORS*",
)

TIER4_RST_PATTERNS: tuple[str, ...] = (
    "*.rst",
    "*.rest",
)


# =============================================================================
# Tier Information
# =============================================================================

TIER_REGISTRY: dict[int, TierInfo] = {
    0: TierInfo(
        number=0,
        name="Native Override",
        tools=("systemd drop-ins", "*.d/ directories"),
        use_case="Additive changes without touching original files",
    ),
    1: TierInfo(
        number=1,
        name="Augeas",
        tools=("python-augeas", "augtool"),
        use_case="Grammar-based structured editing of config files",
    ),
    2: TierInfo(
        number=2,
        name="Structured Data",
        tools=("jq", "yq", "toml (Python)"),
        use_case="JSON/YAML/TOML transformations",
    ),
    3: TierInfo(
        number=3,
        name="Format Editors",
        tools=("crudini", "xmlstarlet"),
        use_case="INI/XML format-specific editing",
    ),
    4: TierInfo(
        number=4,
        name="Document-aware",
        tools=("markdown-it", "docutils"),
        use_case="Preserve document structure in Markdown/RST",
    ),
    5: TierInfo(
        number=5,
        name="Text Patching",
        tools=("anchor-based text matching",),
        use_case="Last resort with context verification (requires justification)",
    ),
}


# =============================================================================
# Pattern Matching Utilities
# =============================================================================


def matches_pattern(file_path: str, patterns: tuple[str, ...]) -> bool:
    """Check if a file path matches any of the given patterns.

    Args:
        file_path: Path to check.
        patterns: Glob patterns to match against.

    Returns:
        True if the path matches any pattern.
    """
    path = Path(file_path)
    # Use both the original path and resolved path for matching
    # This handles macOS where /etc -> /private/etc
    paths_to_check = [str(path)]
    try:
        resolved = str(path.resolve())
        if resolved != paths_to_check[0]:
            paths_to_check.append(resolved)
    except OSError:
        pass

    for pattern in patterns:
        # Expand ~ in pattern
        if pattern.startswith("~"):
            pattern = str(Path(pattern).expanduser())

        for path_str in paths_to_check:
            # Check if pattern is absolute or relative
            if pattern.startswith("/"):
                # Absolute pattern - match full path
                if fnmatch.fnmatch(path_str, pattern):
                    return True
            else:
                # Relative pattern - match against filename or full path
                if fnmatch.fnmatch(Path(path_str).name, pattern):
                    return True
                if fnmatch.fnmatch(path_str, f"*/{pattern}"):
                    return True

    return False


def get_file_extension(file_path: str) -> str:
    """Get the file extension (lowercase, without dot).

    Args:
        file_path: Path to the file.

    Returns:
        File extension without leading dot, lowercase.
    """
    path = Path(file_path)
    # Handle files like .gitconfig (no extension, name is the type)
    if path.suffix:
        return path.suffix.lower().lstrip(".")
    return ""


def is_dropin_candidate(file_path: str) -> tuple[bool, str | None]:
    """Check if a file supports drop-in overrides.

    Args:
        file_path: Path to check.

    Returns:
        Tuple of (supports_dropin, dropin_directory_path).
    """
    # Use the original path for comparison (not resolved)
    # This handles macOS where /etc -> /private/etc
    path_str = str(Path(file_path))

    # Normalize to /etc/ prefix for comparison
    normalized_path = path_str
    if path_str.startswith("/private/etc/"):
        normalized_path = path_str.replace("/private/etc/", "/etc/", 1)

    # Check if it's a file that supports .d/ directories
    if matches_pattern(file_path, TIER0_DROPIN_PATTERNS):
        # Determine the drop-in directory path
        if normalized_path.startswith("/etc/systemd/system/"):
            # systemd unit files use <unit>.d/
            dropin_dir = f"{normalized_path}.d"
        elif normalized_path == "/etc/sudoers":
            dropin_dir = "/etc/sudoers.d"
        elif normalized_path == "/etc/apt/sources.list":
            dropin_dir = "/etc/apt/sources.list.d"
        elif normalized_path == "/etc/sysctl.conf":
            dropin_dir = "/etc/sysctl.d"
        elif normalized_path == "/etc/modprobe.conf":
            dropin_dir = "/etc/modprobe.d"
        elif normalized_path == "/etc/logrotate.conf":
            dropin_dir = "/etc/logrotate.d"
        elif normalized_path == "/etc/rsyslog.conf":
            dropin_dir = "/etc/rsyslog.d"
        elif normalized_path == "/etc/profile":
            dropin_dir = "/etc/profile.d"
        elif normalized_path == "/etc/security/limits.conf":
            dropin_dir = "/etc/security/limits.d"
        else:
            # Generic .d directory
            dropin_dir = f"{normalized_path}.d"
        return (True, dropin_dir)

    # Check if it's already in a drop-in directory
    for dir_pattern in TIER0_DROPIN_DIRECTORIES:
        if dir_pattern.startswith("~"):
            dir_pattern = str(Path(dir_pattern).expanduser())
        # Check if path is inside a drop-in directory
        # Match the directory part of the path against patterns
        parent_dir = str(Path(normalized_path).parent) + "/"
        if fnmatch.fnmatch(parent_dir, dir_pattern):
            return (True, None)  # Already in drop-in dir

    return (False, None)


def detect_tier_from_path(file_path: str) -> TierNumber | None:
    """Detect the most appropriate tier based on file path alone.

    This is a quick heuristic check. The TierSelector does full validation.

    Args:
        file_path: Path to the file.

    Returns:
        Suggested tier number, or None if unknown.
    """
    # Check Tier 0 (drop-ins)
    supports_dropin, _ = is_dropin_candidate(file_path)
    if supports_dropin:
        return 0

    # Check Tier 1 (Augeas)
    if matches_pattern(file_path, TIER1_AUGEAS_PATTERNS):
        return 1

    # Check Tier 2 (Structured Data)
    if matches_pattern(file_path, TIER2_JSON_PATTERNS):
        return 2
    if matches_pattern(file_path, TIER2_YAML_PATTERNS):
        return 2
    if matches_pattern(file_path, TIER2_TOML_PATTERNS):
        return 2

    # Check Tier 3 (Format Editors)
    if matches_pattern(file_path, TIER3_INI_PATTERNS):
        return 3
    if matches_pattern(file_path, TIER3_XML_PATTERNS):
        return 3

    # Check Tier 4 (Document-aware)
    if matches_pattern(file_path, TIER4_MARKDOWN_PATTERNS):
        return 4
    if matches_pattern(file_path, TIER4_RST_PATTERNS):
        return 4

    # Default to Tier 5 (text patching) for unknown files
    return 5


def get_tier_info(tier: TierNumber) -> TierInfo:
    """Get information about a specific tier.

    Args:
        tier: Tier number (0-5).

    Returns:
        TierInfo for the requested tier.
    """
    return TIER_REGISTRY[tier]


def list_all_tiers() -> list[TierInfo]:
    """Get information about all tiers.

    Returns:
        List of TierInfo for all tiers in order.
    """
    return [TIER_REGISTRY[i] for i in range(6)]
