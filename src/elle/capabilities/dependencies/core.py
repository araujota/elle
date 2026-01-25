"""Core dependency definitions.

Defines the built-in dependencies that ELLE knows how to check and install,
along with the security allowlist of permitted packages.
"""

from __future__ import annotations

from elle.capabilities.dependencies.models import DependencySpec

# =============================================================================
# Core Dependencies
# =============================================================================

ATSPI_DEPENDENCY = DependencySpec(
    name="atspi",
    display_name="AT-SPI Accessibility",
    description="Required for GUI automation and learning application interfaces",
    apt_packages=("python3-pyatspi", "at-spi2-core"),
    check_import="gi.repository.Atspi",
    required_by=(
        "gui.learn",
        "gui.click",
        "gui.type",
        "gui.navigate",
        "gui.execute_task",
    ),
)

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


# =============================================================================
# Core Dependencies Collection
# =============================================================================

CORE_DEPENDENCIES: tuple[DependencySpec, ...] = (
    ATSPI_DEPENDENCY,
    AUGEAS_DEPENDENCY,
    DOCKER_DEPENDENCY,
    WIREGUARD_DEPENDENCY,
    SMARTMONTOOLS_DEPENDENCY,
    LM_SENSORS_DEPENDENCY,
    NET_TOOLS_DEPENDENCY,
)


# =============================================================================
# Security Allowlist
# =============================================================================

# CRITICAL: Only packages in this set can ever be installed by the dependency system.
# This prevents arbitrary package installation via prompt injection or other attacks.
ALLOWED_PACKAGES: frozenset[str] = frozenset({
    # AT-SPI for GUI automation
    "python3-pyatspi",
    "at-spi2-core",
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
    # Python GObject introspection (needed for AT-SPI)
    "gir1.2-atspi-2.0",
    "python3-gi",
})
