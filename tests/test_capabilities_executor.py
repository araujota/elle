"""Tests for capabilities executor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from elle.capabilities.exceptions import (
    CapabilityCancelledError,
    CapabilityDeniedError,
    CapabilityExecutionError,
    CapabilityNotFoundError,
)
from elle.capabilities.executor import (
    CapabilityExecutor,
    get_executor,
    reset_executor,
)
from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RollbackResult,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability
from elle.capabilities.registry import CapabilityRegistry


def _make_mock_autonomy_engine():
    """Create a mock autonomy engine that doesn't touch the filesystem."""
    engine = MagicMock()
    engine.can_run_autonomously.return_value = (False, "Test mode: requires confirmation")
    engine.update_after_execution.return_value = None
    return engine


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
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

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

    @patch("elle.capabilities.executor.CapabilityExecutor.__init__", return_value=None)
    def test_get_executor_singleton(self, mock_init):
        """Test that get_executor returns singleton."""
        exec1 = get_executor()
        exec2 = get_executor()
        assert exec1 is exec2

    @patch("elle.capabilities.executor.CapabilityExecutor.__init__", return_value=None)
    def test_reset_executor(self, mock_init):
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
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

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


# =============================================================================
# Additional coverage tests
# =============================================================================


class DryRunExceptionCapability(BaseCapability):
    """A capability whose dry_run raises an exception."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="dryrun.exception",
            summary="Dry run raises",
            domain="file",
            risk="low",
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        raise RuntimeError("dry_run exploded")

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(success=True)


class VerifyExceptionCapability(BaseCapability):
    """A capability whose verify() raises an exception."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="verify.exception",
            summary="Verify raises",
            domain="file",
            risk="low",
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(is_valid=True)

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(
            success=True,
            evidence=CapabilityEvidence(
                commands_executed=("cmd",),
                rationale="test",
            ),
        )

    def verify(self, input: TestInput) -> VerificationResult:
        raise RuntimeError("verify exploded")


class VerifyFailCapability(BaseCapability):
    """A capability whose verify() returns failed, and supports rollback."""

    _rollback_should_raise: bool = False
    _rollback_should_fail: bool = False

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="verify.fail",
            summary="Verify fails",
            domain="file",
            risk="low",
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(is_valid=True)

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(
            success=True,
            evidence=CapabilityEvidence(
                commands_executed=("cmd",),
                rationale="test",
            ),
        )

    def verify(self, input: TestInput) -> VerificationResult:
        return VerificationResult(
            passed=False,
            discrepancies=("state mismatch",),
        )

    def rollback(self, input: TestInput, result: CapabilityResult) -> RollbackResult:
        if self._rollback_should_raise:
            raise RuntimeError("rollback exploded")
        if self._rollback_should_fail:
            return RollbackResult(success=False, error="rollback failed")
        return RollbackResult(success=True, rolled_back=("item",))


class RunExceptionCapability(BaseCapability):
    """A capability whose run() raises an exception."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="run.exception",
            summary="Run raises",
            domain="file",
            risk="low",
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(is_valid=True)

    def run(self, input: TestInput) -> CapabilityResult:
        raise RuntimeError("run exploded")


class ConfirmNeededCapability(BaseCapability):
    """A capability that always needs confirmation (high risk)."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="confirm.needed",
            summary="Needs confirmation",
            domain="service",
            risk="high",
            requires_privilege=True,
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(
            is_valid=True,
            requires_confirmation=True,
        )

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(success=True)


