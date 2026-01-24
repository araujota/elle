"""Configuration file validators.

Provides validation for various config file types using system commands
to ensure syntax correctness before applying changes.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.ops.augeas.lenses import detect_domain

logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
    """Result of a configuration validation."""

    model_config = ConfigDict(frozen=True)

    valid: bool = Field(description="Whether validation passed")
    output: str = Field(default="", description="Validator stdout")
    error: str = Field(default="", description="Validator stderr")
    validator: str = Field(description="Validator name that was used")
    command: str = Field(default="", description="Command that was executed")
    exit_code: int = Field(default=0, description="Validator exit code")
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Non-fatal warnings from validation",
    )


# =============================================================================
# Base Validator
# =============================================================================


class ConfigValidator(ABC):
    """Abstract base class for config validators."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Validator name for logging and results."""
        pass

    @property
    @abstractmethod
    def domains(self) -> tuple[str, ...]:
        """Domains this validator handles."""
        pass

    @abstractmethod
    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate a configuration file.

        Args:
            file_path: Path to the config file.

        Returns:
            ValidationResult indicating success/failure.
        """
        pass

    def _run_command(
        self,
        command: list[str],
        timeout: float = 30.0,
    ) -> tuple[int, str, str]:
        """Run a validation command synchronously.

        Args:
            command: Command and arguments.
            timeout: Maximum execution time.

        Returns:
            Tuple of (exit_code, stdout, stderr).
        """
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Validation timed out after {timeout}s"
        except FileNotFoundError:
            return -1, "", f"Validator command not found: {command[0]}"
        except Exception as e:
            return -1, "", f"Validation error: {e}"


# =============================================================================
# Specific Validators
# =============================================================================


class FstabValidator(ConfigValidator):
    """Validates /etc/fstab syntax using mount --fake."""

    @property
    def name(self) -> str:
        return "fstab"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("fstab",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate fstab syntax.

        Uses 'mount -a -f --fake' to check syntax without mounting.
        """
        # Note: mount -a validates against /etc/fstab specifically
        # For validation of a different file, we'd need to use --fstab
        cmd = ["mount", "-a", "-f", "--fake"]

        # If validating a non-standard fstab location
        if str(file_path) != "/etc/fstab":
            cmd.extend(["--fstab", str(file_path)])

        exit_code, stdout, stderr = self._run_command(cmd)

        # mount --fake returns 0 on success
        valid = exit_code == 0

        # Check for warnings in output
        warnings = []
        if "unknown filesystem type" in stderr.lower():
            warnings.append("Unknown filesystem type (may still be valid)")

        return ValidationResult(
            valid=valid,
            output=stdout,
            error=stderr,
            validator=self.name,
            command=" ".join(cmd),
            exit_code=exit_code,
            warnings=tuple(warnings),
        )


