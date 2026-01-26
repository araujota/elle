"""Tests for /map REPL commands (GUI tree traces).

Renamed from test_learn_commands.py as part of the /learn -> /map rename.
"""

from unittest.mock import MagicMock, patch

import pytest

from elle.cli.map_commands import (
    _get_help,
    _handle_delete,
    _handle_list,
    _handle_show,
    handle_map_command,
)


class TestMapCommandHelp:
    """Tests for /map help."""

    def test_get_help(self):
        """Test help text generation."""
        help_text = _get_help()

        assert "/map" in help_text
        assert "Usage:" in help_text
        assert "Examples:" in help_text
        assert "gnome-control-center" in help_text

    @pytest.mark.asyncio
    async def test_empty_args_returns_help(self):
        """Test that empty args returns help."""
        result = await handle_map_command("")

        assert "/map" in result
        assert "Usage:" in result


class TestMapCommandRouting:
    """Tests for command routing."""

    @pytest.mark.asyncio
    async def test_help_subcommand(self):
        """Test /map help subcommand."""
        result = await handle_map_command("help")

        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_list_subcommand_empty(self):
        """Test /map list with no recipes."""
        with patch("elle.atspi.list_recipes", return_value=[]):
            result = await _handle_list("")

            assert "No mapped applications" in result

    @pytest.mark.asyncio
    async def test_list_subcommand_with_recipes(self):
        """Test /map list with recipes."""
        mock_recipe = MagicMock()
        mock_recipe.app_name = "gnome-settings"
        mock_recipe.version = 1
        mock_recipe.confidence = 0.95
        mock_recipe.success_count = 10
        mock_recipe.failure_count = 2

        with patch("elle.atspi.list_recipes", return_value=[mock_recipe]):
            result = await _handle_list("")

            assert "Mapped applications" in result
            assert "gnome-settings" in result

    @pytest.mark.asyncio
    async def test_show_no_args(self):
        """Test /map show without app name."""
        result = await _handle_show("")

        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_show_not_found(self):
        """Test /map show for non-existent app."""
        with patch("elle.atspi.get_recipe_by_app", return_value=None):
            result = await _handle_show("nonexistent")

            assert "No recipe found" in result

    @pytest.mark.asyncio
    async def test_delete_no_args(self):
        """Test /map delete without app name."""
        result = await _handle_delete("")

        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        """Test /map delete for non-existent app."""
        with patch("elle.atspi.get_recipe_by_app", return_value=None):
            result = await _handle_delete("nonexistent")

            assert "No recipe found" in result


class TestMapCommandMap:
    """Tests for mapping applications."""

    @pytest.mark.asyncio
    async def test_map_atspi_unavailable(self):
        """Test mapping when AT-SPI is not available."""
        with patch("elle.atspi.is_atspi_available", return_value=False):
            result = await handle_map_command("gnome-settings")

            assert "AT-SPI is not available" in result

    @pytest.mark.asyncio
    async def test_map_app_not_found(self):
        """Test mapping when app is not found."""
        with patch("elle.atspi.is_atspi_available", return_value=True):
            with patch("elle.atspi.learn_application", return_value=None):
                result = await handle_map_command("nonexistent-app")

                assert "Could not find application" in result

    @pytest.mark.asyncio
    async def test_map_success(self):
        """Test successful mapping."""
        mock_recipe = MagicMock()
        mock_recipe.app_name = "gnome-settings"
        mock_recipe.recipe_id = "recipe-123"
        mock_recipe.elements = [1, 2, 3]  # 3 elements
        mock_recipe.navigation_paths = {"a": (0,), "b": (1,)}  # 2 paths
        mock_recipe.version = 1
        mock_recipe.confidence = 1.0

        with patch("elle.atspi.is_atspi_available", return_value=True):
            with patch("elle.atspi.learn_application", return_value=mock_recipe):
                result = await handle_map_command("gnome-settings")

                assert "Mapped" in result
                assert "gnome-settings" in result
                assert "Elements: 3" in result

    @pytest.mark.asyncio
    async def test_map_passive_mode(self):
        """Test mapping with --passive flag."""
        mock_recipe = MagicMock()
        mock_recipe.app_name = "test"
        mock_recipe.recipe_id = "id"
        mock_recipe.elements = []
        mock_recipe.navigation_paths = {}
        mock_recipe.version = 1
        mock_recipe.confidence = 1.0

        with patch("elle.atspi.is_atspi_available", return_value=True):
            with patch("elle.atspi.learn_application", return_value=mock_recipe) as mock_map:
                await handle_map_command("test --passive")

                # Verify passive mode was used
                mock_map.assert_called_once()
                assert mock_map.call_args[1].get("mode") == "passive" or mock_map.call_args[0][1] == "passive"


class TestMapCommandShow:
    """Tests for showing recipe details."""

    @pytest.mark.asyncio
    async def test_show_recipe_details(self):
        """Test showing recipe details."""
        from datetime import datetime

        from elle.atspi.models import UIElement

        mock_recipe = MagicMock()
        mock_recipe.app_name = "gnome-settings"
        mock_recipe.recipe_id = "recipe-123"
        mock_recipe.version = 2
        mock_recipe.learn_method = "passive"
        mock_recipe.root_window = "Settings"
        mock_recipe.app_desktop_id = "org.gnome.Settings.desktop"
        mock_recipe.elements = [
            UIElement(role="button", name="OK", path=(0,)),
            UIElement(role="button", name="Cancel", path=(1,)),
        ]
        mock_recipe.navigation_paths = {"ok": (0,)}
        mock_recipe.confidence = 0.95
        mock_recipe.success_count = 10
        mock_recipe.failure_count = 1
        mock_recipe.created_at = datetime.utcnow()
        mock_recipe.updated_at = datetime.utcnow()
        mock_recipe.last_success_at = datetime.utcnow()

        with patch("elle.atspi.get_recipe_by_app", return_value=mock_recipe):
            result = await _handle_show("gnome-settings")

            assert "Recipe: gnome-settings" in result
            assert "Version: 2" in result
            assert "Elements: 2" in result
            assert "Confidence: 95%" in result


class TestMapCommandExportImport:
    """Tests for export/import functionality."""

    @pytest.mark.asyncio
    async def test_export_no_args(self):
        """Test export without app name."""
        result = await handle_map_command("export")

        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_import_no_file(self):
        """Test import without file path."""
        result = await handle_map_command("import")

        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_import_file_not_found(self):
        """Test import with non-existent file."""
        result = await handle_map_command("import /nonexistent/file.json")

        assert "File not found" in result


class TestMapCommandRebuild:
    """Tests for rebuild functionality."""

    @pytest.mark.asyncio
    async def test_rebuild_no_args(self):
        """Test rebuild without app name."""
        with patch("elle.atspi.is_atspi_available", return_value=True):
            result = await handle_map_command("rebuild")

            assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_rebuild_no_existing(self):
        """Test rebuild when no existing recipe."""
        with patch("elle.atspi.is_atspi_available", return_value=True):
            with patch("elle.atspi.get_recipe_by_app", return_value=None):
                result = await handle_map_command("rebuild nonexistent")

                assert "No existing recipe" in result