class TestDryRunRaisesException:
    """Test that dry_run raising an exception produces an invalid result."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(DryRunExceptionCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_dry_run_exception_returns_failure(self):
        """When dry_run raises, the executor catches and returns a failed result."""
        result = await self.executor.execute(
            "dryrun.exception",
            TestInput(),
            require_confirmation=False,
        )

        assert result.success is False
        assert "dry_run exploded" in (result.error or "")


class TestAutonomyAllowsExecution:
    """Test that autonomy engine can_auto=True skips confirmation."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(ConfirmNeededCapability)
        engine = _make_mock_autonomy_engine()
        # Autonomy says: yes, run autonomously
        engine.can_run_autonomously.return_value = (True, "Earned autonomy")
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=engine,
        )

    @pytest.mark.asyncio
    async def test_autonomy_skips_confirmation(self):
        """When autonomy says can_auto=True, no confirmation callback is needed."""
        result = await self.executor.execute(
            "confirm.needed",
            TestInput(),
            require_confirmation=True,
            # No confirm_callback provided - should still proceed
        )

        assert result.success is True


class TestConfirmCallbackRaisesNonCancelled:
    """Test confirm_callback raising something other than CapabilityCancelledError."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(ConfirmNeededCapability)
        engine = _make_mock_autonomy_engine()
        # Autonomy says: needs confirmation
        engine.can_run_autonomously.return_value = (False, "Requires confirmation")
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=engine,
        )

    @pytest.mark.asyncio
    async def test_non_cancelled_exception_wraps_as_cancelled(self):
        """A non-CapabilityCancelledError from the callback wraps into CapabilityCancelledError."""

        async def bad_callback(dry_run: DryRunResult) -> bool:
            raise ValueError("connection lost")

        with pytest.raises(CapabilityCancelledError, match="Confirmation failed"):
            await self.executor.execute(
                "confirm.needed",
                TestInput(),
                require_confirmation=True,
                confirm_callback=bad_callback,
            )


class TestNoConfirmCallbackButRequired:
    """Test when requires_confirmation is True but no callback provided."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(ConfirmNeededCapability)
        engine = _make_mock_autonomy_engine()
        engine.can_run_autonomously.return_value = (False, "Requires confirmation")
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=engine,
        )

    @pytest.mark.asyncio
    async def test_no_callback_still_proceeds(self):
        """When no confirm_callback but confirmation required, execution still proceeds with warning."""
        result = await self.executor.execute(
            "confirm.needed",
            TestInput(),
            require_confirmation=True,
            # No confirm_callback
        )

        # The code logs a warning but does not raise; execution continues
        assert result.success is True


class TestVerifyRaisesException:
    """Test that verify() raising an exception is handled gracefully."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(VerifyExceptionCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_verify_exception_captured_in_result(self):
        """When verify() raises, the exception is captured in evidence."""
        result = await self.executor.execute(
            "verify.exception",
            TestInput(),
            require_confirmation=False,
            skip_verification=False,
        )

        # The execution itself still succeeds
        assert result.success is True
        # But verification_passed is False and discrepancies noted
        assert result.evidence.verification_passed is False
        assert "verify exploded" in result.evidence.verification_details


class TestAutoRollbackOnVerifyFailure:
    """Test auto-rollback when verification fails."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.cap = VerifyFailCapability()
        self.registry.register(type(self.cap))
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_rollback_succeeds_on_verify_fail(self):
        """When verification fails and auto_rollback=True, rollback is attempted and succeeds."""
        result = await self.executor.execute(
            "verify.fail",
            TestInput(),
            require_confirmation=False,
            skip_verification=False,
            auto_rollback_on_verify_fail=True,
        )

        # Result still reflects the original execution with failed verification
        assert result.success is True
        assert result.evidence.verification_passed is False
        assert "state mismatch" in result.evidence.verification_details

    @pytest.mark.asyncio
    async def test_rollback_failure_on_verify_fail(self):
        """When verification fails and rollback also fails, it logs but doesn't raise."""
        # Monkey-patch the class so rollback returns failure
        original = VerifyFailCapability._rollback_should_fail
        VerifyFailCapability._rollback_should_fail = True
        try:
            result = await self.executor.execute(
                "verify.fail",
                TestInput(),
                require_confirmation=False,
                skip_verification=False,
                auto_rollback_on_verify_fail=True,
            )

            assert result.success is True
            assert result.evidence.verification_passed is False
        finally:
            VerifyFailCapability._rollback_should_fail = original

    @pytest.mark.asyncio
    async def test_rollback_exception_on_verify_fail(self):
        """When verification fails and rollback raises an exception, it is caught."""
        original = VerifyFailCapability._rollback_should_raise
        VerifyFailCapability._rollback_should_raise = True
        try:
            result = await self.executor.execute(
                "verify.fail",
                TestInput(),
                require_confirmation=False,
                skip_verification=False,
                auto_rollback_on_verify_fail=True,
            )

            # Even if rollback raises, the result is returned
            assert result.success is True
            assert result.evidence.verification_passed is False
        finally:
            VerifyFailCapability._rollback_should_raise = original


