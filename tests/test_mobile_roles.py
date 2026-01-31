from __future__ import annotations

"""Tests for elle.mobile.roles — role-based access control for Mobile Gateway.

Covers:
- Module-level constants (ROLE_ALLOWED_MODELS, HIGH_RISK_CAPABILITIES, OPERATOR_ALLOWED_CAPABILITIES)
- RoleViolationError exception
- RoleEnforcer.filter_request() — model validation and downgrading
- RoleEnforcer.can_execute_capability() — high-risk blocking, readonly deny, operator allow/deny
- RoleEnforcer.get_execution_mode() — readonly vs execute
- RoleEnforcer.validate_tool_call() — capability prefix dispatch, readonly tool allow-list, operator passthrough
- get_role_description() — description lookup and unknown role fallback
- get_allowed_operations() — per-role allowed operations and unknown role fallback
- get_blocked_operations() — per-role blocked operations and unknown role fallback
"""

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure elle.mobile.crypto is importable even when 'cryptography' is absent.
# The real module imports 'cryptography' at the top level, which may not be
# installed in the test environment.  We inject a lightweight mock module
# into sys.modules so that the transitive import chain resolves cleanly.
# ---------------------------------------------------------------------------
import elle.mobile

if "elle.mobile.crypto" not in sys.modules:
    _crypto_stub = MagicMock()
    sys.modules["elle.mobile.crypto"] = _crypto_stub
    elle.mobile.crypto = _crypto_stub  # Required for patch() dotted-path resolution

from elle.mobile.models import MobileRole
from elle.mobile.roles import (
    HIGH_RISK_CAPABILITIES,
    OPERATOR_ALLOWED_CAPABILITIES,
    ROLE_ALLOWED_MODELS,
    RoleEnforcer,
    RoleViolationError,
    get_allowed_operations,
    get_blocked_operations,
    get_role_description,
)

# =============================================================================
# Helpers
# =============================================================================

FAKE_FINGERPRINT = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
CLIENT_IP = "192.168.1.42"


def _make_auth_context(
    device_id: str = "dev-001",
    device_name: str = "Test Phone",
    role: MobileRole = MobileRole.MOBILE_READONLY,
    effective_role: MobileRole | None = None,
) -> MagicMock:
    """Build a mock MobileAuthContext with sensible defaults."""
    ctx = MagicMock()
    ctx.device_id = device_id
    ctx.device_name = device_name
    ctx.role = role
    ctx.effective_role = effective_role if effective_role is not None else role
    ctx.client_ip = CLIENT_IP
    ctx.cert_fingerprint = FAKE_FINGERPRINT
    return ctx


# =============================================================================
# TestModuleConstants
# =============================================================================


