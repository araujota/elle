from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    StoredCapability,
    TrustLevel,
)
from elle.capabilities.autogen.loader import (
    load_capability_from_stored,
    _compile_core_capability,
    load_all_approved,
    load_capability_by_name,
    register_autogen_capabilities,
    get_loaded_autogen_capabilities,
    create_capability_instance,
    execute_autogen_capability,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec() -> GeneratedCapabilitySpec:
    return GeneratedCapabilitySpec(
        name="pkg.action",
        display_name="Pkg Action",
        description="Does stuff",
        domain="file",
        risk_level="medium",
        command_template="pkg action",
        source_command="pkg",
    )


def _make_stored(
    trust_level: TrustLevel = TrustLevel.OFFICIAL,
    capability_name: str = "pkg.action",
    approved: bool = True,
    enabled: bool = True,
) -> StoredCapability:
    spec = _make_spec()
    return StoredCapability(
        id="test-id-1",
        capability_name=capability_name,
        spec_json=spec.model_dump_json(),
        input_model_code="class PkgActionInput:\n    pass",
        output_model_code="class PkgActionOutput:\n    pass",
        capability_class_code="class PkgActionCapability:\n    pass",
        source_command="pkg",
        man_page_hash="abc123",
        trust_level=trust_level,
        approved=approved,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# load_capability_from_stored
# ---------------------------------------------------------------------------

class TestLoadCapabilityFromStored:

    def test_official_uses_factory(self):
        stored = _make_stored(trust_level=TrustLevel.OFFICIAL)
        mock_class = type("FakeCapability", (), {})

        with patch(
            "elle.capabilities.autogen.loader.compile_capability_class",
            return_value=mock_class,
        ):
            result = load_capability_from_stored(stored)
        assert result is mock_class
        assert result._autogen_id == "test-id-1"
        assert result._autogen_name == "pkg.action"
        assert result._autogen_trust == TrustLevel.OFFICIAL

    def test_core_uses_compile_core(self):
        stored = _make_stored(trust_level=TrustLevel.CORE)
        mock_class = type("FakeCapability", (), {})

        with patch(
            "elle.capabilities.autogen.loader._compile_core_capability",
            return_value=mock_class,
        ):
            result = load_capability_from_stored(stored)
        assert result is mock_class

    def test_none_on_compile_failure(self):
        stored = _make_stored()
        with patch(
            "elle.capabilities.autogen.loader.compile_capability_class",
            return_value=None,
        ):
            result = load_capability_from_stored(stored)
        assert result is None

    def test_exception_returns_none(self):
        stored = _make_stored()
        with patch(
            "elle.capabilities.autogen.loader.compile_capability_class",
            side_effect=RuntimeError("boom"),
        ):
            result = load_capability_from_stored(stored)
        assert result is None


# ---------------------------------------------------------------------------
# _compile_core_capability
# ---------------------------------------------------------------------------

class TestCompileCoreCapability:

    def test_compile_finds_class_by_name(self):
        code = "class PkgActionCapability:\n    x = 1"
        stored = _make_stored(
            trust_level=TrustLevel.CORE,
            capability_name="pkg.action",
        )
        stored_mut = StoredCapability(
            **{**stored.model_dump(), "capability_class_code": code}
        )
        result = _compile_core_capability(stored_mut)
        assert result is not None
        assert result.__name__ == "PkgActionCapability"

    def test_compile_finds_alternative_class(self):
        code = "class MySpecialCapability:\n    pass"
        stored = _make_stored(capability_name="some.other")
        stored_mut = StoredCapability(
            **{**stored.model_dump(), "capability_class_code": code}
        )
        result = _compile_core_capability(stored_mut)
        assert result is not None
        assert result.__name__ == "MySpecialCapability"

    def test_compile_no_capability_class_found(self):
        code = "class SomeHelper:\n    pass"
        stored = _make_stored(capability_name="pkg.action")
        stored_mut = StoredCapability(
            **{**stored.model_dump(), "capability_class_code": code}
        )
        result = _compile_core_capability(stored_mut)
        assert result is None

    def test_compile_syntax_error(self):
        stored = _make_stored()
        stored_mut = StoredCapability(
            **{**stored.model_dump(), "capability_class_code": "def ===INVALID"}
        )
        result = _compile_core_capability(stored_mut)
        assert result is None


# ---------------------------------------------------------------------------
# load_all_approved
# ---------------------------------------------------------------------------

class TestLoadAllApproved:

    def test_loads_and_registers(self):
        stored = _make_stored()
        mock_store = MagicMock()
        mock_store.list_approved_enabled.return_value = [stored]

        mock_registry = MagicMock()
        mock_class = type("Cap", (), {})

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ), patch(
            "elle.capabilities.autogen.loader.load_capability_from_stored",
            return_value=mock_class,
        ):
            count = load_all_approved(mock_registry)
        assert count == 1
        mock_registry.register.assert_called_once()

    def test_loads_without_registry(self):
        stored = _make_stored()
        mock_store = MagicMock()
        mock_store.list_approved_enabled.return_value = [stored]
        mock_class = type("Cap", (), {})

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ), patch(
            "elle.capabilities.autogen.loader.load_capability_from_stored",
            return_value=mock_class,
        ):
            count = load_all_approved(None)
        assert count == 1

    def test_skips_failed_loads(self):
        stored = _make_stored()
        mock_store = MagicMock()
        mock_store.list_approved_enabled.return_value = [stored]

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ), patch(
            "elle.capabilities.autogen.loader.load_capability_from_stored",
            return_value=None,
        ):
            count = load_all_approved(None)
        assert count == 0

    def test_handles_registration_failure(self):
        stored = _make_stored()
        mock_store = MagicMock()
        mock_store.list_approved_enabled.return_value = [stored]
        mock_registry = MagicMock()
        mock_registry.register.side_effect = RuntimeError("register fail")
        mock_class = type("Cap", (), {})

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ), patch(
            "elle.capabilities.autogen.loader.load_capability_from_stored",
            return_value=mock_class,
        ):
            count = load_all_approved(mock_registry)
        assert count == 0


