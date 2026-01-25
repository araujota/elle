"""Tests for autogen store module."""

from __future__ import annotations

import pytest
import tempfile
from pathlib import Path

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    InputFieldSpec,
    TrustLevel,
)
from elle.capabilities.autogen.store import AutogenStore, get_store, reset_store


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield Path(f.name)
    # Cleanup after test
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def store(temp_db):
    """Create a store with temporary database."""
    return AutogenStore(temp_db)


@pytest.fixture
def sample_spec():
    """Create a sample capability spec."""
    return GeneratedCapabilitySpec(
        name="test.capability",
        display_name="Test Capability",
        description="A test capability",
        domain="file",
        risk_level="low",
        side_effects=("file_write",),
        input_fields=(
            InputFieldSpec(name="path", field_type="str", description="File path"),
        ),
        command_template="test {path}",
        source_command="test",
        confidence_score=0.8,
    )


class TestAutogenStore:
    """Tests for AutogenStore."""

    def test_init(self, store, temp_db):
        """Test store initialization."""
        assert store.db_path == temp_db

    def test_save_and_get(self, store, sample_spec):
        """Test saving and retrieving capability."""
        cap_id = store.save(
            spec=sample_spec,
            input_model_code="class Input: pass",
            output_model_code="class Output: pass",
            capability_class_code="class Cap: pass",
            man_page_text="Test man page content",
            trust_level=TrustLevel.OFFICIAL,
        )

        assert cap_id is not None

        # Retrieve
        stored = store.get("test.capability")
        assert stored is not None
        assert stored.capability_name == "test.capability"
        assert stored.trust_level == TrustLevel.OFFICIAL

    def test_get_nonexistent(self, store):
        """Test getting nonexistent capability."""
        result = store.get("nonexistent")
        assert result is None

    def test_list_all(self, store, sample_spec):
        """Test listing all capabilities."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )

        all_caps = store.list_all()
        assert len(all_caps) == 1
        assert all_caps[0].capability_name == "test.capability"

    def test_approve(self, store, sample_spec):
        """Test approving capability."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )

        # Initially not approved
        stored = store.get("test.capability")
        assert stored.approved is False

        # Approve
        result = store.approve("test.capability")
        assert result is True

        # Check approved
        stored = store.get("test.capability")
        assert stored.approved is True

    def test_disable(self, store, sample_spec):
        """Test disabling capability."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )

        # Initially enabled
        stored = store.get("test.capability")
        assert stored.enabled is True

        # Disable
        result = store.disable("test.capability")
        assert result is True

        # Check disabled
        stored = store.get("test.capability")
        assert stored.enabled is False

    def test_enable(self, store, sample_spec):
        """Test enabling capability."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )
        store.disable("test.capability")

        result = store.enable("test.capability")
        assert result is True

        stored = store.get("test.capability")
        assert stored.enabled is True

    def test_delete(self, store, sample_spec):
        """Test deleting capability."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )

        result = store.delete("test.capability")
        assert result is True

        # Should be gone
        stored = store.get("test.capability")
        assert stored is None

    def test_delete_nonexistent(self, store):
        """Test deleting nonexistent capability."""
        result = store.delete("nonexistent")
        assert result is False

    def test_list_approved_enabled(self, store, sample_spec):
        """Test listing approved and enabled capabilities."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )

        # Not approved yet
        approved = store.list_approved_enabled()
        assert len(approved) == 0

        # Approve
        store.approve("test.capability")

        approved = store.list_approved_enabled()
        assert len(approved) == 1

    def test_list_pending_approval(self, store, sample_spec):
        """Test listing pending capabilities."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
            trust_level=TrustLevel.THIRD_PARTY,
        )

        pending = store.list_pending_approval()
        assert len(pending) == 1

        # Approve
        store.approve("test.capability")

        pending = store.list_pending_approval()
        assert len(pending) == 0

    def test_check_needs_regeneration(self, store, sample_spec):
        """Test checking for regeneration need."""
        store.save(
            spec=sample_spec,
            input_model_code="",
            output_model_code="",
            capability_class_code="",
            man_page_text="original content",
        )

        # Same content - no regeneration
        result = store.check_needs_regeneration("test.capability", "original content")
        assert result is False

        # Different content - needs regeneration
        result = store.check_needs_regeneration("test.capability", "new content")
        assert result is True

        # Nonexistent - needs generation
        result = store.check_needs_regeneration("nonexistent", "any content")
        assert result is True

    def test_update_existing(self, store, sample_spec):
        """Test updating existing capability."""
        store.save(
            spec=sample_spec,
            input_model_code="v1",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )

        # Update
        store.save(
            spec=sample_spec,
            input_model_code="v2",
            output_model_code="",
            capability_class_code="",
            man_page_text="",
        )

        # Should still be one entry
        all_caps = store.list_all()
        assert len(all_caps) == 1

        # Should have updated code
        stored = store.get("test.capability")
        assert stored.input_model_code == "v2"


class TestGetStore:
    """Tests for get_store function."""

    def test_get_store_singleton(self, temp_db):
        """Test store singleton behavior."""
        reset_store()

        store1 = get_store(temp_db)
        store2 = get_store(temp_db)

        assert store1 is store2

        reset_store()

    def test_reset_store(self, temp_db):
        """Test store reset."""
        reset_store()
        store1 = get_store(temp_db)
        reset_store()
        store2 = get_store(temp_db)

        assert store1 is not store2
