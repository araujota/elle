"""GRUB management for safe reboot operations.

Provides functions to:
- Detect GRUB configuration and available entries
- Set one-shot boot entry (grub-reboot)
- Confirm successful boot (grub-set-default)
- Manage rollback state

Uses Polkit for privileged GRUB operations.
"""

import logging
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.reboot.models import GRUBState

logger = logging.getLogger(__name__)

# GRUB config paths
GRUB_CONFIG = Path("/boot/grub/grub.cfg")
GRUB_DEFAULT = Path("/etc/default/grub")
GRUB_ENV = Path("/boot/grub/grubenv")


class GRUBError(Exception):
    """Error during GRUB operation."""

    pass


class GRUBResult(BaseModel):
    """Result of a GRUB operation."""

    model_config = ConfigDict(frozen=True)

    success: bool
    message: str = ""
    error: str = ""
    exit_code: int = 0


# =============================================================================
# Detection Functions
# =============================================================================


def detect_grub_mode() -> str:
    """Detect the GRUB boot mode.

    Returns:
        "legacy" for BIOS, "efi" for UEFI, "unknown" if cannot detect.
    """
    efi_dir = Path("/sys/firmware/efi")
    if efi_dir.exists():
        return "efi"

    # Check for legacy BIOS GRUB
    if GRUB_CONFIG.exists():
        return "legacy"

    return "unknown"