class TestRecordActionViaContext:
    """Test the _record_action_via_context method."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(MockCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_record_with_incident_id_delegates_to_record_action(self):
        """When incident_id is provided, _record_action_via_context calls _record_action."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        with patch.object(self.executor, "_record_action", new_callable=AsyncMock) as mock_rec:
            # Simulate IncidentContext import succeeding and incident_id present
            with patch(
                "elle.capabilities.executor.CapabilityExecutor._record_action_via_context",
                wraps=self.executor._record_action_via_context,
            ):
                await self.executor._record_action_via_context("inc-123", cap, input_data, result)
                mock_rec.assert_awaited_once_with("inc-123", cap, input_data, result)

    @pytest.mark.asyncio
    async def test_record_with_no_incident_id_uses_context(self):
        """When no incident_id, uses IncidentContext.for_capability."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        mock_ctx = MagicMock()
        mock_ctx.record_action = AsyncMock()
        mock_ctx.set_outcome = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_incident_context = MagicMock()
        mock_incident_context.for_capability.return_value = mock_ctx

        mock_recording_mode = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.cli.agentic.incident_context": MagicMock(
                    IncidentContext=mock_incident_context,
                    RecordingMode=mock_recording_mode,
                ),
            },
        ):
            await self.executor._record_action_via_context(None, cap, input_data, result)

        mock_incident_context.for_capability.assert_called_once()
        mock_ctx.record_action.assert_awaited_once()
        mock_ctx.set_outcome.assert_called_once()


class TestRecordActionImportErrorFallback:
    """Test _record_action when incident store import fails."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(MockCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_record_action_import_error_is_silent(self):
        """When incident store import fails, _record_action catches ImportError."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        # Patch the import of elle.daemon.incidents.store inside _record_action
        with patch(
            "elle.daemon.incidents.store.append_action",
            side_effect=ImportError("no incident store"),
        ):
            # Should not raise - ImportError is caught inside _record_action
            await self.executor._record_action("inc-1", cap, input_data, result)

    @pytest.mark.asyncio
    async def test_record_action_direct_import_error(self):
        """Directly test _record_action with import failure of store module."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        with patch(
            "builtins.__import__",
            side_effect=ImportError("cannot import store"),
        ):
            # Should not raise, the method catches ImportError
            await self.executor._record_action("inc-1", cap, input_data, result)


class TestRecordActionGenericException:
    """Test _record_action when recording raises a non-ImportError."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(MockCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_record_action_generic_exception_is_caught(self):
        """When append_action raises a generic exception, _record_action catches it."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        with patch(
            "elle.daemon.incidents.store.append_action",
            side_effect=RuntimeError("db write failed"),
        ):
            # Should not raise
            await self.executor._record_action("inc-1", cap, input_data, result)


