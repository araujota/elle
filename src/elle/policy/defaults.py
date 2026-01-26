"""Default policy rules migrated from hardcoded guards.

These rules replace the bespoke safety logic from subprocess_runner.py
and confgen/validator.py with declarative policy rules.
"""

from __future__ import annotations

from elle.policy.models import (
    Condition,
    MatchType,
    PolicyDocument,
    PolicyEffect,
    PolicyRule,
)

# =============================================================================
# Dangerous Command Patterns (from subprocess_runner.py)
# =============================================================================

# rm -rf / patterns
_RM_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(-[a-zA-Z]*r[a-zA-Z]*\s+)?/\s*$",
    r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$",
    r"rm\s+.*--no-preserve-root",
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(-[a-zA-Z]*r[a-zA-Z]*\s+)?/\*",
    r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\*",
    r"rm\s+.*\s+~\s*$",
    r"rm\s+.*\s+/home\s*$",
    r"rm\s+.*\s+/etc\s*$",
    r"rm\s+.*\s+/usr\s*$",
    r"rm\s+.*\s+/var\s*$",
    r"rm\s+.*\s+/boot\s*$",
]

# Fork bomb patterns
_FORK_BOMB_PATTERNS = [
    r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    r"\.\/\s*&\s*\.\/\s*&",
    r"fork\s*while.*fork",
    r"while\s*true.*do.*&.*done",
]

# Filesystem format commands
_FORMAT_PATTERNS = [
    r"^mkfs\b",
    r"^mkswap\b",
    r"^mke2fs\b",
    r"^mkdosfs\b",
    r"^mkntfs\b",
]

# dd with dangerous targets
_DD_PATTERNS = [
    r"dd\s+.*of=/dev/[a-z]+\b",
    r"dd\s+.*of=/dev/sd[a-z]",
    r"dd\s+.*of=/dev/nvme",
    r"dd\s+.*of=/dev/vd[a-z]",
]

# Recursive permission changes
_PERM_PATTERNS = [
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/\s*$",
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/etc",
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/usr",
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/var",
    r"chmod\s+.*\s+777\s+/",
    r"chown\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/\s*$",
    r"chown\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/etc",
    r"chown\s+(-[a-zA-Z]*R[a-zA-Z]*\s+).*\s+/usr",
]

# Pipe to shell
_PIPE_PATTERNS = [
    r"curl\s+.*\|\s*(ba)?sh",
    r"wget\s+.*\|\s*(ba)?sh",
    r"curl\s+.*\|\s*sudo",
    r"wget\s+.*\|\s*sudo",
    r"\|\s*bash\s+-c",
    r"\|\s*sh\s+-c",
]

# Shutdown commands
_SHUTDOWN_PATTERNS = [
    r"^shutdown\b",
    r"^reboot\b",
    r"^poweroff\b",
    r"^halt\b",
    r"^init\s+[0156]",
    r"^systemctl\s+(reboot|poweroff|halt)",
]

# History clearing
_HISTORY_PATTERNS = [
    r"history\s+-c",
    r">\s*~/\.bash_history",
    r"rm\s+.*\.bash_history",
    r"rm\s+.*\.zsh_history",
    r"export\s+HISTSIZE=0",
    r"unset\s+HISTFILE",
]


# =============================================================================
# Default Rules
# =============================================================================


def _create_deny_rules_from_patterns(
    rule_id: str,
    name: str,
    patterns: list[str],
    message: str,
) -> list[PolicyRule]:
    """Create deny rules from regex patterns.

    Args:
        rule_id: Base rule ID.
        name: Rule name.
        patterns: Regex patterns to match.
        message: Message to show when blocked.

    Returns:
        List of PolicyRule objects.
    """
    rules = []
    for i, pattern in enumerate(patterns):
        rules.append(
            PolicyRule(
                id=f"{rule_id}-{i}",
                name=name,
                conditions=(
                    Condition(
                        command=pattern,
                        match_type=MatchType.REGEX,
                    ),
                ),
                effect=PolicyEffect.DENY,
                message=message,
                priority=100,  # High priority for safety rules
            )
        )
    return rules


