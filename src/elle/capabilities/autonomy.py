"""Autonomy Configuration System for Capabilities.

Manages user preferences for autonomous capability execution:
- Base autonomy level (controls which risk levels can run without confirmation)
- Earned autonomy (capabilities earn autonomous execution based on success rates)
- Per-capability overrides (manual grants or revokes)

The autonomy engine integrates with the capability executor to determine
whether a capability requires user confirmation before execution.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import RiskLevel

logger = logging.getLogger(__name__)


# =============================================================================
# Autonomy Level Enum
# =============================================================================


class AutonomyLevel(str, Enum):
    """User-configurable autonomy thresholds.

    Determines which risk levels can execute autonomously (without confirmation).
    """

    READONLY = "readonly"  # No autonomous execution
    LOW_RISK = "low_risk"  # Only RiskLevel.none and .low
    MEDIUM_RISK = "medium_risk"  # Up to RiskLevel.medium
    HIGH_RISK = "high_risk"  # Up to RiskLevel.high
    FULL_AUTO = "full_auto"  # All capabilities (dangerous)

    @property
    def max_risk(self) -> RiskLevel | None:
        """Get the maximum risk level allowed for this autonomy level.

        Returns:
            The highest RiskLevel that can run autonomously, or None for readonly.
        """
        mapping: dict[AutonomyLevel, RiskLevel | None] = {
            AutonomyLevel.READONLY: None,
            AutonomyLevel.LOW_RISK: "low",
            AutonomyLevel.MEDIUM_RISK: "medium",
            AutonomyLevel.HIGH_RISK: "high",
            AutonomyLevel.FULL_AUTO: "critical",
        }
        return mapping[self]


# Risk level ordering for comparison
RISK_ORDERING: dict[RiskLevel, int] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def risk_allowed(cap_risk: RiskLevel, max_risk: RiskLevel | None) -> bool:
    """Check if a capability risk level is allowed under a max risk threshold.

    Args:
        cap_risk: The capability's risk level.
        max_risk: The maximum allowed risk level, or None to deny all.

    Returns:
        True if the capability is allowed to run autonomously.
    """
    if max_risk is None:
        return False
    return RISK_ORDERING.get(cap_risk, 0) <= RISK_ORDERING.get(max_risk, 0)


# =============================================================================
# Configuration Models
# =============================================================================


class EarnedAutonomyConfig(BaseModel):
    """Configuration for capability autonomy earning."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=False, description="Whether earned autonomy is enabled")
    min_executions: int = Field(
        default=10,
        ge=1,
        description="Minimum runs before earning autonomy",
    )
    min_success_rate: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Minimum success rate required (95% = 0.95)",
    )
    cooldown_after_failure: int = Field(
        default=5,
        ge=1,
        description="Successful runs needed after a failure to regain autonomy",
    )


class AutonomyPreferences(BaseModel):
    """User's autonomy preferences."""

    model_config = ConfigDict(frozen=True)

    base_level: AutonomyLevel = Field(
        default=AutonomyLevel.LOW_RISK,
        description="Base autonomy level",
    )
    earned_autonomy: EarnedAutonomyConfig = Field(
        default_factory=EarnedAutonomyConfig,
        description="Earned autonomy configuration",
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When preferences were last updated",
    )


class AutonomyOverride(BaseModel):
    """Per-capability autonomy override."""

    model_config = ConfigDict(frozen=True)

    capability_name: str = Field(description="Capability this override applies to")
    autonomous: bool = Field(description="Whether to grant or revoke autonomy")
    reason: Literal["manual", "earned"] = Field(
        default="manual",
        description="How this override was created",
    )
    earned_at: datetime | None = Field(
        default=None,
        description="When autonomy was earned (if reason=earned)",
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this override was last updated",
    )


class ExecutionStats(BaseModel):
    """Execution statistics for a capability."""

    model_config = ConfigDict(frozen=True)

    capability_name: str = Field(description="Capability name")
    total_executions: int = Field(default=0, description="Total number of executions")
    successful_executions: int = Field(default=0, description="Number of successful executions")
    failed_executions: int = Field(default=0, description="Number of failed executions")
    consecutive_successes: int = Field(
        default=0,
        description="Current streak of consecutive successes",
    )
    last_executed_at: datetime | None = Field(
        default=None,
        description="When this capability was last executed",
    )
    last_success_at: datetime | None = Field(
        default=None,
        description="When this capability last succeeded",
    )
    last_failure_at: datetime | None = Field(
        default=None,
        description="When this capability last failed",
    )

    @property
    def success_rate(self) -> float:
        """Calculate the success rate."""
        if self.total_executions == 0:
            return 0.0
        return self.successful_executions / self.total_executions


