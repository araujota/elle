"""Dependency Registry - centralized health checking with graceful degradation.

Provides a unified system for checking service dependencies (Ollama, daemon,
databases) and determining what features are available based on current state.

Key components:
- DependencyChecker: ABC for checking specific dependencies
- DependencyRegistry: Central registry for all dependency checkers
- SystemCapabilities: Current system state and available features
- DegradedMode: Enum for operational modes based on availability

Usage:
    registry = get_dependency_registry()

    # Check specific dependency
    result = await registry.check("ollama")
    if result.available:
        # Use LLM features
    else:
        print(result.fallback_message)

    # Check all at startup
    capabilities = await check_startup_dependencies()
    if not capabilities.llm_available:
        print("Running in degraded mode without LLM")
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class DependencyStatus(Enum):
    """Status of a dependency check."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class DegradedMode(Enum):
    """Operational mode based on available dependencies."""

    FULL = "full"  # All features available
    NO_LLM = "no_llm"  # LLM unavailable, rule-based only
    NO_DAEMON = "no_daemon"  # Daemon unavailable, no telemetry
    OFFLINE = "offline"  # All network services unavailable


# =============================================================================
# Models
# =============================================================================


class DependencyCheckResult(BaseModel):
    """Result from checking a dependency."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Name of the dependency")
    status: DependencyStatus = Field(description="Current status")
    available: bool = Field(description="Whether dependency is available")
    message: str = Field(default="", description="Status message")
    fallback_message: str = Field(default="", description="User-facing fallback guidance")
    latency_ms: float | None = Field(default=None, description="Check latency")
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_degraded(self) -> bool:
        """Check if dependency is in degraded state."""
        return self.status == DependencyStatus.DEGRADED


class SystemCapabilities(BaseModel):
    """Current system capabilities based on dependency availability."""

    model_config = ConfigDict(frozen=True)

    mode: DegradedMode = Field(default=DegradedMode.FULL)
    llm_available: bool = Field(default=True)
    daemon_available: bool = Field(default=True)
    incident_db_available: bool = Field(default=True)
    man_vault_db_available: bool = Field(default=True)

    messages: tuple[str, ...] = Field(default_factory=tuple, description="Status messages for user notification")

    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def can_classify_with_llm(self) -> bool:
        """Check if LLM is available for agent loop."""
        return self.llm_available

    @property
    def can_generate_plans(self) -> bool:
        """Check if LLM-based plan generation is available."""
        return self.llm_available

    @property
    def can_run_agentic_loop(self) -> bool:
        """Check if the agentic loop can run."""
        return self.llm_available

    @property
    def can_record_incidents(self) -> bool:
        """Check if incidents can be recorded."""
        return self.incident_db_available


# =============================================================================
# Dependency Checkers
# =============================================================================


class DependencyChecker(ABC):
    """Abstract base class for dependency checkers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the dependency being checked."""
        ...

    @abstractmethod
    async def check(self) -> DependencyCheckResult:
        """Perform the health check.

        Returns:
            DependencyCheckResult with status and details.
        """
        ...

    @property
    @abstractmethod
    def fallback_message(self) -> str:
        """User-facing message when dependency is unavailable."""
        ...


class OllamaChecker(DependencyChecker):
    """Check Ollama availability via /api/tags endpoint."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def fallback_message(self) -> str:
        return "Ollama is not running. LLM features are unavailable. Start Ollama with: ollama serve"

    async def check(self) -> DependencyCheckResult:
        start_time = time.time()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.host}/api/tags")
                latency_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    data = response.json()
                    models = data.get("models", [])
                    model_names = [m.get("name", "") for m in models]

                    return DependencyCheckResult(
                        name=self.name,
                        status=DependencyStatus.AVAILABLE,
                        available=True,
                        message=f"Ollama running with {len(models)} models",
                        fallback_message="",
                        latency_ms=latency_ms,
                        details={"models": model_names, "model_count": len(models)},
                    )
                else:
                    return DependencyCheckResult(
                        name=self.name,
                        status=DependencyStatus.UNAVAILABLE,
                        available=False,
                        message=f"Ollama returned status {response.status_code}",
                        fallback_message=self.fallback_message,
                        latency_ms=latency_ms,
                    )

        except httpx.ConnectError:
            return DependencyCheckResult(
                name=self.name,
                status=DependencyStatus.UNAVAILABLE,
                available=False,
                message="Cannot connect to Ollama",
                fallback_message=self.fallback_message,
                latency_ms=(time.time() - start_time) * 1000,
            )
        except httpx.TimeoutException:
            return DependencyCheckResult(
                name=self.name,
                status=DependencyStatus.DEGRADED,
                available=False,
                message="Ollama health check timed out",
                fallback_message=self.fallback_message,
                latency_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.warning(f"Ollama check failed: {e}")
            return DependencyCheckResult(
                name=self.name,
                status=DependencyStatus.UNAVAILABLE,
                available=False,
                message=str(e),
                fallback_message=self.fallback_message,
                latency_ms=(time.time() - start_time) * 1000,
            )


class DaemonChecker(DependencyChecker):
    """Check daemon availability via /health endpoint."""

    def __init__(
        self,
        host: str = "http://localhost:8642",
        timeout: float = 3.0,
    ) -> None:
        self.host = host
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "daemon"

    @property
    def fallback_message(self) -> str:
        return (
            "ELLE daemon is not running. Telemetry and background monitoring "
            "are unavailable. Start with: sudo systemctl start elled"
        )

    async def check(self) -> DependencyCheckResult:
        start_time = time.time()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.host}/health")
                latency_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    data = response.json()
                    return DependencyCheckResult(
                        name=self.name,
                        status=DependencyStatus.AVAILABLE,
                        available=True,
                        message="Daemon running",
                        fallback_message="",
                        latency_ms=latency_ms,
                        details=data,
                    )
                else:
                    return DependencyCheckResult(
                        name=self.name,
                        status=DependencyStatus.UNAVAILABLE,
                        available=False,
                        message=f"Daemon returned status {response.status_code}",
                        fallback_message=self.fallback_message,
                        latency_ms=latency_ms,
                    )

        except httpx.ConnectError:
            return DependencyCheckResult(
                name=self.name,
                status=DependencyStatus.UNAVAILABLE,
                available=False,
                message="Cannot connect to daemon",
                fallback_message=self.fallback_message,
                latency_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            logger.debug(f"Daemon check failed: {e}")
            return DependencyCheckResult(
                name=self.name,
                status=DependencyStatus.UNAVAILABLE,
                available=False,
                message=str(e),
                fallback_message=self.fallback_message,
                latency_ms=(time.time() - start_time) * 1000,
            )


class DatabaseChecker(DependencyChecker):
    """Check PostgreSQL database/schema accessibility."""

    def __init__(
        self,
        schema: str,
        db_name: str,
    ) -> None:
        self.schema = schema
        self.db_name = db_name

    @property
    def name(self) -> str:
        return f"database_{self.db_name}"

    @property
    def fallback_message(self) -> str:
        return f"Database schema '{self.db_name}' is not accessible."

    async def check(self) -> DependencyCheckResult:
        start_time = time.time()

        try:
            from elle.storage.engine import get_conn

            with get_conn(schema=self.schema) as conn:
                cursor = conn.execute("SELECT 1")
                cursor.fetchone()

                # Get table count for details
                cursor = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM information_schema.tables WHERE table_schema = %s",
                    (self.schema,),
                )
                row = cursor.fetchone()
                table_count = row["cnt"] if row else 0

                latency_ms = (time.time() - start_time) * 1000

                return DependencyCheckResult(
                    name=self.name,
                    status=DependencyStatus.AVAILABLE,
                    available=True,
                    message=f"Database accessible with {table_count} tables",
                    fallback_message="",
                    latency_ms=latency_ms,
                    details={"table_count": table_count, "schema": self.schema},
                )

        except Exception as e:
            logger.debug(f"Database check failed for {self.db_name}: {e}")
            return DependencyCheckResult(
                name=self.name,
                status=DependencyStatus.UNAVAILABLE,
                available=False,
                message=str(e),
                fallback_message=self.fallback_message,
                latency_ms=(time.time() - start_time) * 1000,
            )


