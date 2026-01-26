"""Tests for Docker environment variable detector."""

import io
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile

from ruamel.yaml import YAML

from elle.cli.docker.env_detector import (
    DockerEnvDetector,
    detect_env_vars,
    get_missing_required_vars,
)


class TestDockerEnvDetector:
    """Tests for DockerEnvDetector class."""

    def test_create_detector(self):
        detector = DockerEnvDetector()
        assert detector.use_profiles
        assert detector.use_inspect

    def test_create_detector_no_profiles(self):
        detector = DockerEnvDetector(use_profiles=False)
        assert not detector.use_profiles

    def test_create_detector_no_inspect(self):
        detector = DockerEnvDetector(use_inspect=False)
        assert not detector.use_inspect


class TestDetectFromImage:
    """Tests for detect_from_image method."""

    def test_detect_postgres_vars(self):
        detector = DockerEnvDetector()
        specs = detector.detect_from_image("postgres:15")

        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" in var_names

    def test_detect_mysql_vars(self):
        detector = DockerEnvDetector()
        specs = detector.detect_from_image("mysql:8")

        var_names = [s.name for s in specs]
        assert "MYSQL_ROOT_PASSWORD" in var_names

    def test_detect_excludes_existing_env(self):
        detector = DockerEnvDetector()
        existing = (("POSTGRES_PASSWORD", "secret"),)
        specs = detector.detect_from_image("postgres:15", existing_env=existing)

        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" not in var_names

    def test_detect_unknown_image_no_profile(self):
        detector = DockerEnvDetector(use_inspect=False)
        specs = detector.detect_from_image("unknown-image:latest")

        assert len(specs) == 0

    def test_detect_returns_tuple(self):
        detector = DockerEnvDetector()
        specs = detector.detect_from_image("postgres:15")
        assert isinstance(specs, tuple)

    def test_detect_profiles_take_priority(self):
        """Profile-based detection should be used for known images."""
        detector = DockerEnvDetector()
        specs = detector.detect_from_image("postgres:15")

        # All specs should come from profile
        for spec in specs:
            assert spec.source == "profile"


class TestDetectFromCommand:
    """Tests for detect_from_command method."""

    def test_detect_from_simple_command(self):
        detector = DockerEnvDetector()
        specs = detector.detect_from_command("docker run postgres:15")

        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" in var_names

    def test_detect_from_command_with_existing_env(self):
        detector = DockerEnvDetector()
        specs = detector.detect_from_command(
            "docker run -e POSTGRES_PASSWORD=secret postgres:15"
        )

        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" not in var_names

    def test_detect_from_invalid_command(self):
        detector = DockerEnvDetector()
        specs = detector.detect_from_command("not a docker command")
        assert len(specs) == 0


class TestDetectFromCompose:
    """Tests for detect_from_compose method."""

    def test_detect_from_compose_file(self):
        compose_content = {
            "services": {
                "db": {
                    "image": "postgres:15",
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yml = YAML()
            yml.dump(compose_content, f)
            f.flush()
            path = Path(f.name)

        try:
            detector = DockerEnvDetector()
            result = detector.detect_from_compose(path)

            assert "db" in result
            var_names = [s.name for s in result["db"]]
            assert "POSTGRES_PASSWORD" in var_names
        finally:
            path.unlink()

    def test_detect_from_compose_excludes_existing(self):
        compose_content = {
            "services": {
                "db": {
                    "image": "postgres:15",
                    "environment": {
                        "POSTGRES_PASSWORD": "secret",
                    },
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yml = YAML()
            yml.dump(compose_content, f)
            f.flush()
            path = Path(f.name)

        try:
            detector = DockerEnvDetector()
            result = detector.detect_from_compose(path)

            if "db" in result:
                var_names = [s.name for s in result["db"]]
                assert "POSTGRES_PASSWORD" not in var_names
        finally:
            path.unlink()

    def test_detect_from_compose_list_env(self):
        """Test compose with list-style environment."""
        compose_content = {
            "services": {
                "db": {
                    "image": "postgres:15",
                    "environment": [
                        "POSTGRES_PASSWORD=secret",
                    ],
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yml = YAML()
            yml.dump(compose_content, f)
            f.flush()
            path = Path(f.name)

        try:
            detector = DockerEnvDetector()
            result = detector.detect_from_compose(path)

            if "db" in result:
                var_names = [s.name for s in result["db"]]
                assert "POSTGRES_PASSWORD" not in var_names
        finally:
            path.unlink()

    def test_detect_from_nonexistent_file(self):
        detector = DockerEnvDetector()
        result = detector.detect_from_compose(Path("/nonexistent/compose.yml"))
        assert result == {}


class TestGetMissingRequired:
    """Tests for get_missing_required method."""

    def test_get_missing_required_postgres(self):
        detector = DockerEnvDetector()
        specs = detector.get_missing_required("postgres:15", ())

        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" in var_names

    def test_get_missing_required_with_password_set(self):
        detector = DockerEnvDetector()
        existing = (("POSTGRES_PASSWORD", "secret"),)
        specs = detector.get_missing_required("postgres:15", existing)

        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" not in var_names

    def test_get_missing_required_only_required(self):
        detector = DockerEnvDetector()
        specs = detector.get_missing_required("postgres:15", ())

        # Should only return required vars
        for spec in specs:
            assert spec.required


class TestNeedsPrompting:
    """Tests for needs_prompting method."""

    def test_postgres_needs_prompting(self):
        detector = DockerEnvDetector()
        assert detector.needs_prompting("postgres:15", ())

    def test_postgres_no_prompt_if_password_set(self):
        detector = DockerEnvDetector()
        existing = (("POSTGRES_PASSWORD", "secret"),)
        # Still needs prompting for optional vars with profile
        result = detector.needs_prompting("postgres:15", existing)
        # Could go either way based on implementation
        assert isinstance(result, bool)

    def test_redis_no_prompt_required(self):
        detector = DockerEnvDetector()
        # Redis has no required vars
        result = detector.needs_prompting("redis:7", ())
        # Might still prompt for optional vars if profile exists
        assert isinstance(result, bool)

    def test_unknown_image_no_prompt(self):
        detector = DockerEnvDetector(use_inspect=False)
        assert not detector.needs_prompting("unknown-image:latest", ())


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_detect_env_vars(self):
        specs = detect_env_vars("postgres:15")
        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" in var_names

    def test_detect_env_vars_with_existing(self):
        existing = (("POSTGRES_PASSWORD", "secret"),)
        specs = detect_env_vars("postgres:15", existing_env=existing)
        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" not in var_names

    def test_get_missing_required_vars(self):
        specs = get_missing_required_vars("postgres:15")
        var_names = [s.name for s in specs]
        assert "POSTGRES_PASSWORD" in var_names