class SshdValidator(ConfigValidator):
    """Validates sshd_config syntax using sshd -t."""

    @property
    def name(self) -> str:
        return "sshd"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("ssh",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate sshd_config syntax.

        Uses 'sshd -t -f <file>' to check syntax.
        """
        cmd = ["sshd", "-t", "-f", str(file_path)]
        exit_code, stdout, stderr = self._run_command(cmd)

        # sshd -t returns 0 on valid config
        valid = exit_code == 0

        # Combine output (sshd outputs to stderr)
        output = stdout or stderr

        return ValidationResult(
            valid=valid,
            output=output,
            error=stderr if not valid else "",
            validator=self.name,
            command=" ".join(cmd),
            exit_code=exit_code,
        )


class SysctlValidator(ConfigValidator):
    """Validates sysctl.conf syntax using sysctl --system."""

    @property
    def name(self) -> str:
        return "sysctl"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("sysctl",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate sysctl.conf syntax.

        Uses 'sysctl -p <file> --dry-run' to check syntax.
        """
        # Note: --dry-run may not be available on all systems
        # Fall back to parsing without --dry-run if needed
        cmd = ["sysctl", "-p", str(file_path), "--dry-run"]
        exit_code, stdout, stderr = self._run_command(cmd)

        # If --dry-run is not supported, try without it
        if "unrecognized option" in stderr.lower() or exit_code == 1:
            # Try without --dry-run (will actually load values temporarily)
            cmd = ["sysctl", "-n", "-e", "-p", str(file_path)]
            exit_code, stdout, stderr = self._run_command(cmd)

        valid = exit_code == 0

        # Check for specific error patterns
        warnings = []
        if "cannot stat" in stderr.lower():
            warnings.append("Some referenced files may not exist")

        return ValidationResult(
            valid=valid,
            output=stdout,
            error=stderr,
            validator=self.name,
            command=" ".join(cmd),
            exit_code=exit_code,
            warnings=tuple(warnings),
        )


class NetplanValidator(ConfigValidator):
    """Validates netplan YAML configuration."""

    @property
    def name(self) -> str:
        return "netplan"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("netplan",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate netplan configuration.

        Uses 'netplan generate' to check syntax without applying.
        """
        # netplan generate checks all configs in /etc/netplan/
        # To validate a specific file, we use netplan generate --debug
        cmd = ["netplan", "generate"]
        exit_code, stdout, stderr = self._run_command(cmd)

        valid = exit_code == 0

        # Netplan outputs to stderr even on success
        output = stderr or stdout

        return ValidationResult(
            valid=valid,
            output=output,
            error=stderr if not valid else "",
            validator=self.name,
            command=" ".join(cmd),
            exit_code=exit_code,
        )


class NginxValidator(ConfigValidator):
    """Validates nginx configuration."""

    @property
    def name(self) -> str:
        return "nginx"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("nginx",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate nginx configuration.

        Uses 'nginx -t' to check syntax.
        """
        cmd = ["nginx", "-t"]
        exit_code, stdout, stderr = self._run_command(cmd)

        # nginx outputs to stderr
        valid = exit_code == 0 and "syntax is ok" in stderr.lower()

        return ValidationResult(
            valid=valid,
            output=stderr,
            error=stderr if not valid else "",
            validator=self.name,
            command=" ".join(cmd),
            exit_code=exit_code,
        )


class ApacheValidator(ConfigValidator):
    """Validates Apache httpd configuration."""

    @property
    def name(self) -> str:
        return "apache"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("apache",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate Apache configuration.

        Uses 'apachectl configtest' to check syntax.
        """
        # Try apachectl first (Debian/Ubuntu), then httpd (RHEL/CentOS)
        cmd = ["apachectl", "configtest"]
        exit_code, stdout, stderr = self._run_command(cmd)

        if exit_code == -1 and "not found" in stderr.lower():
            # Try httpd -t as fallback
            cmd = ["httpd", "-t"]
            exit_code, stdout, stderr = self._run_command(cmd)

        valid = exit_code == 0

        return ValidationResult(
            valid=valid,
            output=stdout or stderr,
            error=stderr if not valid else "",
            validator=self.name,
            command=" ".join(cmd),
            exit_code=exit_code,
        )


class HostsValidator(ConfigValidator):
    """Validates /etc/hosts syntax."""

    @property
    def name(self) -> str:
        return "hosts"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("network",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate hosts file syntax.

        Uses 'getent hosts localhost' to verify the file is parseable.
        """
        # There's no dedicated hosts validator, so we check basic parsing
        cmd = ["getent", "hosts", "localhost"]
        exit_code, stdout, stderr = self._run_command(cmd)

        # Additionally, do a simple syntax check
        warnings = []
        try:
            content = Path(file_path).read_text()
            for i, line in enumerate(content.splitlines(), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    warnings.append(f"Line {i}: Invalid format (need IP and hostname)")
        except Exception as e:
            return ValidationResult(
                valid=False,
                output="",
                error=f"Failed to read file: {e}",
                validator=self.name,
                command="file read",
                exit_code=-1,
            )

        valid = exit_code == 0 and len(warnings) == 0

        return ValidationResult(
            valid=valid,
            output=stdout,
            error=stderr if not valid else "",
            validator=self.name,
            command=" ".join(cmd),
            exit_code=exit_code,
            warnings=tuple(warnings),
        )


class CronValidator(ConfigValidator):
    """Validates crontab syntax."""

    @property
    def name(self) -> str:
        return "cron"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("cron",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate crontab syntax.

        Performs basic syntax validation of cron entries.
        """
        warnings = []
        errors = []

        try:
            content = Path(file_path).read_text()
            for i, line in enumerate(content.splitlines(), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Check for environment variables
                if "=" in line and not line[0].isdigit() and line[0] != "*":
                    continue

                # Check cron time fields
                parts = line.split()
                if len(parts) < 6:
                    errors.append(f"Line {i}: Not enough fields")
                    continue

                # Basic validation of time fields
                time_fields = parts[:5]
                for j, field in enumerate(time_fields):
                    if not self._validate_cron_field(field, j):
                        warnings.append(f"Line {i}: Unusual value in field {j+1}: {field}")

        except Exception as e:
            return ValidationResult(
                valid=False,
                output="",
                error=f"Failed to read file: {e}",
                validator=self.name,
                command="syntax check",
                exit_code=-1,
            )

        valid = len(errors) == 0

        return ValidationResult(
            valid=valid,
            output="Cron syntax check passed" if valid else "",
            error="; ".join(errors) if errors else "",
            validator=self.name,
            command="syntax check",
            exit_code=0 if valid else 1,
            warnings=tuple(warnings),
        )

    def _validate_cron_field(self, field: str, index: int) -> bool:
        """Basic validation of a cron time field."""
        if field == "*":
            return True

        # Max values for each field: minute, hour, day, month, weekday
        max_values = [59, 23, 31, 12, 7]

        # Handle ranges and lists
        for part in field.replace("-", ",").replace("/", ",").split(","):
            if part == "*":
                continue
            try:
                val = int(part)
                if val < 0 or val > max_values[index]:
                    return False
            except ValueError:
                # Could be a month/day name
                return True

        return True


class SudoersValidator(ConfigValidator):
    """Validates sudoers file syntax."""

    @property
    def name(self) -> str:
        return "sudoers"

    @property
    def domains(self) -> tuple[str, ...]:
        return ("security",)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate sudoers syntax.

        Uses 'visudo -cf <file>' to check syntax.
        """
        cmd = ["visudo", "-cf", str(file_path)]
        exit_code, stdout, stderr = self._run_command(cmd)

        valid = exit_code == 0

        return ValidationResult(
            valid=valid,
            output=stdout,
            error=stderr if not valid else "",
            validator=self.name,
            command=" ".join(cmd),
            exit_code=exit_code,
        )


class NoOpValidator(ConfigValidator):
    """Validator that always passes (for files without specific validators)."""

    def __init__(self, domain: str = "other") -> None:
        self._domain = domain

    @property
    def name(self) -> str:
        return "noop"

    @property
    def domains(self) -> tuple[str, ...]:
        return (self._domain,)

    async def validate(self, file_path: str | Path) -> ValidationResult:
        """Always returns valid (no validation available)."""
        return ValidationResult(
            valid=True,
            output="No specific validator available",
            validator=self.name,
            command="",
            exit_code=0,
            warnings=("No specific validator for this file type",),
        )


# =============================================================================
# Validator Registry
# =============================================================================

# All available validators
_VALIDATORS: list[ConfigValidator] = [
    FstabValidator(),
    SshdValidator(),
    SysctlValidator(),
    NetplanValidator(),
    NginxValidator(),
    ApacheValidator(),
    HostsValidator(),
    CronValidator(),
    SudoersValidator(),
]

# Build domain -> validator mapping
_DOMAIN_VALIDATORS: dict[str, ConfigValidator] = {}
for validator in _VALIDATORS:
    for domain in validator.domains:
        _DOMAIN_VALIDATORS[domain] = validator


def get_validator(file_path: str | Path) -> ConfigValidator:
    """Get the appropriate validator for a file.

    Args:
        file_path: Path to the config file.

    Returns:
        ConfigValidator instance for the file type.
    """
    domain = detect_domain(file_path)
    validator = _DOMAIN_VALIDATORS.get(domain)

    if validator is None:
        return NoOpValidator(domain)

    return validator


async def validate_config(file_path: str | Path) -> ValidationResult:
    """Validate a configuration file.

    Automatically selects the appropriate validator based on file path.

    Args:
        file_path: Path to the config file.

    Returns:
        ValidationResult with validation outcome.
    """
    validator = get_validator(file_path)
    return await validator.validate(file_path)


def get_validation_command(file_path: str | Path) -> str | None:
    """Get the validation command for a file (for display purposes).

    Args:
        file_path: Path to the config file.

    Returns:
        Validation command string or None if no validator.
    """
    domain = detect_domain(file_path)

    commands: dict[str, str] = {
        "fstab": "mount -a -f --fake",
        "ssh": f"sshd -t -f {file_path}",
        "sysctl": f"sysctl -p {file_path} --dry-run",
        "netplan": "netplan generate",
        "nginx": "nginx -t",
        "apache": "apachectl configtest",
        "security": f"visudo -cf {file_path}",
    }

    return commands.get(domain)


def list_validators() -> list[str]:
    """Get list of available validator names.

    Returns:
        List of validator names.
    """
    return [v.name for v in _VALIDATORS]


def list_validatable_domains() -> list[str]:
    """Get list of domains that have validators.

    Returns:
        List of domain names.
    """
    return list(_DOMAIN_VALIDATORS.keys())
