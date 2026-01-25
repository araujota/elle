"""ELLE Setup Wizard.

A friendly, interactive wizard for first-time configuration.

Usage:
    from elle.cli.setup import is_first_run, run_setup_wizard

    if is_first_run():
        run_setup_wizard(prompt)
"""

from elle.cli.setup.models import (
    CONFIRMATION_INFO,
    FEATURE_INFO,
    SAFETY_LEVEL_INFO,
    TELEMETRY_INFO,
    ConfirmationPreference,
    SafetyLevel,
    SetupPreferences,
    SetupState,
)
from elle.cli.setup.wizard import (
    is_first_run,
    load_setup_state,
    run_setup_wizard,
    save_setup_state,
)

__all__ = [
    # Functions
    "is_first_run",
    "load_setup_state",
    "run_setup_wizard",
    "save_setup_state",
    # Models
    "ConfirmationPreference",
    "SafetyLevel",
    "SetupPreferences",
    "SetupState",
    # Info dicts
    "CONFIRMATION_INFO",
    "FEATURE_INFO",
    "SAFETY_LEVEL_INFO",
    "TELEMETRY_INFO",
]
