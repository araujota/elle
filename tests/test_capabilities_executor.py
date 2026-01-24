"""Tests for capabilities executor."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from elle.capabilities.executor import (
    CapabilityExecutor,
    get_executor,
    reset_executor,
)
from elle.capabilities.registry import CapabilityRegistry
from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability
from elle.capabilities.exceptions import (
    CapabilityCancelledError,
    CapabilityDeniedError,
    CapabilityNotFoundError,
)

from pydantic import BaseModel


# =============================================================================
# Test fixtures
# =============================================================================


class TestInput(BaseModel):
    value: str = "test"


class MockCapability(BaseCapability):
    """A mock capability for testing."""

    def __init__(self):
        self._run_called = False
        self._verify_called = False

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="mock.capability",
            summary="A mock capability for testing",
            domain="file",
            risk="low",
            requires_privilege=False,
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(
            would_execute=("mock command",),
            would_modify=("mock target",),
            estimated_risk="low",
            requires_confirmation=False,
            preview_text=f"Would run mock with value={input.value}",
            is_valid=True,
        )

    def run(self, input: TestInput) -> CapabilityResult:
        self._run_called = True
        return CapabilityResult(
            success=True,
            output={"value": input.value},
            evidence=CapabilityEvidence(
                commands_executed=("mock command",),
                verification_passed=False,
                rationale="Executed mock capability",
            ),
        )

    def verify(self, input: TestInput) -> VerificationResult:
        self._verify_called = True
        return VerificationResult(
            passed=True,
            checks_performed=("mock check",),
            actual_state={"value": input.value},
            expected_state={"value": input.value},
        )


class PrivilegedCapability(BaseCapability):
    """A capability requiring privileges."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="privileged.capability",
            summary="A privileged capability",
            domain="service",
            risk="high",
            requires_privilege=True,
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(
            would_execute=("privileged command",),
            estimated_risk="high",
            requires_confirmation=True,
            preview_text="Would run privileged operation",
            is_valid=True,
        )

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(success=True)