class TestRecordActionViaContextImportError:
    """Test _record_action_via_context when IncidentContext import fails."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(MockCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_import_error_fallback_with_incident_id(self):
        """When IncidentContext import fails and incident_id present, falls back to _record_action."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        # Make the IncidentContext import fail
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def failing_import(name, *args, **kwargs):
            if name == "elle.cli.agentic.incident_context":
                raise ImportError("no incident_context")
            return original_import(name, *args, **kwargs)

        with patch.object(self.executor, "_record_action", new_callable=AsyncMock) as mock_rec:
            with patch("builtins.__import__", side_effect=failing_import):
                await self.executor._record_action_via_context("inc-1", cap, input_data, result)
            mock_rec.assert_awaited_once_with("inc-1", cap, input_data, result)

    @pytest.mark.asyncio
    async def test_import_error_fallback_without_incident_id(self):
        """When IncidentContext import fails and no incident_id, nothing happens."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def failing_import(name, *args, **kwargs):
            if name == "elle.cli.agentic.incident_context":
                raise ImportError("no incident_context")
            return original_import(name, *args, **kwargs)

        with patch.object(self.executor, "_record_action", new_callable=AsyncMock) as mock_rec:
            with patch("builtins.__import__", side_effect=failing_import):
                await self.executor._record_action_via_context(None, cap, input_data, result)
            mock_rec.assert_not_awaited()


class TestRecordActionViaContextGenericException:
    """Test _record_action_via_context when a generic exception occurs."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(MockCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_generic_exception_fallback_with_incident_id(self):
        """When IncidentContext raises generic exception and incident_id present, falls back."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def failing_import(name, *args, **kwargs):
            if name == "elle.cli.agentic.incident_context":
                raise RuntimeError("context exploded")
            return original_import(name, *args, **kwargs)

        with patch.object(self.executor, "_record_action", new_callable=AsyncMock) as mock_rec:
            with patch("builtins.__import__", side_effect=failing_import):
                await self.executor._record_action_via_context("inc-2", cap, input_data, result)
            mock_rec.assert_awaited_once_with("inc-2", cap, input_data, result)

    @pytest.mark.asyncio
    async def test_generic_exception_fallback_without_incident_id(self):
        """When IncidentContext raises generic exception and no incident_id, no fallback."""
        cap = MockCapability()
        input_data = TestInput()
        result = CapabilityResult(success=True)

        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def failing_import(name, *args, **kwargs):
            if name == "elle.cli.agentic.incident_context":
                raise RuntimeError("context exploded")
            return original_import(name, *args, **kwargs)

        with patch.object(self.executor, "_record_action", new_callable=AsyncMock) as mock_rec:
            with patch("builtins.__import__", side_effect=failing_import):
                await self.executor._record_action_via_context(None, cap, input_data, result)
            mock_rec.assert_not_awaited()


class TestAutonomyUpdatePaths:
    """Test _update_autonomy_engine (step 10 in executor.execute)."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(MockCapability)

    @pytest.mark.asyncio
    async def test_autonomy_update_success_with_message(self):
        """When update_after_execution returns a message, it is logged."""
        engine = _make_mock_autonomy_engine()
        engine.update_after_execution.return_value = "Autonomy earned for mock.capability"
        executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=engine,
        )

        result = await executor.execute(
            "mock.capability",
            TestInput(),
            require_confirmation=False,
        )

        assert result.success is True
        engine.update_after_execution.assert_called_once_with("mock.capability", True, "low")

    @pytest.mark.asyncio
    async def test_autonomy_update_returns_none(self):
        """When update_after_execution returns None, no message is logged."""
        engine = _make_mock_autonomy_engine()
        engine.update_after_execution.return_value = None
        executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=engine,
        )

        result = await executor.execute(
            "mock.capability",
            TestInput(),
            require_confirmation=False,
        )

        assert result.success is True
        engine.update_after_execution.assert_called_once()

    @pytest.mark.asyncio
    async def test_autonomy_update_raises_exception(self):
        """When update_after_execution raises, the exception is caught silently."""
        engine = _make_mock_autonomy_engine()
        engine.update_after_execution.side_effect = RuntimeError("autonomy db error")
        executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=engine,
        )

        # Should not raise
        result = await executor.execute(
            "mock.capability",
            TestInput(),
            require_confirmation=False,
        )

        assert result.success is True
        engine.update_after_execution.assert_called_once()


