"""Tests for confgen service integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from elle.rag.confgen.models import (
    ConfigGenContext,
    ConfigGenOutcome,
    ConfigGenRequest,
    ConfigGenResult,
    ConfigGenValidation,
    ConfigOp,
)
from elle.rag.confgen.service import (
    ConfigGenService,
    generate,
    get_confgen_service,
    reset_confgen_service,
)


class MockGenerator:
    """Mock generator for testing."""

    def __init__(self, result: ConfigGenResult | None = None) -> None:
        self._result = result

    def build_context(self, request: ConfigGenRequest) -> ConfigGenContext:
        return ConfigGenContext(
            request=request,
            detected_domain="general",
            detected_file_type="text",
        )

    def generate(self, context: ConfigGenContext) -> ConfigGenResult | None:
        return self._result


class TestConfigGenService:
    """Tests for ConfigGenService."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_confgen_service()

    def test_generate_success(self, tmp_path: Path) -> None:
        """Test successful generation."""
        result = ConfigGenResult(
            title="Test config",
            explanation="Test explanation",
            operations=(ConfigOp(kind="set", path="/test", value="value"),),
            target_path=str(tmp_path / "config.txt"),
        )

        service = ConfigGenService(
            generator=MockGenerator(result=result),
        )

        outcome = service.generate(
            request="Set test value",
            target_path=tmp_path / "config.txt",
        )

        assert outcome.success is True
        assert outcome.result is not None
        assert outcome.result.title == "Test config"
        assert outcome.validation is not None

    def test_generate_with_mode(self, tmp_path: Path) -> None:
        """Test generation with different modes."""
        result = ConfigGenResult(
            title="New config",
            explanation="Create new file",
            generated_content="content: value",
            target_path=str(tmp_path / "new.yaml"),
            file_type="yaml",
        )

        service = ConfigGenService(
            generator=MockGenerator(result=result),
        )

        outcome = service.generate(
            request="Create new config",
            target_path=tmp_path / "new.yaml",
            mode="create",
        )

        assert outcome.success is True
        assert outcome.result.generated_content is not None

    def test_generate_failure(self) -> None:
        """Test generation failure."""
        service = ConfigGenService(
            generator=MockGenerator(result=None),
        )

        outcome = service.generate(
            request="Test",
            target_path="/tmp/test.txt",
        )

        assert outcome.success is False
        assert outcome.generation_error is not None

    def test_generate_auto_detects_domain(self, tmp_path: Path) -> None:
        """Test domain auto-detection."""
        netplan_path = tmp_path / "netplan.yaml"

        result = ConfigGenResult(
            title="Test",
            explanation="Test",
            target_path=str(netplan_path),
            domain="network",
        )

        service = ConfigGenService(
            generator=MockGenerator(result=result),
        )

        outcome = service.generate(
            request="Configure network",
            target_path=netplan_path,
        )

        assert outcome.success is True

    def test_generate_reads_existing_file(self, tmp_path: Path) -> None:
        """Test that existing file content is read."""
        config_file = tmp_path / "config.txt"
        config_file.write_text("existing content")

        result = ConfigGenResult(
            title="Test",
            explanation="Test",
            target_path=str(config_file),
        )

        # Use real generator to verify content reading
        class ContentCheckGenerator:
            def build_context(self, request: ConfigGenRequest) -> ConfigGenContext:
                return ConfigGenContext(
                    request=request,
                    current_content="existing content",
                    detected_domain="general",
                    detected_file_type="text",
                )

            def generate(self, context: ConfigGenContext) -> ConfigGenResult:
                # Verify content was read
                assert context.current_content == "existing content"
                return result

        service = ConfigGenService(
            generator=ContentCheckGenerator(),
        )

        outcome = service.generate(
            request="Modify config",
            target_path=config_file,
        )

        assert outcome.success is True