class FailingCapability(BaseCapability):
    """A capability that fails."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="failing.capability",
            summary="A failing capability",
            domain="file",
            risk="low",
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(is_valid=True)

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(
            success=False,
            error="Intentional failure",
        )


# =============================================================================
# Tests
# =============================================================================


class TestCapabilityExecutor:
    """Tests for CapabilityExecutor."""

    def setup_method(self):
        """Set up test fixtures."""
        self.registry = CapabilityRegistry()
        self.registry.register(MockCapability)
        self.registry.register(PrivilegedCapability)
        self.registry.register(FailingCapability)
        self.executor = CapabilityExecutor(registry=self.registry)

    @pytest.mark.asyncio
    async def test_execute_basic(self):
        """Test basic capability execution."""
        input_data = TestInput(value="hello")

        result = await self.executor.execute(
            "mock.capability",
            input_data,
            require_confirmation=False,
        )

        assert result.success is True
        assert result.output == {"value": "hello"}

    @pytest.mark.asyncio
    async def test_execute_with_verification(self):
        """Test execution includes verification."""
        input_data = TestInput()

        result = await self.executor.execute(
            "mock.capability",
            input_data,
            require_confirmation=False,
            skip_verification=False,
        )

        assert result.success is True
        assert result.evidence.verification_passed is True

    @pytest.mark.asyncio
    async def test_execute_skip_verification(self):
        """Test execution can skip verification."""
        input_data = TestInput()

        result = await self.executor.execute(
            "mock.capability",
            input_data,
            require_confirmation=False,
            skip_verification=True,
        )

        assert result.success is True
        # Verification not updated when skipped
        assert result.evidence.verification_passed is False

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        """Test execution fails for unknown capability."""
        with pytest.raises(CapabilityNotFoundError):
            await self.executor.execute(
                "nonexistent.capability",
                TestInput(),
            )

    @pytest.mark.asyncio
    async def test_execute_with_confirmation_callback(self):
        """Test execution with confirmation callback."""
        confirmed = False

        async def confirm_callback(dry_run: DryRunResult) -> bool:
            nonlocal confirmed
            confirmed = True
            return True

        result = await self.executor.execute(
            "privileged.capability",
            TestInput(),
            require_confirmation=True,
            confirm_callback=confirm_callback,
        )

        assert confirmed is True
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_cancelled_by_callback(self):
        """Test execution cancelled when callback returns False."""

        async def deny_callback(dry_run: DryRunResult) -> bool:
            return False

        with pytest.raises(CapabilityCancelledError):
            await self.executor.execute(
                "privileged.capability",
                TestInput(),
                require_confirmation=True,
                confirm_callback=deny_callback,
            )

    @pytest.mark.asyncio
    async def test_execute_failed_capability(self):
        """Test handling of failed capability execution."""
        result = await self.executor.execute(
            "failing.capability",
            TestInput(),
            require_confirmation=False,
        )

        assert result.success is False
        assert result.error == "Intentional failure"

    @pytest.mark.asyncio
    async def test_dry_run_only(self):
        """Test dry run without execution."""
        result = await self.executor.dry_run(
            "mock.capability",
            TestInput(value="test"),
        )

        assert result.is_valid is True
        assert "mock command" in result.would_execute
        assert "test" in result.preview_text

    @pytest.mark.asyncio
    async def test_verify_only(self):
        """Test verification without execution."""
        result = await self.executor.verify(
            "mock.capability",
            TestInput(),
        )

        assert result.passed is True

    @pytest.mark.asyncio
    async def test_policy_blocks_execution(self):
        """Test that policy can block execution."""
        # Mock policy to deny
        with patch("elle.capabilities.executor.evaluate_capability") as mock_eval:
            mock_result = MagicMock()
            mock_result.is_blocked = True
            mock_result.message = "Denied by policy"
            mock_eval.return_value = mock_result

            with pytest.raises(CapabilityDeniedError) as exc_info:
                await self.executor.execute(
                    "mock.capability",
                    TestInput(),
                )

            assert "Denied by policy" in str(exc_info.value)


class TestGlobalExecutor:
    """Tests for global executor functions."""

    def setup_method(self):
        """Reset global executor."""
        reset_executor()

    def teardown_method(self):
        """Reset global executor."""
        reset_executor()

    def test_get_executor_singleton(self):
        """Test that get_executor returns singleton."""
        exec1 = get_executor()
        exec2 = get_executor()
        assert exec1 is exec2

    def test_reset_executor(self):
        """Test that reset_executor creates new instance."""
        exec1 = get_executor()
        reset_executor()
        exec2 = get_executor()
        assert exec1 is not exec2


class TestInvalidDryRun:
    """Tests for handling invalid dry run results."""

    def setup_method(self):
        """Set up test fixtures."""

        class InvalidDryRunCapability(BaseCapability):
            @property
            def spec(self) -> CapabilitySpec:
                return CapabilitySpec(
                    name="invalid.dryrun",
                    summary="Returns invalid dry run",
                    domain="file",
                    risk="low",
                )

            def dry_run(self, input: TestInput) -> DryRunResult:
                return DryRunResult(
                    is_valid=False,
                    validation_errors=("Path does not exist",),
                    preview_text="Cannot proceed",
                )

            def run(self, input: TestInput) -> CapabilityResult:
                return CapabilityResult(success=True)

        self.registry = CapabilityRegistry()
        self.registry.register(InvalidDryRunCapability)
        self.executor = CapabilityExecutor(registry=self.registry)

    @pytest.mark.asyncio
    async def test_invalid_dry_run_stops_execution(self):
        """Test that invalid dry run prevents execution."""
        result = await self.executor.execute(
            "invalid.dryrun",
            TestInput(),
            require_confirmation=False,
        )

        # Execution should fail due to invalid dry run
        assert result.success is False
        assert "Path does not exist" in (result.error or "")