class TestRunRaisesException:
    """Test that run() raising propagates as CapabilityExecutionError."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(RunExceptionCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_run_exception_wraps_as_execution_error(self):
        """When run() raises, the executor wraps it as CapabilityExecutionError."""
        with pytest.raises(CapabilityExecutionError, match="run exploded"):
            await self.executor.execute(
                "run.exception",
                TestInput(),
                require_confirmation=False,
            )


class TestRecordingInExecuteFlow:
    """Test that recording in the execute flow catches exceptions gracefully."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(MockCapability)

    @pytest.mark.asyncio
    async def test_record_action_via_context_exception_does_not_break_execute(self):
        """When _record_action_via_context raises inside execute, it is caught."""
        engine = _make_mock_autonomy_engine()
        executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=engine,
        )

        with patch.object(
            executor,
            "_record_action_via_context",
            new_callable=AsyncMock,
            side_effect=RuntimeError("recording broke"),
        ):
            result = await executor.execute(
                "mock.capability",
                TestInput(),
                require_confirmation=False,
            )

        # Execution should still succeed even if recording fails
        assert result.success is True


class TestInputValidation:
    """Test input validation paths."""

    def setup_method(self):
        self.registry = CapabilityRegistry()

        class SchemaCapability(BaseCapability):
            @property
            def spec(self) -> CapabilitySpec:
                return CapabilitySpec(
                    name="schema.cap",
                    summary="Has input schema",
                    domain="file",
                    risk="low",
                    input_schema_name="TestInput",
                )

            def dry_run(self, input: TestInput) -> DryRunResult:
                return DryRunResult(is_valid=True)

            def run(self, input: TestInput) -> CapabilityResult:
                return CapabilityResult(success=True)

        self.registry.register(SchemaCapability)
        self.executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

    @pytest.mark.asyncio
    async def test_non_pydantic_input_raises_input_error(self):
        """When input_schema_name is set but input is not a BaseModel, raise CapabilityInputError."""
        from elle.capabilities.exceptions import CapabilityInputError

        with pytest.raises(CapabilityInputError, match="must be a Pydantic model"):
            await self.executor.execute(
                "schema.cap",
                "not_a_model",  # type: ignore
                require_confirmation=False,
            )


# =============================================================================
# Lazy initialisation tests
# =============================================================================


class TestLazyDependencyChecker:
    """Cover the lazy property for dependency_checker (lines 107-113)."""

    def test_dependency_checker_lazy_init(self):
        """When no dependency_checker provided, one is created lazily."""
        registry = CapabilityRegistry()
        CapabilityExecutor(
            registry=registry,
            autonomy_engine=_make_mock_autonomy_engine(),
            # dependency_checker left as None
        )
        with patch(
            "elle.capabilities.executor.CapabilityExecutor.dependency_checker",
            new_callable=lambda: property(lambda self: MagicMock()),
        ):
            # Just accessing the property triggers lazy init
            pass

        # Test via real lazy init with patched get_checker
        executor2 = CapabilityExecutor(
            registry=registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )
        mock_checker = MagicMock()
        with patch("elle.capabilities.dependencies.checker.get_checker", return_value=mock_checker):
            result = executor2.dependency_checker
            assert result is mock_checker

    def test_dependency_checker_provided(self):
        """When dependency_checker is provided, it is returned directly."""
        registry = CapabilityRegistry()
        mock_checker = MagicMock()
        executor = CapabilityExecutor(
            registry=registry,
            dependency_checker=mock_checker,
        )
        assert executor.dependency_checker is mock_checker


class TestLazyAutonomyEngine:
    """Cover the lazy property for autonomy_engine (lines 116-122)."""

    def test_autonomy_engine_lazy_init(self):
        """When no autonomy_engine provided, one is created lazily."""
        registry = CapabilityRegistry()
        executor = CapabilityExecutor(
            registry=registry,
            # autonomy_engine left as None
        )
        mock_engine = MagicMock()
        with patch("elle.capabilities.autonomy.get_autonomy_engine", return_value=mock_engine):
            result = executor.autonomy_engine
            assert result is mock_engine


