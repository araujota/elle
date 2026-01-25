"""Dependency management system for ELLE.

Provides graceful fallback when capabilities require packages that aren't installed:
1. Detection: Check if dependencies are available
2. User Communication: Explain what's missing and why
3. Installation Prompt: Ask user if they want to install
4. Secure Installation: Install with strict allowlist enforcement
5. Preference Memory: Remember user choices for future runs

Example:
    from elle.capabilities.dependencies import (
        check_dependency,
        check_dependencies_for_capability,
        get_preference,
        set_preference,
        install_dependency,
        InstallationRequest,
    )

    # Check if AT-SPI is available
    result = check_dependency("atspi")
    if not result.available:
        print(f"Missing: {result.install_command}")

    # Check dependencies for a capability
    missing = check_dependencies_for_capability("gui.learn")
    for dep_result in missing:
        print(f"Need to install: {dep_result.dependency}")

    # Check user preference
    pref = get_preference("atspi")
    if pref == "always_install":
        # Auto-install
        request = InstallationRequest(
            dependency="atspi",
            apt_packages=("python3-pyatspi", "at-spi2-core"),
            reason="Required for GUI automation",
        )
        install_result = await install_dependency(request)
"""

from __future__ import annotations

# Models
from elle.capabilities.dependencies.models import (
    DependencyCheckResult,
    DependencyPreference,
    DependencySpec,
    InstallationRequest,
    InstallationResult,
    PreferenceChoice,
)

# Core dependencies
from elle.capabilities.dependencies.core import (
    ALLOWED_PACKAGES,
    ATSPI_DEPENDENCY,
    AUGEAS_DEPENDENCY,
    CORE_DEPENDENCIES,
    DOCKER_DEPENDENCY,
    WIREGUARD_DEPENDENCY,
)

# Registry
from elle.capabilities.dependencies.registry import (
    DependencyRegistry,
    get_registry,
    reset_registry,
)

# Checker
from elle.capabilities.dependencies.checker import (
    DependencyChecker,
    check_dependency,
    check_dependencies_for_capability,
    get_checker,
    reset_checker,
)

# Store
from elle.capabilities.dependencies.store import (
    DependencyPreferenceStore,
    get_preference,
    get_store,
    reset_store,
    set_preference,
)

# Installer
from elle.capabilities.dependencies.installer import (
    DependencyInstaller,
    DependencySecurityError,
    get_installer,
    install_dependency,
    reset_installer,
)

__all__ = [
    # Models
    "DependencySpec",
    "DependencyCheckResult",
    "DependencyPreference",
    "InstallationRequest",
    "InstallationResult",
    "PreferenceChoice",
    # Core dependencies
    "ALLOWED_PACKAGES",
    "CORE_DEPENDENCIES",
    "ATSPI_DEPENDENCY",
    "AUGEAS_DEPENDENCY",
    "DOCKER_DEPENDENCY",
    "WIREGUARD_DEPENDENCY",
    # Registry
    "DependencyRegistry",
    "get_registry",
    "reset_registry",
    # Checker
    "DependencyChecker",
    "get_checker",
    "reset_checker",
    "check_dependency",
    "check_dependencies_for_capability",
    # Store
    "DependencyPreferenceStore",
    "get_store",
    "reset_store",
    "get_preference",
    "set_preference",
    # Installer
    "DependencyInstaller",
    "DependencySecurityError",
    "get_installer",
    "reset_installer",
    "install_dependency",
]
