"""Reboot Persistence and Recovery System.

SAFETY-CRITICAL: This module enables ELLE to persist intent/context before
reboot, use GRUB one-shot boot for automatic rollback on boot failure,
run verification checks post-reboot, and automatically rollback if
critical services fail.

Safety Principles:
- No silent reboots - always explicit user confirmation
- GRUB fallback to known-good config on boot failure
- Maximum 3 verification retries before declaring failure
- 60-second user intervention window before auto-rollback
- All state persisted to SQLite (survives crashes)

Public API:

    Models:
        - RebootIntent: Complete reboot intent record
        - RebootIntentSummary: Summary for listings
        - RebootStatus: Status report
        - PendingVerification: Post-boot check definition
        - GRUBState: GRUB configuration state

    Manager Functions:
        - get_manager(): Get the RebootManager singleton
        - RebootManager.create_reboot_intent(): Create new intent
        - RebootManager.execute_reboot(): Execute pending reboot
        - RebootManager.check_pending_reboots(): Post-boot recovery
        - RebootManager.cancel_pending_reboot(): Cancel before reboot
        - RebootManager.cancel_pending_rollback(): Cancel auto-rollback
        - RebootManager.force_confirm_boot(): Override verification
        - RebootManager.force_rollback(): Manual rollback

    Store Functions:
        - get_intent(): Get intent by ID
        - get_active_intent(): Get currently active intent
        - list_intents(): List intents with filtering
        - list_recent_intents(): Get recent intent summaries
        - get_reboot_status(): Get module status report

    GRUB Functions:
        - get_boot_id(): Get current systemd boot ID
        - get_grub_state(): Get GRUB configuration
        - has_rebooted_since(): Check if reboot occurred

    Verifier Functions:
        - create_default_verifications(): Comprehensive default checks
        - create_kernel_update_verifications(): Kernel-specific checks
        - create_driver_update_verifications(): Driver/module-specific checks
        - create_fstab_change_verifications(): Mount/fstab-specific checks
        - create_service_restart_verifications(): Service-specific checks
        - create_network_config_verifications(): Network-specific checks
        - create_grub_update_verifications(): GRUB-specific checks
        - collect_failure_diagnostics(): Collect detailed diagnostics

    Diagnostics:
        - FailureDiagnostics: Comprehensive failure diagnostics for LLM

Example Usage:
    from elle.daemon.reboot import (
        get_manager,
        create_default_verifications,
        get_reboot_status,
    )

    manager = get_manager()

    # Create reboot intent
    intent = await manager.create_reboot_intent(
        goal="Install kernel security updates",
        task_description="Update to kernel 6.8.0-45",
        reason="kernel_update",
        verifications=create_default_verifications(),
    )

    # Execute reboot (requires user confirmation)
    await manager.execute_reboot(intent.id)

    # On next boot, daemon checks:
    await manager.check_pending_reboots()

    # Get status
    status = get_reboot_status()
"""

# Models
from elle.daemon.reboot.models import (
    AUTO_ROLLBACK_DELAY_SEC,
    CRITICAL_MOUNT_POINTS,
    CRITICAL_SERVICES,
    DISK_SPACE_REQUIREMENTS,
    DMESG_ERROR_PATTERNS,
    JOURNAL_ERROR_PATTERNS,
    MAX_VERIFICATION_ATTEMPTS,
    NETWORK_CHECK_TIMEOUT_SEC,
    VERIFICATION_DELAYS,
    VERIFICATION_TIMEOUT_SEC,
    FailureDiagnostics,
    GRUBState,
    PendingVerification,
    RebootIntent,
    RebootIntentStatus,
    RebootIntentSummary,
    RebootOutcome,
    RebootReason,
    RebootStatus,
)

# Schema
from elle.daemon.reboot.schema import (
    DB_PATH,
    ensure_schema,
    get_connection,
    get_db_path,
    init_reboot_schema,
)

