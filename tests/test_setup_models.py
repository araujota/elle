"""Tests for setup wizard Pydantic models."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from elle.cli.setup.models import (
    CONFIRMATION_INFO,
    FEATURE_INFO,
    PRIVILEGE_LEVEL_INFO,
    SAFETY_LEVEL_INFO,
    TELEMETRY_INFO,
    ConfirmationPreference,
    PrivilegeLevel,
    SafetyLevel,
    SetupPreferences,
    SetupState,
)

# =============================================================================
# Enums
# =============================================================================


class TestSafetyLevel:
    """Tests for SafetyLevel enum."""

    def test_values(self) -> None:
        assert SafetyLevel.STANDARD == "standard"
        assert SafetyLevel.CAUTIOUS == "cautious"
        assert SafetyLevel.MINIMAL == "minimal"

    def test_string_type(self) -> None:
        assert isinstance(SafetyLevel.STANDARD, str)
        assert isinstance(SafetyLevel.STANDARD, SafetyLevel)

    def test_all_members(self) -> None:
        assert len(SafetyLevel) == 3

    def test_from_value(self) -> None:
        assert SafetyLevel("standard") is SafetyLevel.STANDARD
        assert SafetyLevel("cautious") is SafetyLevel.CAUTIOUS
        assert SafetyLevel("minimal") is SafetyLevel.MINIMAL

    def test_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            SafetyLevel("extreme")


class TestConfirmationPreference:
    """Tests for ConfirmationPreference enum."""

    def test_values(self) -> None:
        assert ConfirmationPreference.ALWAYS == "always"
        assert ConfirmationPreference.HIGH_RISK == "high_risk"
        assert ConfirmationPreference.NEVER == "never"

    def test_string_type(self) -> None:
        assert isinstance(ConfirmationPreference.ALWAYS, str)

    def test_all_members(self) -> None:
        assert len(ConfirmationPreference) == 3

    def test_from_value(self) -> None:
        assert ConfirmationPreference("always") is ConfirmationPreference.ALWAYS


class TestPrivilegeLevel:
    """Tests for PrivilegeLevel enum."""

    def test_values(self) -> None:
        assert PrivilegeLevel.SECURE == "secure"
        assert PrivilegeLevel.CONVENIENT == "convenient"
        assert PrivilegeLevel.PASSWORDLESS == "passwordless"

    def test_string_type(self) -> None:
        assert isinstance(PrivilegeLevel.SECURE, str)

    def test_all_members(self) -> None:
        assert len(PrivilegeLevel) == 3

    def test_from_value(self) -> None:
        assert PrivilegeLevel("secure") is PrivilegeLevel.SECURE


# =============================================================================
# SetupPreferences
# =============================================================================


class TestSetupPreferences:
    """Tests for SetupPreferences model."""

    def test_defaults(self) -> None:
        prefs = SetupPreferences()
        assert prefs.safety_level is SafetyLevel.STANDARD
        assert prefs.confirmation_preference is ConfirmationPreference.HIGH_RISK
        assert prefs.require_preview_for_configs is True
        assert prefs.journal_enabled is True
        assert prefs.kernel_enabled is True
        assert prefs.probes_enabled is True
        assert prefs.ebpf_enabled is False
        assert prefs.docker_enabled is True
        assert prefs.api_enabled is True
        assert prefs.auto_learn_packages is True
        assert prefs.privilege_level is PrivilegeLevel.SECURE
        assert prefs.polkit_configured is False
        assert prefs.ollama_verified is False

    def test_custom_safety(self) -> None:
        prefs = SetupPreferences(safety_level=SafetyLevel.CAUTIOUS)
        assert prefs.safety_level is SafetyLevel.CAUTIOUS

    def test_custom_confirmation(self) -> None:
        prefs = SetupPreferences(confirmation_preference=ConfirmationPreference.ALWAYS)
        assert prefs.confirmation_preference is ConfirmationPreference.ALWAYS

    def test_ebpf_enabled(self) -> None:
        prefs = SetupPreferences(ebpf_enabled=True)
        assert prefs.ebpf_enabled is True

    def test_disable_all_telemetry(self) -> None:
        prefs = SetupPreferences(
            journal_enabled=False,
            kernel_enabled=False,
            probes_enabled=False,
            ebpf_enabled=False,
            docker_enabled=False,
        )
        assert prefs.journal_enabled is False
        assert prefs.kernel_enabled is False
        assert prefs.probes_enabled is False
        assert prefs.ebpf_enabled is False
        assert prefs.docker_enabled is False

    def test_privilege_convenient(self) -> None:
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.CONVENIENT)
        assert prefs.privilege_level is PrivilegeLevel.CONVENIENT

    def test_privilege_passwordless(self) -> None:
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.PASSWORDLESS)
        assert prefs.privilege_level is PrivilegeLevel.PASSWORDLESS

    def test_all_verified(self) -> None:
        prefs = SetupPreferences(
            polkit_configured=True,
            ollama_verified=True,
        )
        assert prefs.polkit_configured is True
        assert prefs.ollama_verified is True

    def test_invalid_safety_level(self) -> None:
        with pytest.raises(ValidationError):
            SetupPreferences(safety_level="extreme")

    def test_invalid_confirmation_preference(self) -> None:
        with pytest.raises(ValidationError):
            SetupPreferences(confirmation_preference="sometimes")

    def test_invalid_privilege_level(self) -> None:
        with pytest.raises(ValidationError):
            SetupPreferences(privilege_level="root")

    def test_serialization_roundtrip(self) -> None:
        prefs = SetupPreferences(
            safety_level=SafetyLevel.CAUTIOUS,
            confirmation_preference=ConfirmationPreference.ALWAYS,
            ebpf_enabled=True,
            privilege_level=PrivilegeLevel.CONVENIENT,
            ollama_verified=True,
        )
        data = prefs.model_dump()
        restored = SetupPreferences.model_validate(data)
        assert restored.safety_level == prefs.safety_level
        assert restored.confirmation_preference == prefs.confirmation_preference
        assert restored.ebpf_enabled == prefs.ebpf_enabled
        assert restored.privilege_level == prefs.privilege_level
        assert restored.ollama_verified == prefs.ollama_verified

    def test_string_coercion_for_enums(self) -> None:
        prefs = SetupPreferences(
            safety_level="cautious",
            confirmation_preference="always",
            privilege_level="convenient",
        )
        assert prefs.safety_level is SafetyLevel.CAUTIOUS
        assert prefs.confirmation_preference is ConfirmationPreference.ALWAYS
        assert prefs.privilege_level is PrivilegeLevel.CONVENIENT


# =============================================================================
# SetupState
# =============================================================================


class TestSetupState:
    """Tests for SetupState model."""

    def test_defaults(self) -> None:
        state = SetupState()
        assert state.completed is False
        assert state.completed_at is None
        assert state.version == "0.1.0"
        assert isinstance(state.preferences, SetupPreferences)

    def test_completed_state(self) -> None:
        now = datetime.now()
        state = SetupState(
            completed=True,
            completed_at=now,
            version="0.2.0",
        )
        assert state.completed is True
        assert state.completed_at == now
        assert state.version == "0.2.0"

    def test_with_custom_preferences(self) -> None:
        prefs = SetupPreferences(safety_level=SafetyLevel.MINIMAL)
        state = SetupState(preferences=prefs)
        assert state.preferences.safety_level is SafetyLevel.MINIMAL

    def test_nested_preferences_defaults(self) -> None:
        state = SetupState()
        assert state.preferences.safety_level is SafetyLevel.STANDARD
        assert state.preferences.ebpf_enabled is False

    def test_serialization_roundtrip(self) -> None:
        now = datetime.now()
        state = SetupState(
            completed=True,
            completed_at=now,
            version="1.0.0",
            preferences=SetupPreferences(
                safety_level=SafetyLevel.CAUTIOUS,
                ebpf_enabled=True,
            ),
        )
        data = state.model_dump()
        restored = SetupState.model_validate(data)
        assert restored.completed == state.completed
        assert restored.version == state.version
        assert restored.preferences.safety_level == state.preferences.safety_level
        assert restored.preferences.ebpf_enabled is True

    def test_partial_creation_via_dict(self) -> None:
        state = SetupState.model_validate({"completed": True, "version": "0.3.0"})
        assert state.completed is True
        assert state.version == "0.3.0"
        assert isinstance(state.preferences, SetupPreferences)


# =============================================================================
# Info Dictionaries
# =============================================================================


class TestInfoDictionaries:
    """Tests for the module-level info dictionaries."""

    def test_safety_level_info_keys(self) -> None:
        for level in SafetyLevel:
            assert level in SAFETY_LEVEL_INFO
            assert "name" in SAFETY_LEVEL_INFO[level]
            assert "description" in SAFETY_LEVEL_INFO[level]

    def test_confirmation_info_keys(self) -> None:
        for pref in ConfirmationPreference:
            assert pref in CONFIRMATION_INFO
            assert "name" in CONFIRMATION_INFO[pref]
            assert "description" in CONFIRMATION_INFO[pref]

    def test_telemetry_info_keys(self) -> None:
        expected_keys = {"journal", "kernel", "probes", "ebpf", "docker"}
        assert set(TELEMETRY_INFO.keys()) == expected_keys
        for info in TELEMETRY_INFO.values():
            assert "name" in info
            assert "description" in info
            assert "default" in info

    def test_feature_info_keys(self) -> None:
        expected_keys = {"api", "auto_learn_packages"}
        assert set(FEATURE_INFO.keys()) == expected_keys
        for info in FEATURE_INFO.values():
            assert "name" in info
            assert "description" in info
            assert "default" in info

    def test_privilege_level_info_keys(self) -> None:
        for level in PrivilegeLevel:
            assert level in PRIVILEGE_LEVEL_INFO
            assert "name" in PRIVILEGE_LEVEL_INFO[level]
            assert "description" in PRIVILEGE_LEVEL_INFO[level]

    def test_passwordless_has_warning(self) -> None:
        assert "warning" in PRIVILEGE_LEVEL_INFO[PrivilegeLevel.PASSWORDLESS]

    def test_telemetry_defaults_match_preferences(self) -> None:
        prefs = SetupPreferences()
        assert TELEMETRY_INFO["journal"]["default"] == prefs.journal_enabled
        assert TELEMETRY_INFO["kernel"]["default"] == prefs.kernel_enabled
        assert TELEMETRY_INFO["probes"]["default"] == prefs.probes_enabled
        assert TELEMETRY_INFO["ebpf"]["default"] == prefs.ebpf_enabled
        assert TELEMETRY_INFO["docker"]["default"] == prefs.docker_enabled

    def test_feature_defaults_match_preferences(self) -> None:
        prefs = SetupPreferences()
        assert FEATURE_INFO["api"]["default"] == prefs.api_enabled
        assert FEATURE_INFO["auto_learn_packages"]["default"] == prefs.auto_learn_packages