class TestModuleConstants:
    """Tests for module-level constant dictionaries and sets."""

    def test_role_allowed_models_has_readonly_key(self):
        """ROLE_ALLOWED_MODELS contains a mapping for MOBILE_READONLY."""
        assert MobileRole.MOBILE_READONLY in ROLE_ALLOWED_MODELS

    def test_role_allowed_models_has_operator_key(self):
        """ROLE_ALLOWED_MODELS contains a mapping for MOBILE_OPERATOR."""
        assert MobileRole.MOBILE_OPERATOR in ROLE_ALLOWED_MODELS

    def test_readonly_models_are_subset_of_operator(self):
        """The readonly allowed models are a subset of operator allowed models."""
        readonly_models = ROLE_ALLOWED_MODELS[MobileRole.MOBILE_READONLY]
        operator_models = ROLE_ALLOWED_MODELS[MobileRole.MOBILE_OPERATOR]
        assert readonly_models.issubset(operator_models)

    def test_operator_models_include_elle(self):
        """Operator role can use the 'elle' model."""
        assert "elle" in ROLE_ALLOWED_MODELS[MobileRole.MOBILE_OPERATOR]

    def test_readonly_models_do_not_include_elle(self):
        """Readonly role cannot use the 'elle' model."""
        assert "elle" not in ROLE_ALLOWED_MODELS[MobileRole.MOBILE_READONLY]

    def test_readonly_models_include_elle_readonly(self):
        """Readonly role can use 'elle.readonly'."""
        assert "elle.readonly" in ROLE_ALLOWED_MODELS[MobileRole.MOBILE_READONLY]

    def test_high_risk_capabilities_is_non_empty(self):
        """HIGH_RISK_CAPABILITIES contains at least one entry."""
        assert len(HIGH_RISK_CAPABILITIES) > 0

    def test_file_delete_is_high_risk(self):
        """file.delete is classified as high risk."""
        assert "file.delete" in HIGH_RISK_CAPABILITIES

    def test_system_reboot_is_high_risk(self):
        """system.reboot is classified as high risk."""
        assert "system.reboot" in HIGH_RISK_CAPABILITIES

    def test_operator_allowed_capabilities_is_non_empty(self):
        """OPERATOR_ALLOWED_CAPABILITIES contains at least one entry."""
        assert len(OPERATOR_ALLOWED_CAPABILITIES) > 0

    def test_service_restart_is_operator_allowed(self):
        """service.restart is allowed for operators."""
        assert "service.restart" in OPERATOR_ALLOWED_CAPABILITIES

    def test_high_risk_and_operator_are_disjoint(self):
        """High-risk and operator-allowed capability sets do not overlap."""
        overlap = HIGH_RISK_CAPABILITIES & OPERATOR_ALLOWED_CAPABILITIES
        assert overlap == set(), f"Unexpected overlap: {overlap}"


# =============================================================================
# TestRoleViolationError
# =============================================================================


class TestRoleViolationError:
    """Tests for the RoleViolationError exception."""

    def test_exception_stores_message(self):
        """RoleViolationError stores the message string."""
        err = RoleViolationError("not allowed", role=MobileRole.MOBILE_READONLY)
        assert str(err) == "not allowed"

    def test_exception_stores_role(self):
        """RoleViolationError stores the role attribute."""
        err = RoleViolationError("denied", role=MobileRole.MOBILE_OPERATOR)
        assert err.role == MobileRole.MOBILE_OPERATOR

    def test_exception_is_exception(self):
        """RoleViolationError inherits from Exception."""
        err = RoleViolationError("msg", role=MobileRole.MOBILE_READONLY)
        assert isinstance(err, Exception)

    def test_exception_can_be_raised_and_caught(self):
        """RoleViolationError can be raised and caught."""
        with pytest.raises(RoleViolationError) as exc_info:
            raise RoleViolationError("test error", role=MobileRole.MOBILE_READONLY)
        assert exc_info.value.role == MobileRole.MOBILE_READONLY
        assert "test error" in str(exc_info.value)


# =============================================================================
# TestRoleEnforcerFilterRequest
# =============================================================================