# Destructive rm rules
DENY_DESTRUCTIVE_RM_RULES = _create_deny_rules_from_patterns(
    rule_id="deny-destructive-rm",
    name="Deny destructive rm commands",
    patterns=_RM_PATTERNS,
    message="This rm command could destroy critical system files or your entire filesystem.",
)

# Fork bomb rules
DENY_FORK_BOMB_RULES = _create_deny_rules_from_patterns(
    rule_id="deny-fork-bomb",
    name="Deny fork bombs",
    patterns=_FORK_BOMB_PATTERNS,
    message="This appears to be a fork bomb that would crash your system.",
)

# Filesystem format rules
DENY_FILESYSTEM_FORMAT_RULES = _create_deny_rules_from_patterns(
    rule_id="deny-filesystem-format",
    name="Deny filesystem formatting",
    patterns=_FORMAT_PATTERNS,
    message="Filesystem formatting commands require explicit confirmation outside ELLE.",
)

# Raw disk write rules
DENY_RAW_DISK_WRITE_RULES = _create_deny_rules_from_patterns(
    rule_id="deny-raw-disk-write",
    name="Deny raw disk writes",
    patterns=_DD_PATTERNS,
    message="Writing directly to block devices can destroy data. Use specific tools instead.",
)

# Recursive permission rules
DENY_RECURSIVE_PERM_RULES = _create_deny_rules_from_patterns(
    rule_id="deny-recursive-perm",
    name="Deny dangerous permission changes",
    patterns=_PERM_PATTERNS,
    message="Recursive permission changes on system directories can break your system.",
)

# Pipe to shell rules
DENY_PIPE_TO_SHELL_RULES = _create_deny_rules_from_patterns(
    rule_id="deny-pipe-to-shell",
    name="Deny pipe to shell",
    patterns=_PIPE_PATTERNS,
    message="Piping remote content to a shell is a security risk. Download and inspect first.",
)

# Shutdown rules
DENY_SHUTDOWN_RULES = _create_deny_rules_from_patterns(
    rule_id="deny-shutdown",
    name="Deny shutdown/reboot commands",
    patterns=_SHUTDOWN_PATTERNS,
    message="System shutdown/reboot commands should be run directly, not through ELLE.",
)

# History clearing rules
DENY_HISTORY_CLEAR_RULES = _create_deny_rules_from_patterns(
    rule_id="deny-history-clear",
    name="Deny history clearing",
    patterns=_HISTORY_PATTERNS,
    message="Clearing shell history is often used to cover tracks after malicious activity.",
)

# Sudo denial rule (exact prefix match)
DENY_SUDO_RULE = PolicyRule(
    id="deny-sudo",
    name="Deny sudo commands",
    conditions=(
        Condition(
            command="sudo ",
            match_type=MatchType.PREFIX,
        ),
    ),
    effect=PolicyEffect.DENY,
    message=("ELLE does not execute commands with sudo. Privileged operations go through Polkit authorization."),
    priority=100,
)


# =============================================================================
# Forbidden Paths (from confgen/validator.py)
# =============================================================================

DENY_FORBIDDEN_PATHS_RULES = [
    PolicyRule(
        id="deny-forbidden-paths",
        name="Deny modification of critical system files",
        conditions=(
            Condition(
                path="/etc/passwd",
                match_type=MatchType.EXACT,
            ),
        ),
        effect=PolicyEffect.DENY,
        message="This file cannot be modified directly.",
        priority=100,
    ),
    PolicyRule(
        id="deny-forbidden-paths-shadow",
        name="Deny modification of shadow file",
        conditions=(
            Condition(
                path="/etc/shadow",
                match_type=MatchType.EXACT,
            ),
        ),
        effect=PolicyEffect.DENY,
        message="This file cannot be modified directly.",
        priority=100,
    ),
    PolicyRule(
        id="deny-forbidden-paths-group",
        name="Deny modification of group file",
        conditions=(
            Condition(
                path="/etc/group",
                match_type=MatchType.EXACT,
            ),
        ),
        effect=PolicyEffect.DENY,
        message="This file cannot be modified directly.",
        priority=100,
    ),
    PolicyRule(
        id="deny-forbidden-paths-gshadow",
        name="Deny modification of gshadow file",
        conditions=(
            Condition(
                path="/etc/gshadow",
                match_type=MatchType.EXACT,
            ),
        ),
        effect=PolicyEffect.DENY,
        message="This file cannot be modified directly.",
        priority=100,
    ),
    PolicyRule(
        id="deny-forbidden-paths-sudoers",
        name="Deny modification of sudoers file",
        conditions=(
            Condition(
                path="/etc/sudoers",
                match_type=MatchType.EXACT,
            ),
        ),
        effect=PolicyEffect.DENY,
        message="This file cannot be modified directly. Use visudo instead.",
        priority=100,
    ),
    PolicyRule(
        id="deny-forbidden-paths-grub",
        name="Deny modification of GRUB config",
        conditions=(
            Condition(
                path="/boot/grub/grub.cfg",
                match_type=MatchType.EXACT,
            ),
        ),
        effect=PolicyEffect.DENY,
        message="This file is auto-generated. Modify /etc/default/grub and run update-grub.",
        priority=100,
    ),
    PolicyRule(
        id="deny-forbidden-paths-efi",
        name="Deny modification of EFI partition",
        conditions=(
            Condition(
                path="/boot/efi/**",
                match_type=MatchType.GLOB,
            ),
        ),
        effect=PolicyEffect.DENY,
        message="Direct modification of EFI partition is not allowed.",
        priority=100,
    ),
]