# =============================================================================
# Dependency Registry
# =============================================================================


class DependencyRegistry:
    """Central registry for all dependency checkers.

    Maintains a cache of recent check results and provides
    unified interface for checking dependencies.

    Usage:
        registry = DependencyRegistry()
        registry.register(OllamaChecker())
        registry.register(DaemonChecker())

        # Check specific dependency
        result = await registry.check("ollama")

        # Check all dependencies
        results = await registry.check_all()

        # Require a dependency (raises if unavailable)
        if await registry.require("ollama"):
            # Use LLM
    """

    def __init__(self, cache_ttl_seconds: float = 30.0) -> None:
        """Initialize the registry.

        Args:
            cache_ttl_seconds: How long to cache check results.
        """
        self._checkers: dict[str, DependencyChecker] = {}
        self._cache: dict[str, tuple[datetime, DependencyCheckResult]] = {}
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)

    def register(self, checker: DependencyChecker) -> None:
        """Register a dependency checker.

        Args:
            checker: The checker to register.
        """
        self._checkers[checker.name] = checker

    def unregister(self, name: str) -> None:
        """Unregister a dependency checker.

        Args:
            name: Name of the checker to remove.
        """
        self._checkers.pop(name, None)
        self._cache.pop(name, None)

    async def check(
        self,
        name: str,
        force: bool = False,
    ) -> DependencyCheckResult:
        """Check a specific dependency.

        Args:
            name: Name of the dependency.
            force: Force a new check, ignoring cache.

        Returns:
            DependencyCheckResult with current status.
        """
        # Check cache
        if not force and name in self._cache:
            cached_time, cached_result = self._cache[name]
            if datetime.now(timezone.utc) - cached_time < self._cache_ttl:
                return cached_result

        # Get checker
        checker = self._checkers.get(name)
        if not checker:
            return DependencyCheckResult(
                name=name,
                status=DependencyStatus.UNAVAILABLE,
                available=False,
                message=f"No checker registered for '{name}'",
                fallback_message=f"Unknown dependency: {name}",
            )

        # Run check
        result = await checker.check()

        # Cache result
        self._cache[name] = (datetime.now(timezone.utc), result)

        return result

    async def check_all(self, force: bool = False) -> list[DependencyCheckResult]:
        """Check all registered dependencies in parallel.

        Args:
            force: Force new checks, ignoring cache.

        Returns:
            List of check results.
        """
        if not self._checkers:
            return []

        tasks = [self.check(name, force=force) for name in self._checkers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to failed results
        final_results: list[DependencyCheckResult] = []
        for name, result in zip(self._checkers.keys(), results, strict=True):
            if isinstance(result, BaseException):
                final_results.append(
                    DependencyCheckResult(
                        name=name,
                        status=DependencyStatus.UNAVAILABLE,
                        available=False,
                        message=str(result),
                        fallback_message=self._checkers[name].fallback_message,
                    )
                )
            elif isinstance(result, DependencyCheckResult):
                final_results.append(result)

        return final_results

    async def require(self, name: str, force: bool = False) -> bool:
        """Check if a dependency is available.

        This is a convenience method that returns True if available,
        False otherwise. Does not raise exceptions.

        Args:
            name: Name of the dependency.
            force: Force a new check.

        Returns:
            True if dependency is available.
        """
        result = await self.check(name, force=force)
        return result.available

    def get_fallback_message(self, name: str) -> str:
        """Get the fallback message for a dependency.

        Args:
            name: Name of the dependency.

        Returns:
            Fallback message string.
        """
        checker = self._checkers.get(name)
        if checker:
            return checker.fallback_message
        return f"Dependency '{name}' is not available."

    def clear_cache(self) -> None:
        """Clear all cached check results."""
        self._cache.clear()


# =============================================================================
# Startup Check Functions
# =============================================================================


async def check_startup_dependencies() -> SystemCapabilities:
    """Run startup dependency checks and return system capabilities.

    This should be called during ELLE initialization to determine
    what features are available.

    Returns:
        SystemCapabilities with current state.
    """
    registry = get_dependency_registry()

    # Run all checks in parallel
    results = await registry.check_all(force=True)

    # Extract states
    llm_available = True
    daemon_available = True
    incident_db_available = True
    man_vault_db_available = True
    messages: list[str] = []

    for result in results:
        if result.name == "ollama":
            llm_available = result.available
            if not result.available:
                messages.append(result.fallback_message)
        elif result.name == "daemon":
            daemon_available = result.available
            if not result.available:
                messages.append(result.fallback_message)
        elif result.name == "database_incidents":
            incident_db_available = result.available
            if not result.available:
                messages.append(result.fallback_message)
        elif result.name == "database_manvault":
            man_vault_db_available = result.available
            if not result.available:
                messages.append(result.fallback_message)

    # Determine mode
    if not llm_available and not daemon_available:
        mode = DegradedMode.OFFLINE
    elif not llm_available:
        mode = DegradedMode.NO_LLM
    elif not daemon_available:
        mode = DegradedMode.NO_DAEMON
    else:
        mode = DegradedMode.FULL

    return SystemCapabilities(
        mode=mode,
        llm_available=llm_available,
        daemon_available=daemon_available,
        incident_db_available=incident_db_available,
        man_vault_db_available=man_vault_db_available,
        messages=tuple(messages),
    )


# =============================================================================
# Module-level Singleton
# =============================================================================


_registry: DependencyRegistry | None = None


def get_dependency_registry() -> DependencyRegistry:
    """Get the shared dependency registry instance.

    Registers default checkers on first call.

    Returns:
        The DependencyRegistry singleton.
    """
    global _registry
    if _registry is None:
        _registry = DependencyRegistry()

        # Register default checkers
        _registry.register(OllamaChecker())
        _registry.register(DaemonChecker())
        _registry.register(
            DatabaseChecker(
                schema="incidents",
                db_name="incidents",
            )
        )
        _registry.register(
            DatabaseChecker(
                schema="manvault",
                db_name="manvault",
            )
        )

    return _registry


def reset_dependency_registry() -> None:
    """Reset the shared registry (for testing)."""
    global _registry
    _registry = None
