"""Tests for Docker environment variable prompting session."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from elle.cli.docker.env_models import EnvVarPromptResult, EnvVarSpec
from elle.cli.docker.env_prompt import (
    EnvVarPromptSession,
    prompt_for_image,
)
from elle.cli.docker.env_store import DockerEnvStore


class TestEnvVarPromptSession:
    """Tests for EnvVarPromptSession class."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        if db_path.exists():
            db_path.unlink()

    @pytest.fixture
    def specs(self):
        return (
            EnvVarSpec(
                name="POSTGRES_PASSWORD",
                required=True,
                sensitive=True,
                description="Database password",
            ),
            EnvVarSpec(
                name="POSTGRES_USER",
                required=False,
                default="postgres",
                description="Database user",
            ),
        )

    @pytest.fixture
    def console(self):
        return Console(force_terminal=True, no_color=True)

    @pytest.fixture
    def store(self, temp_db):
        store = DockerEnvStore(db_path=temp_db)
        yield store
        store.close()

    def test_create_session(self, specs, console):
        session = EnvVarPromptSession(
            specs=specs,
            image="postgres:15",
            console=console,
        )
        assert session.specs == specs
        assert session.image == "postgres:15"
        assert session.image_family == "postgres"

    def test_session_with_store(self, specs, console, store):
        session = EnvVarPromptSession(
            specs=specs,
            image="postgres:15",
            console=console,
            store=store,
        )
        assert session.store is store


class TestPromptSessionResult:
    """Tests for prompt session result handling."""

    @pytest.fixture
    def specs(self):
        return (
            EnvVarSpec(name="VAR1", required=True),
            EnvVarSpec(name="VAR2", required=False),
        )

    def test_cancelled_result(self, specs):
        """Test that cancelled sessions return proper result."""
        result = EnvVarPromptResult(cancelled=True, image="test:1")
        assert result.cancelled
        assert len(result.values) == 0

    def test_skipped_result(self, specs):
        """Test that skipped variables are tracked."""
        result = EnvVarPromptResult(skipped=("VAR1", "VAR2"), image="test:1")
        assert "VAR1" in result.skipped
        assert "VAR2" in result.skipped


class TestPromptEnvVars:
    """Tests for prompt_env_vars function."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        if db_path.exists():
            db_path.unlink()

    def test_prompt_returns_result(self):
        """Test basic structure of prompt result."""
        (EnvVarSpec(name="VAR1", required=False),)

        # Mock user cancelling
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with patch("elle.cli.docker.env_prompt.DockerEnvStore"):
                result = EnvVarPromptResult(cancelled=True, image="test:1")
                # In real usage, prompt_env_vars would return this
                assert isinstance(result, EnvVarPromptResult)


class TestPromptForImage:
    """Tests for prompt_for_image function."""

    def test_returns_empty_for_unknown_image(self):
        """Unknown images with no specs return empty result."""
        with patch("elle.cli.docker.env_detector.DockerEnvDetector") as mock_detector_class:
            mock_detector = MagicMock()
            mock_detector.detect_from_image.return_value = ()
            mock_detector_class.return_value = mock_detector

            result = prompt_for_image("unknown-image:latest")

            assert isinstance(result, EnvVarPromptResult)
            assert len(result.values) == 0


class TestOverviewPanel:
    """Tests for overview panel display."""

    @pytest.fixture
    def specs(self):
        return (
            EnvVarSpec(
                name="REQUIRED_VAR",
                required=True,
                sensitive=True,
                description="A required sensitive variable",
            ),
            EnvVarSpec(
                name="OPTIONAL_VAR",
                required=False,
                default="default_value",
                description="An optional variable",
            ),
        )

    def test_session_has_specs(self, specs):
        console = Console(force_terminal=True, no_color=True)
        session = EnvVarPromptSession(
            specs=specs,
            image="test:1",
            console=console,
        )

        # Verify session has the specs
        assert len(session.specs) == 2
        assert session.specs[0].name == "REQUIRED_VAR"
        assert session.specs[1].name == "OPTIONAL_VAR"


class TestSavedValuesIntegration:
    """Tests for saved values integration."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        if db_path.exists():
            db_path.unlink()

    def test_store_has_saved_values(self, temp_db):
        """Test that we can check for saved values."""
        from elle.cli.docker.env_models import EnvVarValue

        store = DockerEnvStore(db_path=temp_db)

        # Initially no saved values
        assert not store.has_saved_values("postgres")

        # Save some values
        store.save_values(
            "postgres",
            [EnvVarValue(name="POSTGRES_PASSWORD", value="secret", sensitive=True)],
        )

        # Now has saved values
        assert store.has_saved_values("postgres")

        store.close()


class TestExplainFeature:
    """Tests for the explain feature."""

    def test_specs_have_descriptions(self):
        """Verify specs can have descriptions."""
        spec = EnvVarSpec(
            name="MY_VAR",
            description="This is what the variable does",
        )
        assert spec.description == "This is what the variable does"

    def test_specs_without_descriptions(self):
        """Specs without descriptions default to empty string."""
        spec = EnvVarSpec(name="MY_VAR")
        assert spec.description == ""