# Store (CRUD)
from elle.daemon.reboot.store import (
    add_verification,
    cancel_intent,
    create_intent,
    delete_intent,
    get_active_intent,
    get_intent,
    get_intent_count,
    get_intents_by_status,
    get_reboot_status,
    get_verifications,
    list_intents,
    list_recent_intents,
    mark_rebooting,
    reset_verifications,
    update_intent_status,
    update_verification_result,
)

# GRUB
from elle.daemon.reboot.grub import (
    GRUBError,
    GRUBResult,
    clear_grub_oneshot,
    confirm_boot_success,
    detect_grub_mode,
    get_boot_id,
    get_grub_default,
    get_grub_entries,
    get_grub_state,
    get_saved_default,
    has_rebooted_since,
    is_grub_available,
    prepare_rollback,
    set_grub_default,
    set_grub_oneshot,
    trigger_rollback_reboot,
)

# Verifier
from elle.daemon.reboot.verifier import (
    VerificationResult,
    check_critical_services,
    check_network_connectivity,
    collect_failure_diagnostics,
    create_default_verifications,
    create_driver_update_verifications,
    create_fstab_change_verifications,
    create_grub_update_verifications,
    create_kernel_update_verifications,
    create_network_config_verifications,
    create_service_restart_verifications,
    run_all_verifications,
    run_verification,
    run_verification_with_retry,
)

# Manager
from elle.daemon.reboot.manager import (
    RebootManager,
    RebootManagerError,
    get_manager,
)

__all__ = [
    # Constants
    "AUTO_ROLLBACK_DELAY_SEC",
    "CRITICAL_MOUNT_POINTS",
    "CRITICAL_SERVICES",
    "DB_PATH",
    "DISK_SPACE_REQUIREMENTS",
    "DMESG_ERROR_PATTERNS",
    "JOURNAL_ERROR_PATTERNS",
    "MAX_VERIFICATION_ATTEMPTS",
    "NETWORK_CHECK_TIMEOUT_SEC",
    "VERIFICATION_DELAYS",
    "VERIFICATION_TIMEOUT_SEC",
    # Type aliases
    "RebootIntentStatus",
    "RebootOutcome",
    "RebootReason",
    # Models
    "FailureDiagnostics",
    "GRUBState",
    "PendingVerification",
    "RebootIntent",
    "RebootIntentSummary",
    "RebootStatus",
    "VerificationResult",
    # Errors
    "GRUBError",
    "GRUBResult",
    "RebootManagerError",
    # Schema
    "ensure_schema",
    "get_connection",
    "get_db_path",
    "init_reboot_schema",
    # Store
    "add_verification",
    "cancel_intent",
    "create_intent",
    "delete_intent",
    "get_active_intent",
    "get_intent",
    "get_intent_count",
    "get_intents_by_status",
    "get_reboot_status",
    "get_verifications",
    "list_intents",
    "list_recent_intents",
    "mark_rebooting",
    "reset_verifications",
    "update_intent_status",
    "update_verification_result",
    # GRUB
    "clear_grub_oneshot",
    "confirm_boot_success",
    "detect_grub_mode",
    "get_boot_id",
    "get_grub_default",
    "get_grub_entries",
    "get_grub_state",
    "get_saved_default",
    "has_rebooted_since",
    "is_grub_available",
    "prepare_rollback",
    "set_grub_default",
    "set_grub_oneshot",
    "trigger_rollback_reboot",
    # Verifier
    "check_critical_services",
    "check_network_connectivity",
    "collect_failure_diagnostics",
    "create_default_verifications",
    "create_driver_update_verifications",
    "create_fstab_change_verifications",
    "create_grub_update_verifications",
    "create_kernel_update_verifications",
    "create_network_config_verifications",
    "create_service_restart_verifications",
    "run_all_verifications",
    "run_verification",
    "run_verification_with_retry",
    # Manager
    "RebootManager",
    "get_manager",
]