# ---------------------------------------------------------------------------
# load_capability_by_name
# ---------------------------------------------------------------------------

class TestLoadCapabilityByName:

    def test_found_and_approved(self):
        stored = _make_stored()
        mock_store = MagicMock()
        mock_store.get.return_value = stored
        mock_class = type("Cap", (), {})

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ), patch(
            "elle.capabilities.autogen.loader.load_capability_from_stored",
            return_value=mock_class,
        ):
            result = load_capability_by_name("pkg.action")
        assert result is mock_class

    def test_not_found(self):
        mock_store = MagicMock()
        mock_store.get.return_value = None

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ):
            result = load_capability_by_name("nonexistent")
        assert result is None

    def test_not_approved(self):
        stored = _make_stored(approved=False)
        mock_store = MagicMock()
        mock_store.get.return_value = stored

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ):
            result = load_capability_by_name("pkg.action")
        assert result is None

    def test_not_enabled(self):
        stored = _make_stored(enabled=False)
        mock_store = MagicMock()
        mock_store.get.return_value = stored

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ):
            result = load_capability_by_name("pkg.action")
        assert result is None


# ---------------------------------------------------------------------------
# register_autogen_capabilities
# ---------------------------------------------------------------------------

class TestRegisterAutogenCapabilities:

    def test_delegates_to_load_all_approved(self):
        mock_registry = MagicMock()
        with patch(
            "elle.capabilities.autogen.loader.load_all_approved", return_value=5
        ) as mock_load:
            count = register_autogen_capabilities(mock_registry)
        assert count == 5
        mock_load.assert_called_once_with(mock_registry)


# ---------------------------------------------------------------------------
# get_loaded_autogen_capabilities
# ---------------------------------------------------------------------------

class TestGetLoadedAutogenCapabilities:

    def test_returns_capability_info(self):
        stored = _make_stored()
        mock_store = MagicMock()
        mock_store.list_approved_enabled.return_value = [stored]

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ):
            caps = get_loaded_autogen_capabilities()
        assert len(caps) == 1
        assert caps[0]["name"] == "pkg.action"

    def test_skips_on_error(self):
        stored = _make_stored()
        # Make spec_json invalid
        stored_bad = StoredCapability(
            **{**stored.model_dump(), "spec_json": "INVALID"}
        )
        mock_store = MagicMock()
        mock_store.list_approved_enabled.return_value = [stored_bad]

        with patch(
            "elle.capabilities.autogen.loader.get_store", return_value=mock_store
        ):
            caps = get_loaded_autogen_capabilities()
        assert len(caps) == 0


# ---------------------------------------------------------------------------
# create_capability_instance / execute_autogen_capability
# ---------------------------------------------------------------------------

class TestInstanceCreation:

    def test_create_instance(self):
        mock_class = MagicMock(return_value="instance")
        with patch(
            "elle.capabilities.autogen.loader.load_capability_by_name",
            return_value=mock_class,
        ):
            result = create_capability_instance("pkg.action")
        assert result == "instance"

    def test_create_instance_not_found(self):
        with patch(
            "elle.capabilities.autogen.loader.load_capability_by_name",
            return_value=None,
        ):
            result = create_capability_instance("nonexistent")
        assert result is None

    def test_execute_success(self):
        mock_instance = MagicMock()
        mock_instance.run.return_value = {"success": True, "output": "ok"}

        with patch(
            "elle.capabilities.autogen.loader.create_capability_instance",
            return_value=mock_instance,
        ):
            result = execute_autogen_capability("pkg.action", {"arg": "val"})
        assert result is not None
        assert result["success"] is True

    def test_execute_not_found(self):
        with patch(
            "elle.capabilities.autogen.loader.create_capability_instance",
            return_value=None,
        ):
            result = execute_autogen_capability("nonexistent", {})
        assert result is None

    def test_execute_exception(self):
        mock_instance = MagicMock()
        mock_instance.run.side_effect = RuntimeError("exec fail")

        with patch(
            "elle.capabilities.autogen.loader.create_capability_instance",
            return_value=mock_instance,
        ):
            result = execute_autogen_capability("pkg.action", {})
        assert result is not None
        assert result["success"] is False

    def test_execute_returns_none_result(self):
        mock_instance = MagicMock()
        mock_instance.run.return_value = None

        with patch(
            "elle.capabilities.autogen.loader.create_capability_instance",
            return_value=mock_instance,
        ):
            result = execute_autogen_capability("pkg.action", {})
        assert result is None