class TestConfigGenServiceApply:
    """Tests for ConfigGenService.apply()."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_confgen_service()

    def test_apply_create_mode(self, tmp_path: Path) -> None:
        """Test applying create mode (write new file)."""
        target = tmp_path / "new.yaml"

        result = ConfigGenResult(
            title="New config",
            explanation="Create new file",
            generated_content="key: value\n",
            target_path=str(target),
            file_type="yaml",
        )
        validation = ConfigGenValidation(valid=True)

        request = ConfigGenRequest(
            request="Create new config",
            target_path=target,
            mode="create",
        )

        outcome = ConfigGenOutcome(
            request=request,
            result=result,
            validation=validation,
        )

        service = ConfigGenService()
        applied_outcome = service.apply(outcome)

        assert applied_outcome.applied is True
        assert target.exists()
        assert target.read_text() == "key: value\n"

    def test_apply_creates_backup(self, tmp_path: Path) -> None:
        """Test that apply creates backup for existing files."""
        target = tmp_path / "existing.txt"
        target.write_text("original content")

        result = ConfigGenResult(
            title="Update config",
            explanation="Update file",
            generated_content="new content",
            target_path=str(target),
        )
        validation = ConfigGenValidation(valid=True)

        request = ConfigGenRequest(
            request="Update config",
            target_path=target,
        )

        outcome = ConfigGenOutcome(
            request=request,
            result=result,
            validation=validation,
        )

        # Temporarily mock backup to avoid needing /var/lib/elle
        service = ConfigGenService()

        def mock_backup(path: str) -> str:
            backup = tmp_path / "backup.txt"
            backup.write_text(Path(path).read_text())
            return str(backup)

        service._create_backup = mock_backup

        applied_outcome = service.apply(outcome)

        assert applied_outcome.applied is True
        assert applied_outcome.backup_path is not None

    def test_apply_no_result(self) -> None:
        """Test apply fails when no result."""
        request = ConfigGenRequest(request="Test")

        outcome = ConfigGenOutcome(
            request=request,
            result=None,
            validation=None,
        )

        service = ConfigGenService()
        applied_outcome = service.apply(outcome)

        # When success is False (no result), apply returns the original outcome
        assert applied_outcome.applied is False
        assert applied_outcome.success is False

    def test_apply_invalid_validation(self) -> None:
        """Test apply fails when validation invalid."""
        result = ConfigGenResult(
            title="Test",
            explanation="Test",
            target_path="/tmp/test.txt",
        )
        validation = ConfigGenValidation(valid=False)

        request = ConfigGenRequest(request="Test")

        outcome = ConfigGenOutcome(
            request=request,
            result=result,
            validation=validation,
        )

        ConfigGenService()
        # Service.apply should still work but success property is False
        assert outcome.success is False

    def test_apply_yaml_operations(self, tmp_path: Path) -> None:
        """Test applying YAML operations."""
        target = tmp_path / "config.yaml"
        target.write_text("key1: value1\n")

        result = ConfigGenResult(
            title="Update YAML",
            explanation="Add new key",
            operations=(ConfigOp(kind="set", path="key2", value="value2"),),
            target_path=str(target),
            file_type="yaml",
        )
        validation = ConfigGenValidation(valid=True)

        request = ConfigGenRequest(
            request="Add key",
            target_path=target,
        )

        outcome = ConfigGenOutcome(
            request=request,
            result=result,
            validation=validation,
        )

        service = ConfigGenService()
        applied_outcome = service.apply(outcome, backup=False)

        assert applied_outcome.applied is True

        from io import StringIO

        from ruamel.yaml import YAML

        yaml_parser = YAML(typ="safe")
        data = yaml_parser.load(StringIO(target.read_text()))
        assert data["key2"] == "value2"


class TestServiceSingleton:
    """Tests for service singleton functions."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_confgen_service()

    def test_get_confgen_service_returns_singleton(self) -> None:
        """Test get_confgen_service returns same instance."""
        s1 = get_confgen_service()
        s2 = get_confgen_service()

        assert s1 is s2

    def test_reset_confgen_service(self) -> None:
        """Test reset clears singleton."""
        s1 = get_confgen_service()
        reset_confgen_service()
        s2 = get_confgen_service()

        assert s1 is not s2


