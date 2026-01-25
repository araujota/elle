"""Augeas-based configuration file editing system.

DEPRECATION NOTICE:
    This module is an internal implementation. For policy-governed config
    editing with audit trails, use the Capabilities system instead:

        from elle.capabilities import execute_capability
        from elle.capabilities.core import ConfigEditInput, ConfigOperation

        result = await execute_capability(
            "config.edit",
            ConfigEditInput(
                file_path="/etc/hosts",
                operations=(
                    ConfigOperation(kind="set", path="...", value="..."),
                ),
                description="Add myserver to hosts",
            ),
        )

    The capability provides policy enforcement, incident integration,
    and automatic rollback on failure.

Provides a safe, robust mechanism for structured config file manipulation
with mandatory preview/diff, automatic backup, validation, and rollback.

Public API:
    Models:
        - AugeasOp: Single operation (set, rm, ins, mv)
        - AugeasChange: Record of a change made
        - AugeasEditRequest: Request to edit a config file
        - AugeasEditResult: Result of an edit operation
        - EditPreview: Preview of proposed changes
        - BatchEditRequest: Request for multiple atomic edits
        - BatchEditResult: Result of batch edit
        - BackupRecord: Record of a file backup

    Engine:
        - AugeasEngine: Core Augeas wrapper for config manipulation
        - is_available(): Check if python-augeas is installed
        - get_version(): Get Augeas library version

    Lenses:
        - detect_lens(): Auto-detect lens for a file
        - detect_domain(): Get domain category for a file
        - is_yaml_file(): Check if file should use YAML handler

    Controller:
        - preview_edit(): Preview changes without applying
        - execute_edit(): Execute an edit with full workflow
        - execute_batch_edit(): Execute multiple edits atomically
        - rollback_edit(): Rollback to backup

    Backup:
        - backup_file(): Create a backup of a file
        - restore_file(): Restore from backup
        - list_backups(): List available backups
        - get_latest_backup(): Get most recent backup
        - cleanup_backups(): Clean up old backups

    Validators:
        - validate_config(): Validate a config file
        - get_validator(): Get validator for a file type

    Diff:
        - generate_diff(): Generate diff between contents
        - unified_diff(): Generate unified diff string
        - colored_diff(): Generate ANSI-colored diff

    YAML:
        - YAMLHandler: Handler for YAML config files
        - NetplanHandler: Specialized handler for netplan
        - load_yaml(): Load a YAML file
        - save_yaml(): Save to YAML file

Usage:
    from elle.ops.augeas import (
        AugeasEditRequest,
        AugeasOp,
        preview_edit,
        execute_edit,
    )

    # Create an edit request
    request = AugeasEditRequest(
        file_path="/etc/hosts",
        operations=(
            AugeasOp(kind="set", path="/files/etc/hosts/2/ipaddr", value="192.168.1.100"),
            AugeasOp(kind="set", path="/files/etc/hosts/2/canonical", value="myserver"),
        ),
        description="Add myserver to hosts file",
    )

    # Preview first (mandatory)
    preview = preview_edit(request)
    print(preview.diff_colored)

    # Execute if approved
    result = await execute_edit(request)
    if result.success:
        print("Changes applied!")
    else:
        print(f"Failed: {result.error}")
"""

# Models
# Backup
from elle.ops.augeas.backup import (
    BackupManager,
    backup_file,
    cleanup_backups,
    get_latest_backup,
    list_backups,
    restore_file,
)
from elle.ops.augeas.backup import (
    get_manager as get_backup_manager,
)

# Controller
from elle.ops.augeas.controller import (
    EditController,
    execute_batch_edit,
    execute_edit,
    get_controller,
    preview_edit,
    rollback_edit,
)

# Diff
from elle.ops.augeas.diff import (
    Colors,
    DiffGenerator,
    DiffResult,
    DiffStats,
    colored_diff,
    generate_diff,
    has_changes,
    unified_diff,
)

# Engine
from elle.ops.augeas.engine import (
    AugeasEngine,
    get_version,
    is_available,
)

# Lenses
from elle.ops.augeas.lenses import (
    build_path,
    detect_domain,
    detect_lens,
    fstab_entry_path,
    fstab_field_path,
    get_augeas_path,
    get_file_path,
    get_lens_for_domain,
    get_supported_files,
    get_validation_command,
    hosts_entry_path,
    is_yaml_file,
    list_domains,
    list_lenses,
    sshd_setting_path,
    sysctl_setting_path,
)
from elle.ops.augeas.models import (
    AugeasChange,
    AugeasEditRequest,
    AugeasEditResult,
    AugeasError,
    AugeasLensNotFoundError,
    AugeasOp,
    AugeasParseError,
    AugeasPermissionError,
    AugeasStatus,
    AugeasUnavailableError,
    AugeasValidationError,
    BackupListResult,
    BackupRecord,
    BatchEditRequest,
    BatchEditResult,
    EditPreview,
)

# Validators
from elle.ops.augeas.validators import (
    ConfigValidator,
    ValidationResult,
    get_validator,
    list_validatable_domains,
    list_validators,
    validate_config,
)

# YAML Handler
from elle.ops.augeas.yaml_handler import (
    NetplanHandler,
    YAMLChange,
    YAMLEditResult,
    YAMLHandler,
    get_yaml_value,
    load_yaml,
    save_yaml,
    set_yaml_value,
)
from elle.ops.augeas.yaml_handler import (
    is_available as yaml_is_available,
)

__all__ = [
    # Models
    "AugeasChange",
    "AugeasEditRequest",
    "AugeasEditResult",
    "AugeasError",
    "AugeasLensNotFoundError",
    "AugeasOp",
    "AugeasParseError",
    "AugeasPermissionError",
    "AugeasStatus",
    "AugeasUnavailableError",
    "AugeasValidationError",
    "BackupListResult",
    "BackupRecord",
    "BatchEditRequest",
    "BatchEditResult",
    "EditPreview",
    # Engine
    "AugeasEngine",
    "get_version",
    "is_available",
    # Lenses
    "build_path",
    "detect_domain",
    "detect_lens",
    "fstab_entry_path",
    "fstab_field_path",
    "get_augeas_path",
    "get_file_path",
    "get_lens_for_domain",
    "get_supported_files",
    "get_validation_command",
    "hosts_entry_path",
    "is_yaml_file",
    "list_domains",
    "list_lenses",
    "sshd_setting_path",
    "sysctl_setting_path",
    # Controller
    "EditController",
    "execute_batch_edit",
    "execute_edit",
    "get_controller",
    "preview_edit",
    "rollback_edit",
    # Backup
    "BackupManager",
    "backup_file",
    "cleanup_backups",
    "get_backup_manager",
    "get_latest_backup",
    "list_backups",
    "restore_file",
    # Validators
    "ConfigValidator",
    "ValidationResult",
    "get_validator",
    "list_validatable_domains",
    "list_validators",
    "validate_config",
    # Diff
    "Colors",
    "DiffGenerator",
    "DiffResult",
    "DiffStats",
    "colored_diff",
    "generate_diff",
    "has_changes",
    "unified_diff",
    # YAML
    "NetplanHandler",
    "YAMLChange",
    "YAMLEditResult",
    "YAMLHandler",
    "get_yaml_value",
    "load_yaml",
    "save_yaml",
    "set_yaml_value",
    "yaml_is_available",
]
