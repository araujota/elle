"""Setup wizard models.

Pydantic models for setup state and configuration.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SafetyLevel(str, Enum):
    """Safety level presets for policy configuration."""

    STANDARD = "standard"
    CAUTIOUS = "cautious"
    MINIMAL = "minimal"


class ConfirmationPreference(str, Enum):
    """When to ask for confirmation before executing tasks."""

    ALWAYS = "always"  # Ask for every system change
    HIGH_RISK = "high_risk"  # Only for high-risk operations
    NEVER = "never"  # Never ask (experienced users)


class LLMProviderChoice(str, Enum):
    """LLM provider selection for setup wizard."""

    LOCAL = "local"  # Local Ollama (default)
    REMOTE = "remote"  # Remote OpenAI-compatible endpoint


class PrivilegeLevel(str, Enum):
    """Polkit privilege configuration level."""

    SECURE = "secure"  # Always prompt for password (default)
    CONVENIENT = "convenient"  # Group-based auth (members of 'elle' group skip password)
    PASSWORDLESS = "passwordless"  # No password required (not recommended)


class SetupPreferences(BaseModel):
    """User preferences captured during setup."""

    # Safety and confirmation
    safety_level: SafetyLevel = SafetyLevel.STANDARD
    confirmation_preference: ConfirmationPreference = ConfirmationPreference.HIGH_RISK
    require_preview_for_configs: bool = True

    # Telemetry sources
    journal_enabled: bool = True
    kernel_enabled: bool = True
    probes_enabled: bool = True
    ebpf_enabled: bool = False  # Disabled by default (requires capabilities)
    docker_enabled: bool = True

    # Features
    api_enabled: bool = True

    # Capability learning
    auto_learn_packages: bool = True  # Auto-generate caps for newly installed packages

    # Privilege configuration
    privilege_level: PrivilegeLevel = PrivilegeLevel.SECURE
    polkit_configured: bool = False

    # LLM
    ollama_verified: bool = False
    llm_provider: LLMProviderChoice = LLMProviderChoice.LOCAL
    llm_remote_host: str = ""
    llm_remote_model: str = "gpt-4o"
    llm_remote_verified: bool = False


class SetupState(BaseModel):
    """Persistent state tracking setup completion."""

    completed: bool = False
    completed_at: datetime | None = None
    version: str = "0.1.0"
    preferences: SetupPreferences = Field(default_factory=SetupPreferences)


# Friendly descriptions for each option
SAFETY_LEVEL_INFO = {
    SafetyLevel.STANDARD: {
        "name": "Standard (Recommended)",
        "description": (
            "Blocks dangerous commands like 'rm -rf /' and requires "
            "confirmation for high-risk operations. Good balance of "
            "safety and usability."
        ),
    },
    SafetyLevel.CAUTIOUS: {
        "name": "Cautious",
        "description": (
            "Maximum protection. Requires confirmation for most system "
            "changes and previews for all config edits. Best for "
            "production servers or learning."
        ),
    },
    SafetyLevel.MINIMAL: {
        "name": "Minimal",
        "description": (
            "Only blocks the most dangerous patterns. For experienced "
            "users who want more freedom. Not recommended for beginners."
        ),
    },
}

CONFIRMATION_INFO = {
    ConfirmationPreference.ALWAYS: {
        "name": "Always confirm",
        "description": "Ask before any system change, even low-risk ones.",
    },
    ConfirmationPreference.HIGH_RISK: {
        "name": "High-risk only (Recommended)",
        "description": "Only ask for operations that could impact system stability.",
    },
    ConfirmationPreference.NEVER: {
        "name": "Never",
        "description": "Execute tasks without confirmation. Use with caution.",
    },
}

TELEMETRY_INFO = {
    "journal": {
        "name": "System Journal",
        "description": "Monitor systemd journal for errors and warnings.",
        "default": True,
    },
    "kernel": {
        "name": "Kernel Messages",
        "description": "Watch for kernel-level events (OOM, hardware errors).",
        "default": True,
    },
    "probes": {
        "name": "System Probes",
        "description": "Periodic checks for disk space, memory, and network.",
        "default": True,
    },
    "ebpf": {
        "name": "eBPF Tracing",
        "description": (
            "Advanced kernel tracing for detailed diagnostics. Requires kernel 5.8+ and additional capabilities."
        ),
        "default": False,
    },
    "docker": {
        "name": "Docker Events",
        "description": "Monitor container state changes and health.",
        "default": True,
    },
}

FEATURE_INFO = {
    "api": {
        "name": "REST API",
        "description": ("OpenAI-compatible API endpoint for external integrations. Bound to localhost by default."),
        "default": True,
    },
    "auto_learn_packages": {
        "name": "Auto-Learn Packages",
        "description": (
            "Automatically generate capabilities when new packages are "
            "installed. ELLE will monitor for package changes and learn "
            "them in the background."
        ),
        "default": True,
    },
}

LLM_PROVIDER_INFO = {
    LLMProviderChoice.LOCAL: {
        "name": "Local Ollama (Recommended)",
        "description": (
            "Run inference locally via Ollama. Keeps all data on-device. "
            "Requires Ollama installed with a supported model."
        ),
    },
    LLMProviderChoice.REMOTE: {
        "name": "Remote OpenAI-Compatible",
        "description": (
            "Use a remote LLM endpoint (OpenAI, Azure, vLLM, etc.). "
            "Requires network access and an API key. "
            "Local Ollama can be used as fallback when remote is unavailable."
        ),
    },
}

PRIVILEGE_LEVEL_INFO = {
    PrivilegeLevel.SECURE: {
        "name": "Secure (Recommended)",
        "description": (
            "Always require password for privileged operations. Polkit caches authentication for 5 minutes per session."
        ),
    },
    PrivilegeLevel.CONVENIENT: {
        "name": "Convenient",
        "description": (
            "Members of the 'elle' group can perform ELLE operations without "
            "password prompts. You will be added to this group. Requires logout/login."
        ),
    },
    PrivilegeLevel.PASSWORDLESS: {
        "name": "Passwordless (Not Recommended)",
        "description": (
            "No password required for any ELLE privileged operation. "
            "Only use on single-user systems or development machines."
        ),
        "warning": ("This allows ANY process running as your user to perform privileged system operations via ELLE."),
    },
}