class TestRoleEnforcerFilterRequest:
    """Tests for RoleEnforcer.filter_request()."""

    def setup_method(self):
        self.enforcer = RoleEnforcer()

    # -- Readonly role --

    def test_readonly_allowed_model_passes_through(self):
        """Readonly device requesting 'elle.readonly' keeps the model."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        body = {"model": "elle.readonly", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle.readonly"

    def test_readonly_allowed_model_dash_variant(self):
        """Readonly device requesting 'elle-readonly' keeps the model."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        body = {"model": "elle-readonly", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle-readonly"

    def test_readonly_disallowed_model_downgrades(self):
        """Readonly device requesting 'elle' is downgraded to 'elle.readonly'."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        body = {"model": "elle", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle.readonly"

    def test_readonly_unknown_model_downgrades(self):
        """Readonly device requesting an unknown model is downgraded to 'elle.readonly'."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        body = {"model": "gpt-4", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle.readonly"

    def test_readonly_no_model_downgrades(self):
        """Readonly device with no model key defaults to empty string and is downgraded."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        body = {"messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle.readonly"

    def test_readonly_downgrade_preserves_other_fields(self):
        """Downgrading the model preserves other request body fields."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        messages = [{"role": "user", "content": "hello"}]
        body = {"model": "elle", "messages": messages, "temperature": 0.7}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle.readonly"
        assert result["messages"] == messages
        assert result["temperature"] == 0.7

    def test_readonly_downgrade_does_not_mutate_original(self):
        """Downgrading creates a new dict rather than mutating the original."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        body = {"model": "elle", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert body["model"] == "elle"  # Original unchanged
        assert result["model"] == "elle.readonly"

    # -- Operator role --

    def test_operator_allowed_model_elle_passes_through(self):
        """Operator device requesting 'elle' keeps the model."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        body = {"model": "elle", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle"

    def test_operator_allowed_model_readonly_passes_through(self):
        """Operator device requesting 'elle.readonly' keeps the model."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        body = {"model": "elle.readonly", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle.readonly"

    def test_operator_disallowed_model_downgrades_to_elle(self):
        """Operator device requesting an unknown model is downgraded to 'elle'."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        body = {"model": "gpt-4", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle"

    def test_operator_no_model_downgrades_to_elle(self):
        """Operator device with no model key is downgraded to 'elle'."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        body = {"messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle"

    def test_operator_downgrade_preserves_other_fields(self):
        """Operator model downgrade preserves other request body fields."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        body = {"model": "some-other", "messages": [], "stream": True}
        result = self.enforcer.filter_request(auth, body)
        assert result["model"] == "elle"
        assert result["stream"] is True

    def test_operator_downgrade_does_not_mutate_original(self):
        """Operator downgrade creates a new dict rather than mutating."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        body = {"model": "unknown-model", "messages": []}
        result = self.enforcer.filter_request(auth, body)
        assert body["model"] == "unknown-model"
        assert result["model"] == "elle"

    # -- Unknown role edge case --

    def test_unknown_role_defaults_to_empty_allowed_set(self):
        """A role not in ROLE_ALLOWED_MODELS yields an empty allowed set, triggering downgrade."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        # Simulate an unrecognized role by manually setting effective_role
        auth.effective_role = "unknown_role"
        body = {"model": "elle", "messages": []}
        # This should NOT raise; the method defaults to empty set for unknown roles
        # The outer if checks "requested_model not in allowed_models" which is True
        # but the inner if checks for MOBILE_READONLY and MOBILE_OPERATOR
        # Since neither matches, it falls through and returns the body as-is
        result = self.enforcer.filter_request(auth, body)
        assert result is body  # No modification


# =============================================================================
# TestRoleEnforcerCanExecuteCapability
# =============================================================================