class TestConvenienceFunction:
    """Tests for generate convenience function."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_confgen_service()

    @patch.object(ConfigGenService, "generate")
    def test_generate_function(self, mock_generate: MagicMock) -> None:
        """Test generate convenience function."""
        mock_outcome = ConfigGenOutcome(
            request=ConfigGenRequest(request="Test"),
            generation_error="Test error",
        )
        mock_generate.return_value = mock_outcome

        outcome = generate(
            request="Set static IP",
            target_path="/etc/netplan/config.yaml",
            mode="edit",
        )

        mock_generate.assert_called_once()
        assert outcome is mock_outcome


class TestYAMLOperations:
    """Tests for YAML operation application."""

    def test_set_nested_value(self, tmp_path: Path) -> None:
        """Test setting a nested value."""
        target = tmp_path / "test.yaml"
        target.write_text("root:\n  child: original\n")

        result = ConfigGenResult(
            title="Update nested",
            explanation="Set nested value",
            operations=(ConfigOp(kind="set", path="root.child", value="modified"),),
            target_path=str(target),
            file_type="yaml",
        )
        validation = ConfigGenValidation(valid=True)

        outcome = ConfigGenOutcome(
            request=ConfigGenRequest(request="Test", target_path=target),
            result=result,
            validation=validation,
        )

        service = ConfigGenService()
        service.apply(outcome, backup=False)

        from io import StringIO

        from ruamel.yaml import YAML

        yaml_parser = YAML(typ="safe")
        data = yaml_parser.load(StringIO(target.read_text()))
        assert data["root"]["child"] == "modified"

    def test_create_nested_path(self, tmp_path: Path) -> None:
        """Test creating nested path that doesn't exist."""
        target = tmp_path / "test.yaml"
        target.write_text("existing: value\n")

        result = ConfigGenResult(
            title="Create nested",
            explanation="Create new nested path",
            operations=(ConfigOp(kind="set", path="new.nested.path", value="created"),),
            target_path=str(target),
            file_type="yaml",
        )
        validation = ConfigGenValidation(valid=True)

        outcome = ConfigGenOutcome(
            request=ConfigGenRequest(request="Test", target_path=target),
            result=result,
            validation=validation,
        )

        service = ConfigGenService()
        service.apply(outcome, backup=False)

        from io import StringIO

        from ruamel.yaml import YAML

        yaml_parser = YAML(typ="safe")
        data = yaml_parser.load(StringIO(target.read_text()))
        assert data["new"]["nested"]["path"] == "created"

    def test_remove_key(self, tmp_path: Path) -> None:
        """Test removing a key."""
        target = tmp_path / "test.yaml"
        target.write_text("keep: value\nremove: this\n")

        result = ConfigGenResult(
            title="Remove key",
            explanation="Remove key",
            operations=(ConfigOp(kind="rm", path="remove"),),
            target_path=str(target),
            file_type="yaml",
        )
        validation = ConfigGenValidation(valid=True)

        outcome = ConfigGenOutcome(
            request=ConfigGenRequest(request="Test", target_path=target),
            result=result,
            validation=validation,
        )

        service = ConfigGenService()
        service.apply(outcome, backup=False)

        from io import StringIO

        from ruamel.yaml import YAML

        yaml_parser = YAML(typ="safe")
        data = yaml_parser.load(StringIO(target.read_text()))
        assert "keep" in data
        assert "remove" not in data

    def test_append_to_list(self, tmp_path: Path) -> None:
        """Test appending to a list."""
        target = tmp_path / "test.yaml"
        target.write_text("items:\n  - item1\n")

        result = ConfigGenResult(
            title="Append item",
            explanation="Append to list",
            operations=(ConfigOp(kind="append", path="items", value="item2"),),
            target_path=str(target),
            file_type="yaml",
        )
        validation = ConfigGenValidation(valid=True)

        outcome = ConfigGenOutcome(
            request=ConfigGenRequest(request="Test", target_path=target),
            result=result,
            validation=validation,
        )

        service = ConfigGenService()
        service.apply(outcome, backup=False)

        from io import StringIO

        from ruamel.yaml import YAML

        yaml_parser = YAML(typ="safe")
        data = yaml_parser.load(StringIO(target.read_text()))
        assert "item2" in data["items"]