class AutonomyStatus(BaseModel):
    """Detailed autonomy status for display."""

    model_config = ConfigDict(frozen=True)

    capability_name: str = Field(description="Capability name")
    can_run_autonomously: bool = Field(description="Whether capability can run without confirmation")
    reason: str = Field(description="Explanation of autonomy status")
    source: Literal["base_level", "earned", "manual_grant", "manual_revoke", "risk_blocked"] = Field(
        description="Source of the autonomy decision",
    )
    stats: ExecutionStats | None = Field(
        default=None,
        description="Execution statistics if available",
    )
    earned_progress: float | None = Field(
        default=None,
        description="Progress toward earning autonomy (0.0-1.0)",
    )


# =============================================================================
# Autonomy Store
# =============================================================================


class AutonomyStore:
    """PostgreSQL storage for autonomy configuration.

    Stores:
    - User autonomy preferences (single row)
    - Per-capability overrides
    - Execution statistics
    """

    # Schema for autonomy tables
    SCHEMA = """
    -- Autonomy preferences (single row)
    CREATE TABLE IF NOT EXISTS autonomy_preferences (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        base_level TEXT NOT NULL DEFAULT 'low_risk',
        earned_autonomy_enabled INTEGER NOT NULL DEFAULT 0,
        min_executions INTEGER NOT NULL DEFAULT 10,
        min_success_rate REAL NOT NULL DEFAULT 0.95,
        cooldown_after_failure INTEGER NOT NULL DEFAULT 5,
        updated_at TEXT NOT NULL
    );

    -- Per-capability autonomy overrides
    CREATE TABLE IF NOT EXISTS capability_autonomy_overrides (
        capability_name TEXT PRIMARY KEY,
        autonomous INTEGER NOT NULL,
        reason TEXT NOT NULL DEFAULT 'manual',
        earned_at TEXT,
        updated_at TEXT NOT NULL
    );

    -- Execution statistics for earned autonomy
    CREATE TABLE IF NOT EXISTS capability_execution_stats (
        capability_name TEXT PRIMARY KEY,
        total_executions INTEGER NOT NULL DEFAULT 0,
        successful_executions INTEGER NOT NULL DEFAULT 0,
        failed_executions INTEGER NOT NULL DEFAULT 0,
        consecutive_successes INTEGER NOT NULL DEFAULT 0,
        last_executed_at TEXT,
        last_success_at TEXT,
        last_failure_at TEXT
    );
    """

    def __init__(self, schema: str = "autonomy") -> None:
        """Initialize the store.

        Args:
            schema: PostgreSQL schema name for autonomy tables.
        """
        self._schema = schema
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensure database schema exists."""
        from elle.storage.engine import get_conn

        try:
            with get_conn(schema=self._schema) as conn:
                for statement in self.SCHEMA.split(";"):
                    statement = statement.strip()
                    if statement:
                        conn.execute(statement)
        except Exception as e:
            logger.error(f"Failed to initialize autonomy store: {e}")

    # -------------------------------------------------------------------------
    # Preferences CRUD
    # -------------------------------------------------------------------------

    def get_preferences(self) -> AutonomyPreferences:
        """Get current autonomy preferences."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute("SELECT * FROM autonomy_preferences WHERE id = 1")
            row = cursor.fetchone()

            if not row:
                # Return defaults
                return AutonomyPreferences()

            return AutonomyPreferences(
                base_level=AutonomyLevel(row["base_level"]),
                earned_autonomy=EarnedAutonomyConfig(
                    enabled=bool(row["earned_autonomy_enabled"]),
                    min_executions=row["min_executions"],
                    min_success_rate=row["min_success_rate"],
                    cooldown_after_failure=row["cooldown_after_failure"],
                ),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def save_preferences(self, prefs: AutonomyPreferences) -> None:
        """Save autonomy preferences."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            conn.execute(
                """
                INSERT INTO autonomy_preferences
                (id, base_level, earned_autonomy_enabled, min_executions,
                 min_success_rate, cooldown_after_failure, updated_at)
                VALUES (1, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    base_level = EXCLUDED.base_level,
                    earned_autonomy_enabled = EXCLUDED.earned_autonomy_enabled,
                    min_executions = EXCLUDED.min_executions,
                    min_success_rate = EXCLUDED.min_success_rate,
                    cooldown_after_failure = EXCLUDED.cooldown_after_failure,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    prefs.base_level.value,
                    1 if prefs.earned_autonomy.enabled else 0,
                    prefs.earned_autonomy.min_executions,
                    prefs.earned_autonomy.min_success_rate,
                    prefs.earned_autonomy.cooldown_after_failure,
                    datetime.utcnow().isoformat(),
                ),
            )

    def set_base_level(self, level: AutonomyLevel) -> None:
        """Set the base autonomy level."""
        prefs = self.get_preferences()
        new_prefs = AutonomyPreferences(
            base_level=level,
            earned_autonomy=prefs.earned_autonomy,
        )
        self.save_preferences(new_prefs)

    def set_earned_autonomy_enabled(self, enabled: bool) -> None:
        """Enable or disable earned autonomy."""
        prefs = self.get_preferences()
        new_earned = EarnedAutonomyConfig(
            enabled=enabled,
            min_executions=prefs.earned_autonomy.min_executions,
            min_success_rate=prefs.earned_autonomy.min_success_rate,
            cooldown_after_failure=prefs.earned_autonomy.cooldown_after_failure,
        )
        new_prefs = AutonomyPreferences(
            base_level=prefs.base_level,
            earned_autonomy=new_earned,
        )
        self.save_preferences(new_prefs)

    # -------------------------------------------------------------------------
    # Override CRUD
    # -------------------------------------------------------------------------

    def get_override(self, capability_name: str) -> AutonomyOverride | None:
        """Get autonomy override for a capability."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute(
                "SELECT * FROM capability_autonomy_overrides WHERE capability_name = %s",
                (capability_name,),
            )
            row = cursor.fetchone()

            if not row:
                return None

            return AutonomyOverride(
                capability_name=row["capability_name"],
                autonomous=bool(row["autonomous"]),
                reason=row["reason"],
                earned_at=datetime.fromisoformat(row["earned_at"]) if row["earned_at"] else None,
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def set_override(
        self,
        capability_name: str,
        autonomous: bool,
        reason: Literal["manual", "earned"] = "manual",
    ) -> None:
        """Set autonomy override for a capability."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            now = datetime.utcnow().isoformat()
            earned_at = now if reason == "earned" and autonomous else None

            conn.execute(
                """
                INSERT INTO capability_autonomy_overrides
                (capability_name, autonomous, reason, earned_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (capability_name) DO UPDATE SET
                    autonomous = EXCLUDED.autonomous,
                    reason = EXCLUDED.reason,
                    earned_at = EXCLUDED.earned_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    capability_name,
                    1 if autonomous else 0,
                    reason,
                    earned_at,
                    now,
                ),
            )

    def remove_override(self, capability_name: str) -> bool:
        """Remove autonomy override for a capability.

        Returns:
            True if an override was removed, False if none existed.
        """
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute(
                "DELETE FROM capability_autonomy_overrides WHERE capability_name = %s",
                (capability_name,),
            )
            return bool(cursor.rowcount > 0)

    def list_overrides(self) -> list[AutonomyOverride]:
        """List all autonomy overrides."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute("SELECT * FROM capability_autonomy_overrides ORDER BY capability_name")
            return [
                AutonomyOverride(
                    capability_name=row["capability_name"],
                    autonomous=bool(row["autonomous"]),
                    reason=row["reason"],
                    earned_at=datetime.fromisoformat(row["earned_at"]) if row["earned_at"] else None,
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
                for row in cursor.fetchall()
            ]

    # -------------------------------------------------------------------------
    # Execution Stats CRUD
    # -------------------------------------------------------------------------

    def get_stats(self, capability_name: str) -> ExecutionStats:
        """Get execution statistics for a capability."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute(
                "SELECT * FROM capability_execution_stats WHERE capability_name = %s",
                (capability_name,),
            )
            row = cursor.fetchone()

            if not row:
                return ExecutionStats(capability_name=capability_name)

            return ExecutionStats(
                capability_name=row["capability_name"],
                total_executions=row["total_executions"],
                successful_executions=row["successful_executions"],
                failed_executions=row["failed_executions"],
                consecutive_successes=row["consecutive_successes"],
                last_executed_at=(
                    datetime.fromisoformat(row["last_executed_at"]) if row["last_executed_at"] else None
                ),
                last_success_at=(
                    datetime.fromisoformat(row["last_success_at"]) if row["last_success_at"] else None
                ),
                last_failure_at=(
                    datetime.fromisoformat(row["last_failure_at"]) if row["last_failure_at"] else None
                ),
            )

    def record_execution(self, capability_name: str, success: bool) -> ExecutionStats:
        """Record a capability execution and return updated stats."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            now = datetime.utcnow().isoformat()

            # Get current stats
            stats = self.get_stats(capability_name)

            # Calculate new values
            new_total = stats.total_executions + 1
            new_successful = stats.successful_executions + (1 if success else 0)
            new_failed = stats.failed_executions + (0 if success else 1)
            new_consecutive = (stats.consecutive_successes + 1) if success else 0

            # Update database
            conn.execute(
                """
                INSERT INTO capability_execution_stats
                (capability_name, total_executions, successful_executions, failed_executions,
                 consecutive_successes, last_executed_at, last_success_at, last_failure_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (capability_name) DO UPDATE SET
                    total_executions = EXCLUDED.total_executions,
                    successful_executions = EXCLUDED.successful_executions,
                    failed_executions = EXCLUDED.failed_executions,
                    consecutive_successes = EXCLUDED.consecutive_successes,
                    last_executed_at = EXCLUDED.last_executed_at,
                    last_success_at = EXCLUDED.last_success_at,
                    last_failure_at = EXCLUDED.last_failure_at
                """,
                (
                    capability_name,
                    new_total,
                    new_successful,
                    new_failed,
                    new_consecutive,
                    now,
                    now if success else (stats.last_success_at.isoformat() if stats.last_success_at else None),
                    now if not success else (stats.last_failure_at.isoformat() if stats.last_failure_at else None),
                ),
            )

            # Return updated stats
            return ExecutionStats(
                capability_name=capability_name,
                total_executions=new_total,
                successful_executions=new_successful,
                failed_executions=new_failed,
                consecutive_successes=new_consecutive,
                last_executed_at=datetime.fromisoformat(now),
                last_success_at=datetime.fromisoformat(now) if success else stats.last_success_at,
                last_failure_at=datetime.fromisoformat(now) if not success else stats.last_failure_at,
            )

    def get_all_stats(self) -> list[ExecutionStats]:
        """Get execution statistics for all capabilities."""
        from elle.storage.engine import get_conn

        with get_conn(schema=self._schema) as conn:
            cursor = conn.execute("SELECT * FROM capability_execution_stats ORDER BY capability_name")
            return [
                ExecutionStats(
                    capability_name=row["capability_name"],
                    total_executions=row["total_executions"],
                    successful_executions=row["successful_executions"],
                    failed_executions=row["failed_executions"],
                    consecutive_successes=row["consecutive_successes"],
                    last_executed_at=(
                        datetime.fromisoformat(row["last_executed_at"]) if row["last_executed_at"] else None
                    ),
                    last_success_at=(
                        datetime.fromisoformat(row["last_success_at"]) if row["last_success_at"] else None
                    ),
                    last_failure_at=(
                        datetime.fromisoformat(row["last_failure_at"]) if row["last_failure_at"] else None
                    ),
                )
                for row in cursor.fetchall()
            ]


# =============================================================================
# Autonomy Engine
# =============================================================================


class AutonomyEngine:
    """Determines whether a capability can run autonomously.

    Checks in order:
    1. Per-capability manual overrides
    2. Per-capability earned autonomy
    3. Base autonomy level vs capability risk
    """

    def __init__(self, store: AutonomyStore | None = None) -> None:
        """Initialize the engine.

        Args:
            store: Autonomy store. Creates new one if not provided.
        """
        self._store = store or AutonomyStore()

    @property
    def store(self) -> AutonomyStore:
        """Get the autonomy store."""
        return self._store

    def can_run_autonomously(
        self,
        capability_name: str,
        risk_level: RiskLevel,
    ) -> tuple[bool, str]:
        """Check if capability can run without confirmation.

        Args:
            capability_name: Name of the capability.
            risk_level: Risk level of the capability.

        Returns:
            Tuple of (can_run, reason_string).
        """
        prefs = self._store.get_preferences()

        # 1. Check for manual override
        override = self._store.get_override(capability_name)
        if override:
            if override.autonomous:
                if override.reason == "earned":
                    return True, f"Earned autonomous execution ({override.earned_at})"
                return True, "Manual grant"
            return False, "Manual revoke"

        # 2. Check earned autonomy (if enabled)
        if prefs.earned_autonomy.enabled:
            stats = self._store.get_stats(capability_name)
            if self._has_earned_autonomy(stats, prefs.earned_autonomy):
                return True, f"Earned: {stats.success_rate:.0%} success over {stats.total_executions} runs"

        # 3. Check base level vs risk
        max_risk = prefs.base_level.max_risk
        if risk_allowed(risk_level, max_risk):
            return True, f"Base level ({prefs.base_level.value}) allows risk {risk_level}"

        return False, f"Risk {risk_level} exceeds base level {prefs.base_level.value}"

    def get_autonomy_status(self, capability_name: str, risk_level: RiskLevel) -> AutonomyStatus:
        """Get detailed autonomy status for display.

        Args:
            capability_name: Name of the capability.
            risk_level: Risk level of the capability.

        Returns:
            AutonomyStatus with full details.
        """
        prefs = self._store.get_preferences()
        stats = self._store.get_stats(capability_name)
        override = self._store.get_override(capability_name)

        can_run, reason = self.can_run_autonomously(capability_name, risk_level)

        # Determine source
        if override:
            source: Literal["base_level", "earned", "manual_grant", "manual_revoke", "risk_blocked"]
            if override.autonomous:
                source = "manual_grant" if override.reason == "manual" else "earned"
            else:
                source = "manual_revoke"
        elif prefs.earned_autonomy.enabled and self._has_earned_autonomy(stats, prefs.earned_autonomy):
            source = "earned"
        elif can_run:
            source = "base_level"
        else:
            source = "risk_blocked"

        # Calculate earned progress
        earned_progress: float | None = None
        if prefs.earned_autonomy.enabled:
            if stats.total_executions > 0:
                # Progress is based on meeting both thresholds
                exec_progress = min(1.0, stats.total_executions / prefs.earned_autonomy.min_executions)
                rate_progress = min(1.0, stats.success_rate / prefs.earned_autonomy.min_success_rate)
                earned_progress = (exec_progress + rate_progress) / 2

        return AutonomyStatus(
            capability_name=capability_name,
            can_run_autonomously=can_run,
            reason=reason,
            source=source,
            stats=stats if stats.total_executions > 0 else None,
            earned_progress=earned_progress,
        )

    def update_after_execution(self, capability_name: str, success: bool, risk_level: RiskLevel) -> str | None:
        """Update earned autonomy tracking after execution.

        Args:
            capability_name: Name of the capability.
            success: Whether execution succeeded.
            risk_level: Risk level of the capability.

        Returns:
            Status change message if autonomy status changed, None otherwise.
        """
        prefs = self._store.get_preferences()
        if not prefs.earned_autonomy.enabled:
            # Still record stats but don't change autonomy
            self._store.record_execution(capability_name, success)
            return None

        # Get current override
        override = self._store.get_override(capability_name)
        was_autonomous = override.autonomous if override else False

        # Record execution and get updated stats
        stats = self._store.record_execution(capability_name, success)

        # Check if should earn autonomy
        if success and not was_autonomous:
            if self._has_earned_autonomy(stats, prefs.earned_autonomy):
                self._store.set_override(capability_name, True, reason="earned")
                return f"{capability_name} has earned autonomous execution!"

        # Check if should lose autonomy
        elif not success and was_autonomous:
            # Only revoke if it was earned (not manual)
            if override and override.reason == "earned":
                self._store.remove_override(capability_name)
                return f"{capability_name} autonomy revoked after failure"

        return None

    def _has_earned_autonomy(self, stats: ExecutionStats, config: EarnedAutonomyConfig) -> bool:
        """Check if stats meet earned autonomy criteria.

        Args:
            stats: Execution statistics.
            config: Earned autonomy configuration.

        Returns:
            True if criteria are met.
        """
        # Need minimum executions
        if stats.total_executions < config.min_executions:
            return False

        # Need minimum success rate
        if stats.success_rate < config.min_success_rate:
            return False

        # Need enough consecutive successes after any failure
        return not (
            stats.failed_executions > 0 and stats.consecutive_successes < config.cooldown_after_failure
        )


# =============================================================================
# Module-level instance
# =============================================================================

_engine: AutonomyEngine | None = None


def get_autonomy_engine() -> AutonomyEngine:
    """Get the shared autonomy engine instance."""
    global _engine
    if _engine is None:
        _engine = AutonomyEngine()
    return _engine


def reset_autonomy_engine() -> None:
    """Reset the autonomy engine (for testing)."""
    global _engine
    _engine = None