class TestRoleEnforcerCanExecuteCapability:
    """Tests for RoleEnforcer.can_execute_capability()."""

    def setup_method(self):
        self.enforcer = RoleEnforcer()

    # -- High-risk blocking --

    def test_high_risk_blocked_for_readonly(self):
        """High-risk capability blocked for readonly role."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        assert self.enforcer.can_execute_capability(auth, "file.delete") is False

    def test_high_risk_blocked_for_operator(self):
        """High-risk capability blocked even for operator role."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.can_execute_capability(auth, "file.delete") is False

    def test_all_high_risk_blocked_for_operator(self):
        """Every capability in HIGH_RISK_CAPABILITIES is blocked for operator."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        for cap in HIGH_RISK_CAPABILITIES:
            assert self.enforcer.can_execute_capability(auth, cap) is False, f"{cap} should be blocked"

    def test_system_reboot_blocked(self):
        """system.reboot is always blocked."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.can_execute_capability(auth, "system.reboot") is False

    def test_config_edit_blocked(self):
        """config.edit is always blocked."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.can_execute_capability(auth, "config.edit") is False

    # -- Readonly role --

    def test_readonly_cannot_execute_any_capability(self):
        """Readonly role cannot execute any non-high-risk capability."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        assert self.enforcer.can_execute_capability(auth, "service.status") is False

    def test_readonly_cannot_execute_operator_capability(self):
        """Readonly role cannot execute operator-allowed capabilities."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        assert self.enforcer.can_execute_capability(auth, "service.restart") is False

    # -- Operator role --

    def test_operator_can_execute_allowed_capability(self):
        """Operator can execute capabilities in OPERATOR_ALLOWED_CAPABILITIES."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.can_execute_capability(auth, "service.restart") is True

    def test_operator_can_execute_all_allowed(self):
        """Operator can execute every capability in OPERATOR_ALLOWED_CAPABILITIES."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        for cap in OPERATOR_ALLOWED_CAPABILITIES:
            assert self.enforcer.can_execute_capability(auth, cap) is True, f"{cap} should be allowed"

    def test_operator_cannot_execute_unlisted_capability(self):
        """Operator cannot execute capabilities not in the allowed set."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.can_execute_capability(auth, "custom.something") is False

    def test_operator_service_status_allowed(self):
        """Operator can check service.status."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.can_execute_capability(auth, "service.status") is True

    def test_operator_docker_logs_allowed(self):
        """Operator can access docker.logs."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.can_execute_capability(auth, "docker.logs") is True

    # -- Unknown role --

    def test_unknown_role_returns_false(self):
        """An unrecognized role returns False for any non-high-risk capability."""
        auth = _make_auth_context()
        auth.effective_role = "some_unknown_role"
        assert self.enforcer.can_execute_capability(auth, "service.status") is False


# =============================================================================
# TestRoleEnforcerGetExecutionMode
# =============================================================================


class TestRoleEnforcerGetExecutionMode:
    """Tests for RoleEnforcer.get_execution_mode()."""

    def setup_method(self):
        self.enforcer = RoleEnforcer()

    def test_readonly_returns_readonly(self):
        """Readonly role returns 'readonly' execution mode."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        assert self.enforcer.get_execution_mode(auth) == "readonly"

    def test_operator_returns_execute(self):
        """Operator role returns 'execute' execution mode."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.get_execution_mode(auth) == "execute"

    def test_unknown_role_returns_execute(self):
        """An unrecognized role defaults to 'execute' mode."""
        auth = _make_auth_context()
        auth.effective_role = "custom_role"
        assert self.enforcer.get_execution_mode(auth) == "execute"


# =============================================================================
# TestRoleEnforcerValidateToolCall
# =============================================================================