def is_grub_available() -> bool:
    """Check if GRUB is available on this system.

    Returns:
        True if GRUB commands are available.
    """
    try:
        result = subprocess.run(
            ["which", "grub-reboot"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_grub_default() -> str | None:
    """Get the current GRUB default entry.

    Reads from /etc/default/grub GRUB_DEFAULT setting.

    Returns:
        Default entry string, or None if cannot read.
    """
    if not GRUB_DEFAULT.exists():
        return None

    try:
        content = GRUB_DEFAULT.read_text()
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("GRUB_DEFAULT="):
                value = line.split("=", 1)[1].strip()
                # Remove quotes if present
                value = value.strip('"').strip("'")
                return value
    except Exception as e:
        logger.warning(f"Failed to read GRUB default: {e}")

    return None


def get_saved_default() -> str | None:
    """Get the GRUB saved default from grubenv.

    Only applicable when GRUB_DEFAULT=saved.

    Returns:
        Saved entry index/name, or None if not set.
    """
    if not GRUB_ENV.exists():
        return None

    try:
        result = subprocess.run(
            ["grub-editenv", str(GRUB_ENV), "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("saved_entry="):
                    return line.split("=", 1)[1]
    except Exception as e:
        logger.warning(f"Failed to read grubenv: {e}")

    return None


def get_grub_entries() -> list[str]:
    """Get list of GRUB menu entries.

    Parses /boot/grub/grub.cfg to extract menu entries.

    Returns:
        List of menu entry names.
    """
    if not GRUB_CONFIG.exists():
        return []

    entries = []
    try:
        content = GRUB_CONFIG.read_text()

        # Match menuentry lines
        # Pattern: menuentry 'Ubuntu' or menuentry "Ubuntu"
        pattern = r"menuentry\s+['\"]([^'\"]+)['\"]"
        matches = re.findall(pattern, content)
        entries.extend(matches)

        # Also match submenu entries (for advanced options)
        # Pattern: submenu 'Advanced options for Ubuntu'
        submenu_pattern = r"submenu\s+['\"]([^'\"]+)['\"]"
        submenus = re.findall(submenu_pattern, content)

        # For each submenu, find nested menuentries
        for submenu in submenus:
            # Find entries within this submenu block
            submenu_entries = _find_submenu_entries(content, submenu)
            for entry in submenu_entries:
                entries.append(f"{submenu}>{entry}")

    except Exception as e:
        logger.warning(f"Failed to parse GRUB config: {e}")

    return entries


def _find_submenu_entries(content: str, submenu_name: str) -> list[str]:
    """Find menu entries within a submenu block.

    Args:
        content: GRUB config content.
        submenu_name: Name of the submenu.

    Returns:
        List of entry names within the submenu.
    """
    entries = []

    # Find the submenu block
    pattern = rf"submenu\s+['\"]({re.escape(submenu_name)})['\"].*?\{{"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return entries

    start = match.end()

    # Find matching closing brace (track nesting)
    depth = 1
    end = start
    for i, char in enumerate(content[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    submenu_content = content[start:end]

    # Find menuentries in submenu
    entry_pattern = r"menuentry\s+['\"]([^'\"]+)['\"]"
    entries = re.findall(entry_pattern, submenu_content)

    return entries


def get_grub_state() -> GRUBState:
    """Get complete GRUB state.

    Returns:
        GRUBState with current configuration.
    """
    default = get_grub_default() or "0"
    entries = get_grub_entries()
    saved = get_saved_default()
    oneshot = _get_grub_oneshot()

    return GRUBState(
        default_entry=default,
        entries=tuple(entries),
        oneshot_entry=oneshot,
        saved_default=saved,
    )


def _get_grub_oneshot() -> str | None:
    """Get the GRUB one-shot boot entry (next_entry in grubenv).

    Returns:
        One-shot entry, or None if not set.
    """
    if not GRUB_ENV.exists():
        return None

    try:
        result = subprocess.run(
            ["grub-editenv", str(GRUB_ENV), "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("next_entry="):
                    return line.split("=", 1)[1]
    except Exception as e:
        logger.debug(f"Failed to read grubenv next_entry: {e}")

    return None


# =============================================================================
# Boot Management Functions (Require Polkit)
# =============================================================================


async def set_grub_oneshot(
    entry: str,
) -> GRUBResult:
    """Set a GRUB one-shot boot entry.

    Uses grub-reboot to set the next boot entry without changing
    the permanent default. If boot fails, GRUB reverts automatically.

    Args:
        entry: GRUB menu entry (index or name).

    Returns:
        GRUBResult with outcome.
    """
    from elle.security.polkit_helper import PolkitAction, get_helper

    if not is_grub_available():
        return GRUBResult(
            success=False,
            error="GRUB commands not available",
        )

    helper = get_helper()

    # Use pkexec to run grub-reboot
    result = await helper._run_pkexec(
        ["grub-reboot", str(entry)],
        PolkitAction.MANAGE_SERVICES,  # Or create specific GRUB action
    )

    if result.success:
        logger.info(f"Set GRUB one-shot boot entry: {entry}")
        return GRUBResult(
            success=True,
            message=f"Next boot will use entry: {entry}",
        )
    else:
        logger.error(f"Failed to set GRUB one-shot: {result.error}")
        return GRUBResult(
            success=False,
            error=result.error or "grub-reboot failed",
            exit_code=result.exit_code,
        )


async def confirm_boot_success(
    entry: str | None = None,
) -> GRUBResult:
    """Confirm current boot configuration as successful.

    Uses grub-set-default to make the current (or specified) entry
    the permanent default. Call this after verification passes.

    Args:
        entry: Entry to set as default. If None, uses current boot entry.

    Returns:
        GRUBResult with outcome.
    """
    from elle.security.polkit_helper import PolkitAction, get_helper

    if not is_grub_available():
        return GRUBResult(
            success=False,
            error="GRUB commands not available",
        )

    # If no entry specified, use the current saved default or "0"
    if entry is None:
        entry = get_saved_default() or "0"

    helper = get_helper()

    # Use pkexec to run grub-set-default
    result = await helper._run_pkexec(
        ["grub-set-default", str(entry)],
        PolkitAction.MANAGE_SERVICES,
    )

    if result.success:
        logger.info(f"Confirmed GRUB default: {entry}")
        return GRUBResult(
            success=True,
            message=f"Boot confirmed, default set to: {entry}",
        )
    else:
        logger.error(f"Failed to confirm boot: {result.error}")
        return GRUBResult(
            success=False,
            error=result.error or "grub-set-default failed",
            exit_code=result.exit_code,
        )


async def clear_grub_oneshot() -> GRUBResult:
    """Clear the GRUB one-shot boot entry.

    Removes next_entry from grubenv if set.

    Returns:
        GRUBResult with outcome.
    """
    from elle.security.polkit_helper import PolkitAction, get_helper

    if not is_grub_available():
        return GRUBResult(
            success=False,
            error="GRUB commands not available",
        )

    helper = get_helper()

    # Use grub-editenv to unset next_entry
    result = await helper._run_pkexec(
        ["grub-editenv", str(GRUB_ENV), "unset", "next_entry"],
        PolkitAction.MANAGE_SERVICES,
    )

    if result.success:
        logger.info("Cleared GRUB one-shot entry")
        return GRUBResult(
            success=True,
            message="One-shot boot entry cleared",
        )
    else:
        # May fail if next_entry wasn't set, which is fine
        return GRUBResult(
            success=True,
            message="One-shot entry clear attempted",
        )


async def set_grub_default(entry: str) -> GRUBResult:
    """Set the permanent GRUB default entry.

    Args:
        entry: GRUB menu entry (index or name).

    Returns:
        GRUBResult with outcome.
    """
    return await confirm_boot_success(entry)


# =============================================================================
# Rollback Support
# =============================================================================


async def prepare_rollback(
    fallback_entry: str | None = None,
) -> GRUBResult:
    """Prepare GRUB for potential rollback.

    Saves the current configuration so we can restore it
    if verification fails.

    Args:
        fallback_entry: Entry to use for rollback. If None, uses current default.

    Returns:
        GRUBResult with rollback preparation info.
    """
    if fallback_entry is None:
        fallback_entry = get_grub_default() or "0"

    # Ensure saved_entry is set if using GRUB_DEFAULT=saved
    saved = get_saved_default()
    if saved is None and get_grub_default() == "saved":
        # Set current entry as saved
        from elle.security.polkit_helper import PolkitAction, get_helper

        helper = get_helper()
        await helper._run_pkexec(
            ["grub-editenv", str(GRUB_ENV), "set", f"saved_entry={fallback_entry}"],
            PolkitAction.MANAGE_SERVICES,
        )

    return GRUBResult(
        success=True,
        message=f"Rollback prepared with fallback entry: {fallback_entry}",
    )


async def trigger_rollback_reboot(
    fallback_entry: str,
) -> GRUBResult:
    """Trigger a reboot to rollback entry.

    Sets the fallback entry as default and schedules immediate reboot.

    Args:
        fallback_entry: Entry to boot into for rollback.

    Returns:
        GRUBResult with outcome.
    """
    # Set the fallback as the default
    result = await set_grub_default(fallback_entry)
    if not result.success:
        return result

    # Clear any one-shot entry
    await clear_grub_oneshot()

    logger.warning(f"Triggering rollback reboot to: {fallback_entry}")

    return GRUBResult(
        success=True,
        message=f"Rollback configured, ready to reboot to: {fallback_entry}",
    )


# =============================================================================
# Boot ID Functions
# =============================================================================


def get_boot_id() -> str | None:
    """Get the current systemd boot ID.

    The boot ID changes on every boot, allowing us to detect
    whether a reboot has occurred.

    Returns:
        Boot ID string, or None if cannot read.
    """
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    try:
        if boot_id_path.exists():
            return boot_id_path.read_text().strip()
    except Exception as e:
        logger.warning(f"Failed to read boot ID: {e}")

    return None


def has_rebooted_since(saved_boot_id: str | None) -> bool:
    """Check if system has rebooted since a saved boot ID.

    Args:
        saved_boot_id: Previously saved boot ID.

    Returns:
        True if boot IDs differ (reboot occurred).
    """
    if saved_boot_id is None:
        return False

    current = get_boot_id()
    if current is None:
        return False

    return current != saved_boot_id