# =============================================================================
# dry_run with dependencies
# =============================================================================


class DepsCapability(BaseCapability):
    """A capability that declares dependencies."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="deps.cap",
            summary="Has dependencies",
            domain="file",
            risk="low",
            dependencies=("dep-a", "dep-b"),
        )

    def dry_run(self, input: TestInput) -> DryRunResult:
        return DryRunResult(
            is_valid=True,
            would_execute=("cmd",),
            preview_text="preview",
        )

    def run(self, input: TestInput) -> CapabilityResult:
        return CapabilityResult(success=True)


class TestDryRunWithDependencies:
    """Cover dry_run method with check_dependencies=True (lines 336-360)."""

    def setup_method(self):
        self.registry = CapabilityRegistry()
        self.registry.register(DepsCapability)

    @pytest.mark.asyncio
    async def test_dry_run_with_missing_deps(self):
        """When dependencies are missing, dry_run returns is_valid=False."""
        mock_checker = MagicMock()
        mock_result_a = MagicMock()
        mock_result_a.available = False
        mock_result_b = MagicMock()
        mock_result_b.available = True

        def check_side_effect(name):
            return mock_result_a if name == "dep-a" else mock_result_b

        mock_checker.check.side_effect = check_side_effect

        executor = CapabilityExecutor(
            registry=self.registry,
            dependency_checker=mock_checker,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

        result = await executor.dry_run(
            "deps.cap",
            TestInput(),
            check_dependencies=True,
        )

        assert result.is_valid is False
        assert "dep-a" in result.missing_dependencies
        assert "dep-b" not in result.missing_dependencies
        assert "Missing dependencies" in result.validation_errors[-1]

    @pytest.mark.asyncio
    async def test_dry_run_with_all_deps_available(self):
        """When all dependencies are available, dry_run returns normal result."""
        mock_checker = MagicMock()
        mock_result = MagicMock()
        mock_result.available = True
        mock_checker.check.return_value = mock_result

        executor = CapabilityExecutor(
            registry=self.registry,
            dependency_checker=mock_checker,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

        result = await executor.dry_run(
            "deps.cap",
            TestInput(),
            check_dependencies=True,
        )

        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_dry_run_skip_dependency_check(self):
        """When check_dependencies=False, deps are not checked."""
        executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

        result = await executor.dry_run(
            "deps.cap",
            TestInput(),
            check_dependencies=False,
        )

        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_dry_run_not_found(self):
        """dry_run for nonexistent capability raises CapabilityNotFoundError."""
        executor = CapabilityExecutor(
            registry=self.registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )

        with pytest.raises(CapabilityNotFoundError):
            await executor.dry_run("nonexistent", TestInput())


# =============================================================================
# verify standalone
# =============================================================================


class TestVerifyNotFound:
    """Cover verify method when capability not found (lines 380-385)."""

    @pytest.mark.asyncio
    async def test_verify_not_found(self):
        registry = CapabilityRegistry()
        executor = CapabilityExecutor(
            registry=registry,
            autonomy_engine=_make_mock_autonomy_engine(),
        )
        with pytest.raises(CapabilityNotFoundError):
            await executor.verify("nonexistent", TestInput())


# =============================================================================
# execute_capability convenience function
# =============================================================================


class TestExecuteCapabilityConvenience:
    """Cover the module-level execute_capability function (line 534)."""

    @pytest.mark.asyncio
    async def test_execute_capability_function(self):
        from elle.capabilities.executor import execute_capability, reset_executor

        reset_executor()

        mock_result = CapabilityResult(success=True)
        with patch.object(CapabilityExecutor, "execute", new_callable=AsyncMock, return_value=mock_result):
            with patch("elle.capabilities.executor.CapabilityExecutor.__init__", return_value=None):
                result = await execute_capability("mock.cap", TestInput())
                assert result.success is True

        reset_executor()