class TestRoleEnforcerValidateToolCall:
    """Tests for RoleEnforcer.validate_tool_call()."""

    def setup_method(self):
        self.enforcer = RoleEnforcer()

    # -- capability.* prefix dispatch --

    def test_capability_prefix_delegates_to_can_execute(self):
        """Tool name starting with 'capability.' delegates to can_execute_capability."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.validate_tool_call(auth, "capability.service.restart", {}) is True

    def test_capability_prefix_high_risk_blocked(self):
        """capability.file.delete delegates and is blocked as high-risk."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.validate_tool_call(auth, "capability.file.delete", {}) is False

    def test_capability_prefix_readonly_blocked(self):
        """Readonly role cannot use any capability.* tool."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        assert self.enforcer.validate_tool_call(auth, "capability.service.status", {}) is False

    def test_capability_prefix_strips_correctly(self):
        """The 'capability.' prefix is stripped to get the capability name."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        # service.start is in OPERATOR_ALLOWED_CAPABILITIES
        assert self.enforcer.validate_tool_call(auth, "capability.service.start", {}) is True
        # custom.unknown is not in OPERATOR_ALLOWED_CAPABILITIES
        assert self.enforcer.validate_tool_call(auth, "capability.custom.unknown", {}) is False

    # -- Readonly tool allow-list --

    def test_readonly_allowed_tools(self):
        """Readonly role can use the specific read/query tools."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        allowed_tools = ["man_search", "incident_search", "status", "logs", "events"]
        for tool in allowed_tools:
            assert self.enforcer.validate_tool_call(auth, tool, {}) is True, f"readonly should be able to use '{tool}'"

    def test_readonly_blocked_non_allowed_tool(self):
        """Readonly role cannot use tools outside the allow-list."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        assert self.enforcer.validate_tool_call(auth, "shell_command", {}) is False

    def test_readonly_blocked_execute_tool(self):
        """Readonly role cannot use execution-type tools."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        assert self.enforcer.validate_tool_call(auth, "execute_capability", {}) is False

    def test_readonly_blocked_unknown_tool(self):
        """Readonly role cannot use unknown tool names."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_READONLY)
        assert self.enforcer.validate_tool_call(auth, "something_random", {}) is False

    # -- Operator passthrough --

    def test_operator_allowed_any_non_capability_tool(self):
        """Operator role can use any tool that does not start with 'capability.'."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.validate_tool_call(auth, "shell_command", {}) is True

    def test_operator_allowed_read_tools(self):
        """Operator role can use read/query tools."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.validate_tool_call(auth, "man_search", {}) is True
        assert self.enforcer.validate_tool_call(auth, "status", {}) is True

    def test_operator_allowed_execution_tools(self):
        """Operator role can use execution tools."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        assert self.enforcer.validate_tool_call(auth, "execute_capability", {}) is True

    def test_tool_args_passed_but_not_inspected(self):
        """Tool args are accepted but not inspected by validate_tool_call."""
        auth = _make_auth_context(effective_role=MobileRole.MOBILE_OPERATOR)
        args = {"command": "ls -la", "target": "/etc"}
        assert self.enforcer.validate_tool_call(auth, "shell_command", args) is True


# =============================================================================
# TestGetRoleDescription
# =============================================================================


class TestGetRoleDescription:
    """Tests for get_role_description()."""

    def test_readonly_description(self):
        """Readonly role returns a description mentioning read-only."""
        desc = get_role_description(MobileRole.MOBILE_READONLY)
        assert "Read-only" in desc
        assert "cannot execute" in desc

    def test_operator_description(self):
        """Operator role returns a description mentioning safe operations."""
        desc = get_role_description(MobileRole.MOBILE_OPERATOR)
        assert "Operator" in desc
        assert "safe operations" in desc

    def test_operator_description_mentions_restrictions(self):
        """Operator description mentions high-risk restrictions."""
        desc = get_role_description(MobileRole.MOBILE_OPERATOR)
        assert "high-risk" in desc

    def test_unknown_role_returns_unknown(self):
        """An unrecognized role returns 'Unknown role'."""
        desc = get_role_description("nonexistent_role")
        assert desc == "Unknown role"


# =============================================================================
# TestGetAllowedOperations
# =============================================================================


class TestGetAllowedOperations:
    """Tests for get_allowed_operations()."""

    def test_readonly_operations_list(self):
        """Readonly role returns a non-empty list of allowed operations."""
        ops = get_allowed_operations(MobileRole.MOBILE_READONLY)
        assert isinstance(ops, list)
        assert len(ops) > 0

    def test_readonly_includes_status(self):
        """Readonly operations include viewing system status."""
        ops = get_allowed_operations(MobileRole.MOBILE_READONLY)
        assert any("status" in op.lower() for op in ops)

    def test_readonly_includes_logs(self):
        """Readonly operations include viewing logs."""
        ops = get_allowed_operations(MobileRole.MOBILE_READONLY)
        assert any("log" in op.lower() for op in ops)

    def test_readonly_includes_search(self):
        """Readonly operations include searching man pages and incidents."""
        ops = get_allowed_operations(MobileRole.MOBILE_READONLY)
        assert any("search" in op.lower() or "man" in op.lower() for op in ops)

    def test_operator_operations_list(self):
        """Operator role returns a non-empty list of allowed operations."""
        ops = get_allowed_operations(MobileRole.MOBILE_OPERATOR)
        assert isinstance(ops, list)
        assert len(ops) > 0

    def test_operator_includes_readonly_reference(self):
        """Operator operations include all readonly operations by reference."""
        ops = get_allowed_operations(MobileRole.MOBILE_OPERATOR)
        assert any("readonly" in op.lower() for op in ops)

    def test_operator_includes_services(self):
        """Operator operations include service management."""
        ops = get_allowed_operations(MobileRole.MOBILE_OPERATOR)
        assert any("service" in op.lower() for op in ops)

    def test_unknown_role_returns_empty_list(self):
        """An unrecognized role returns an empty list."""
        ops = get_allowed_operations("nonexistent_role")
        assert ops == []


# =============================================================================
# TestGetBlockedOperations
# =============================================================================


class TestGetBlockedOperations:
    """Tests for get_blocked_operations()."""

    def test_readonly_blocked_list(self):
        """Readonly role returns a non-empty list of blocked operations."""
        ops = get_blocked_operations(MobileRole.MOBILE_READONLY)
        assert isinstance(ops, list)
        assert len(ops) > 0

    def test_readonly_blocks_commands(self):
        """Readonly blocks executing system commands."""
        ops = get_blocked_operations(MobileRole.MOBILE_READONLY)
        assert any("command" in op.lower() for op in ops)

    def test_readonly_blocks_modifications(self):
        """Readonly blocks file/config modifications."""
        ops = get_blocked_operations(MobileRole.MOBILE_READONLY)
        assert any("modif" in op.lower() for op in ops)

    def test_readonly_blocks_services(self):
        """Readonly blocks starting/stopping services."""
        ops = get_blocked_operations(MobileRole.MOBILE_READONLY)
        assert any("service" in op.lower() for op in ops)

    def test_readonly_blocks_packages(self):
        """Readonly blocks package installation/removal."""
        ops = get_blocked_operations(MobileRole.MOBILE_READONLY)
        assert any("package" in op.lower() for op in ops)

    def test_operator_blocked_list(self):
        """Operator role returns a non-empty list of blocked operations."""
        ops = get_blocked_operations(MobileRole.MOBILE_OPERATOR)
        assert isinstance(ops, list)
        assert len(ops) > 0

    def test_operator_blocks_file_deletion(self):
        """Operator blocks file deletion."""
        ops = get_blocked_operations(MobileRole.MOBILE_OPERATOR)
        assert any("delete" in op.lower() for op in ops)

    def test_operator_blocks_config_editing(self):
        """Operator blocks configuration editing."""
        ops = get_blocked_operations(MobileRole.MOBILE_OPERATOR)
        assert any("config" in op.lower() for op in ops)

    def test_operator_blocks_service_stop(self):
        """Operator blocks stopping/disabling services."""
        ops = get_blocked_operations(MobileRole.MOBILE_OPERATOR)
        assert any("stop" in op.lower() or "disable" in op.lower() for op in ops)

    def test_operator_blocks_package_removal(self):
        """Operator blocks package removal."""
        ops = get_blocked_operations(MobileRole.MOBILE_OPERATOR)
        assert any("package" in op.lower() or "remove" in op.lower() for op in ops)

    def test_operator_blocks_reboot(self):
        """Operator blocks system shutdown/reboot."""
        ops = get_blocked_operations(MobileRole.MOBILE_OPERATOR)
        assert any("shutdown" in op.lower() or "reboot" in op.lower() for op in ops)

    def test_unknown_role_returns_empty_list(self):
        """An unrecognized role returns an empty list."""
        ops = get_blocked_operations("nonexistent_role")
        assert ops == []
