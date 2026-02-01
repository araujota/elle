"""Tests for editing tiers __init__.py coverage.

Covers get_tier_class() and get_tier_instance() functions (lines 43, 55).
"""

from __future__ import annotations

import pytest

from elle.ops.editing.tiers import (
    TIER_CLASSES,
    BaseTier,
    Tier0DropIn,
    Tier1Augeas,
    Tier2Structured,
    Tier3Format,
    Tier4Document,
    Tier5Patch,
    get_tier_class,
    get_tier_instance,
)


class TestGetTierClass:
    """Tests for get_tier_class()."""

    def test_returns_correct_class_for_each_tier(self):
        """get_tier_class returns the correct class for tiers 0-5."""
        expected = {
            0: Tier0DropIn,
            1: Tier1Augeas,
            2: Tier2Structured,
            3: Tier3Format,
            4: Tier4Document,
            5: Tier5Patch,
        }
        for tier_num, expected_cls in expected.items():
            assert get_tier_class(tier_num) is expected_cls

    def test_invalid_tier_raises_key_error(self):
        """get_tier_class raises KeyError for invalid tier numbers."""
        with pytest.raises(KeyError):
            get_tier_class(99)


class TestGetTierInstance:
    """Tests for get_tier_instance()."""

    def test_returns_instance_of_correct_class(self):
        """get_tier_instance returns an instance of the correct tier class."""
        for tier_num in range(6):
            instance = get_tier_instance(tier_num)
            assert isinstance(instance, BaseTier)
            assert isinstance(instance, TIER_CLASSES[tier_num])

    def test_returns_new_instance_each_call(self):
        """get_tier_instance returns a new instance each time."""
        a = get_tier_instance(0)
        b = get_tier_instance(0)
        assert a is not b

    def test_invalid_tier_raises_key_error(self):
        """get_tier_instance raises KeyError for invalid tier numbers."""
        with pytest.raises(KeyError):
            get_tier_instance(99)