# =============================================================================
# Preview Required Paths (from confgen/validator.py)
# =============================================================================

REQUIRE_PREVIEW_RULES = [
    PolicyRule(
        id="require-preview-fstab",
        name="Require preview for fstab changes",
        conditions=(
            Condition(
                path="/etc/fstab",
                match_type=MatchType.EXACT,
            ),
        ),
        effect=PolicyEffect.REQUIRE_PREVIEW,
        message="Changes to fstab affect system boot. Review carefully.",
        priority=50,
    ),
    PolicyRule(
        id="require-preview-netplan",
        name="Require preview for netplan changes",
        conditions=(
            Condition(
                path="/etc/netplan/**",
                match_type=MatchType.GLOB,
            ),
        ),
        effect=PolicyEffect.REQUIRE_PREVIEW,
        message="Network configuration changes may affect connectivity.",
        priority=50,
    ),
    PolicyRule(
        id="require-preview-sshd",
        name="Require preview for sshd changes",
        conditions=(
            Condition(
                path="/etc/ssh/sshd_config",
                match_type=MatchType.EXACT,
            ),
        ),
        effect=PolicyEffect.REQUIRE_PREVIEW,
        message="SSH configuration changes may affect remote access.",
        priority=50,
    ),
    PolicyRule(
        id="require-preview-network",
        name="Require preview for network changes",
        conditions=(
            Condition(
                path="/etc/network/**",
                match_type=MatchType.GLOB,
            ),
        ),
        effect=PolicyEffect.REQUIRE_PREVIEW,
        message="Network configuration changes may affect connectivity.",
        priority=50,
    ),
]


# =============================================================================
# All Default Rules
# =============================================================================

ALL_DEFAULT_RULES: tuple[PolicyRule, ...] = (
    DENY_SUDO_RULE,
    *DENY_DESTRUCTIVE_RM_RULES,
    *DENY_FORK_BOMB_RULES,
    *DENY_FILESYSTEM_FORMAT_RULES,
    *DENY_RAW_DISK_WRITE_RULES,
    *DENY_RECURSIVE_PERM_RULES,
    *DENY_PIPE_TO_SHELL_RULES,
    *DENY_SHUTDOWN_RULES,
    *DENY_HISTORY_CLEAR_RULES,
    *DENY_FORBIDDEN_PATHS_RULES,
    *REQUIRE_PREVIEW_RULES,
)


# =============================================================================
# Default Policy Document
# =============================================================================

DEFAULT_POLICY = PolicyDocument(
    version="1.0",
    name="ELLE System Defaults",
    extends=(),
    default_effect=PolicyEffect.ALLOW,
    rules=ALL_DEFAULT_RULES,
)


def get_default_policy() -> PolicyDocument:
    """Get the default system policy.

    Returns:
        PolicyDocument with all default safety rules.
    """
    return DEFAULT_POLICY


def get_default_rules() -> tuple[PolicyRule, ...]:
    """Get all default safety rules.

    Returns:
        Tuple of default PolicyRule objects.
    """
    return ALL_DEFAULT_RULES
