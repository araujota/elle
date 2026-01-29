"""Core dependency definitions.

Defines the built-in dependencies that ELLE knows how to check and install,
along with the security allowlist of permitted packages.
"""

from __future__ import annotations

from elle.capabilities.dependencies.models import DependencySpec

# =============================================================================
# Core Dependencies
# =============================================================================

AUGEAS_DEPENDENCY = DependencySpec(
    name="augeas",
    display_name="Augeas Configuration Editor",
    description="Required for safe configuration file editing with validation",
    apt_packages=("augeas-tools", "python3-augeas"),
    check_command="augtool",
    check_file="/usr/share/augeas/lenses",
    required_by=("config.edit", "config.validate"),
)

DOCKER_DEPENDENCY = DependencySpec(
    name="docker",
    display_name="Docker Container Engine",
    description="Required for container management operations",
    apt_packages=("docker.io",),
    check_command="docker",
    required_by=(
        "docker.run",
        "docker.stop",
        "docker.logs",
        "docker.prune",
        "docker.inspect",
        "docker.list",
        "docker.rollback",
    ),
)

WIREGUARD_DEPENDENCY = DependencySpec(
    name="wireguard",
    display_name="WireGuard VPN",
    description="Required for VPN tunnel configuration",
    apt_packages=("wireguard", "wireguard-tools"),
    check_command="wg",
    required_by=("wireguard.restart", "wireguard.status"),
)

SMARTMONTOOLS_DEPENDENCY = DependencySpec(
    name="smartmontools",
    display_name="S.M.A.R.T. Monitoring Tools",
    description="Required for disk health monitoring",
    apt_packages=("smartmontools",),
    check_command="smartctl",
    required_by=("disk.smart",),
    optional=True,
)

LM_SENSORS_DEPENDENCY = DependencySpec(
    name="lm-sensors",
    display_name="Hardware Sensors",
    description="Required for temperature and voltage monitoring",
    apt_packages=("lm-sensors",),
    check_command="sensors",
    required_by=("system.sensors",),
    optional=True,
)

NET_TOOLS_DEPENDENCY = DependencySpec(
    name="net-tools",
    display_name="Network Tools",
    description="Required for legacy network diagnostics (ifconfig, netstat, etc.)",
    apt_packages=("net-tools",),
    check_command="netstat",
    required_by=("network.listeners",),
    optional=True,  # Can fall back to ss
)

JQ_DEPENDENCY = DependencySpec(
    name="jq",
    display_name="jq JSON Processor",
    description="Required for JSON file editing and transformation",
    apt_packages=("jq",),
    check_command="jq",
    required_by=("file.edit",),
    optional=True,  # Can fall back to Python json module
)

YQ_DEPENDENCY = DependencySpec(
    name="yq",
    display_name="yq YAML Processor",
    description="Required for YAML file editing and transformation",
    apt_packages=(),  # Installed via pip
    pip_packages=("yq",),
    check_command="yq",
    required_by=("file.edit",),
    optional=True,  # Can fall back to ruamel.yaml
)

CRUDINI_DEPENDENCY = DependencySpec(
    name="crudini",
    display_name="crudini INI Editor",
    description="Required for INI/CFG configuration file editing",
    apt_packages=("crudini",),
    check_command="crudini",
    required_by=("file.edit",),
    optional=True,  # Can fall back to configparser
)

XMLSTARLET_DEPENDENCY = DependencySpec(
    name="xmlstarlet",
    display_name="XMLStarlet XML Toolkit",
    description="Required for XML file editing, validation, and transformation",
    apt_packages=("xmlstarlet",),
    check_command="xmlstarlet",
    required_by=("file.edit",),
    optional=True,  # Can fall back to Python xml.etree
)


# =============================================================================
# Core Dependencies Collection
# =============================================================================

CORE_DEPENDENCIES: tuple[DependencySpec, ...] = (
    AUGEAS_DEPENDENCY,
    DOCKER_DEPENDENCY,
    WIREGUARD_DEPENDENCY,
    SMARTMONTOOLS_DEPENDENCY,
    LM_SENSORS_DEPENDENCY,
    NET_TOOLS_DEPENDENCY,
    JQ_DEPENDENCY,
    YQ_DEPENDENCY,
    CRUDINI_DEPENDENCY,
    XMLSTARLET_DEPENDENCY,
)


# =============================================================================
# Security Allowlist
# =============================================================================

# CRITICAL: Only packages in this set can ever be installed by the dependency system.
# This prevents arbitrary package installation via prompt injection or other attacks.
ALLOWED_PACKAGES: frozenset[str] = frozenset(
    {
        # Augeas for config editing
        "augeas-tools",
        "python3-augeas",
        "libaugeas0",
        # Docker for container management
        "docker.io",
        "docker-compose",
        "docker-compose-v2",
        # WireGuard for VPN
        "wireguard",
        "wireguard-tools",
        # Network utilities
        "net-tools",
        "iproute2",
        "dnsutils",
        "iputils-ping",
        # System monitoring
        "smartmontools",
        "lm-sensors",
        "htop",
        "iotop",
        # Python GObject introspection
        "python3-gi",
        # File editing tools
        "jq",
        "crudini",
        "xmlstarlet",
    }
)
