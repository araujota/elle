"""Tests for editing __init__.py select_tier convenience function coverage.

Covers lines 148-149: select_tier() convenience function.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.ops.editing import EditOperation, TierSelector, select_tier


class TestSelectTierConvenience:
    """Tests for the select_tier() convenience function."""

    def test_select_tier_delegates_to_tier_selector(self):
        """select_tier creates a TierSelector and calls its select_tier method."""
        op = EditOperation(kind="set", path=".key", value="val")
        mock_selection = MagicMock()

        with patch.object(TierSelector, "select_tier", return_value=mock_selection) as mock_method:
            result = select_tier("/etc/docker/daemon.json", op)

        mock_method.assert_called_once_with(
            "/etc/docker/daemon.json",
            op,
            max_tier=5,
            preferred_tier=None,
        )
        assert result is mock_selection

    def test_select_tier_passes_max_tier(self):
        """select_tier passes max_tier argument correctly."""
        op = EditOperation(kind="set", path=".key", value="val")
        mock_selection = MagicMock()

        with patch.object(TierSelector, "select_tier", return_value=mock_selection) as mock_method:
            result = select_tier("/etc/docker/daemon.json", op, max_tier=3)

        mock_method.assert_called_once_with(
            "/etc/docker/daemon.json",
            op,
            max_tier=3,
            preferred_tier=None,
        )
        assert result is mock_selection

    def test_select_tier_passes_preferred_tier(self):
        """select_tier passes preferred_tier argument correctly."""
        op = EditOperation(kind="set", path=".key", value="val")
        mock_selection = MagicMock()

        with patch.object(TierSelector, "select_tier", return_value=mock_selection) as mock_method:
            result = select_tier("/etc/docker/daemon.json", op, preferred_tier=2)

        mock_method.assert_called_once_with(
            "/etc/docker/daemon.json",
            op,
            max_tier=5,
            preferred_tier=2,
        )
        assert result is mock_selection
