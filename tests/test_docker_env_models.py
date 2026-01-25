"""Tests for Docker environment variable models."""

import pytest
from datetime import datetime

from elle.cli.docker.env_models import (
    EnvVarSpec,
    EnvVarValue,
    EnvVarPromptResult,
    SavedEnvVar,
    ImageEnvProfile,
)


class TestEnvVarSpec:
    """Tests for EnvVarSpec model."""

    def test_create_basic_spec(self):
        spec = EnvVarSpec(name="MY_VAR")
        assert spec.name == "MY_VAR"
        assert not spec.required
        assert not spec.sensitive
        assert spec.default is None
        assert spec.description == ""

    def test_create_required_sensitive_spec(self):
        spec = EnvVarSpec(
            name="DB_PASSWORD",
            required=True,
            sensitive=True,
            description="Database password",
        )
        assert spec.name == "DB_PASSWORD"
        assert spec.required
        assert spec.sensitive
        assert spec.description == "Database password"

    def test_spec_with_default(self):
        spec = EnvVarSpec(
            name="DB_HOST",
            default="localhost",
        )
        assert spec.default == "localhost"

    def test_spec_with_example(self):
        spec = EnvVarSpec(
            name="API_KEY",
            example="sk-1234567890",
        )
        assert spec.example == "sk-1234567890"

    def test_spec_source(self):
        spec = EnvVarSpec(name="VAR", source="profile")
        assert spec.source == "profile"

        spec2 = EnvVarSpec(name="VAR", source="llm")
        assert spec2.source == "llm"

    def test_display_name_basic(self):
        spec = EnvVarSpec(name="MY_VAR")
        assert "MY_VAR" in spec.display_name()

    def test_display_name_required(self):
        spec = EnvVarSpec(name="MY_VAR", required=True)
        assert "required" in spec.display_name()

    def test_display_name_sensitive(self):
        spec = EnvVarSpec(name="MY_VAR", sensitive=True)
        assert "sensitive" in spec.display_name()

    def test_spec_is_frozen(self):
        spec = EnvVarSpec(name="MY_VAR")
        with pytest.raises(Exception):  # Pydantic raises ValidationError
            spec.name = "OTHER_VAR"


class TestEnvVarValue:
    """Tests for EnvVarValue model."""

    def test_create_basic_value(self):
        value = EnvVarValue(name="MY_VAR", value="myvalue")
        assert value.name == "MY_VAR"
        assert value.value == "myvalue"
        assert not value.from_saved
        assert not value.sensitive

    def test_create_saved_value(self):
        value = EnvVarValue(
            name="MY_VAR",
            value="saved_value",
            from_saved=True,
        )
        assert value.from_saved

    def test_create_sensitive_value(self):
        value = EnvVarValue(
            name="PASSWORD",
            value="secret",
            sensitive=True,
        )
        assert value.sensitive

    def test_value_is_frozen(self):
        value = EnvVarValue(name="MY_VAR", value="test")
        with pytest.raises(Exception):
            value.value = "changed"


class TestEnvVarPromptResult:
    """Tests for EnvVarPromptResult model."""

    def test_create_empty_result(self):
        result = EnvVarPromptResult()
        assert result.values == ()
        assert result.skipped == ()
        assert not result.cancelled

    def test_create_result_with_values(self):
        values = (
            EnvVarValue(name="VAR1", value="val1"),
            EnvVarValue(name="VAR2", value="val2"),
        )
        result = EnvVarPromptResult(values=values)
        assert len(result.values) == 2

    def test_to_env_dict(self):
        values = (
            EnvVarValue(name="VAR1", value="val1"),
            EnvVarValue(name="VAR2", value="val2"),
        )
        result = EnvVarPromptResult(values=values)
        env_dict = result.to_env_dict()

        assert env_dict == {"VAR1": "val1", "VAR2": "val2"}

    def test_to_env_tuple(self):
        values = (
            EnvVarValue(name="VAR1", value="val1"),
            EnvVarValue(name="VAR2", value="val2"),
        )
        result = EnvVarPromptResult(values=values)
        env_tuple = result.to_env_tuple()

        assert ("VAR1", "val1") in env_tuple
        assert ("VAR2", "val2") in env_tuple

    def test_from_saved_count(self):
        values = (
            EnvVarValue(name="VAR1", value="val1", from_saved=True),
            EnvVarValue(name="VAR2", value="val2", from_saved=False),
            EnvVarValue(name="VAR3", value="val3", from_saved=True),
        )
        result = EnvVarPromptResult(values=values)
        assert result.from_saved_count == 2

    def test_cancelled_result(self):
        result = EnvVarPromptResult(cancelled=True)
        assert result.cancelled

    def test_skipped_variables(self):
        result = EnvVarPromptResult(skipped=("SKIP1", "SKIP2"))
        assert len(result.skipped) == 2
        assert "SKIP1" in result.skipped


class TestSavedEnvVar:
    """Tests for SavedEnvVar model."""

    def test_create_saved_var(self):
        now = datetime.utcnow()
        saved = SavedEnvVar(
            image_family="postgres",
            name="POSTGRES_PASSWORD",
            value="secret",
            updated_at=now,
        )
        assert saved.image_family == "postgres"
        assert saved.name == "POSTGRES_PASSWORD"
        assert saved.value == "secret"
        assert saved.updated_at == now

    def test_saved_var_sensitivity(self):
        saved = SavedEnvVar(
            image_family="postgres",
            name="POSTGRES_PASSWORD",
            value="secret",
            sensitive=True,
            updated_at=datetime.utcnow(),
        )
        assert saved.sensitive


class TestImageEnvProfile:
    """Tests for ImageEnvProfile model."""

    def test_create_profile(self):
        profile = ImageEnvProfile(
            image_family="postgres",
            display_name="PostgreSQL",
            description="PostgreSQL database",
        )
        assert profile.image_family == "postgres"
        assert profile.display_name == "PostgreSQL"

    def test_profile_with_env_vars(self):
        vars = (
            EnvVarSpec(name="POSTGRES_PASSWORD", required=True, sensitive=True),
            EnvVarSpec(name="POSTGRES_USER", required=False),
        )
        profile = ImageEnvProfile(
            image_family="postgres",
            display_name="PostgreSQL",
            env_vars=vars,
        )
        assert len(profile.env_vars) == 2

    def test_required_vars_property(self):
        vars = (
            EnvVarSpec(name="REQUIRED_VAR", required=True),
            EnvVarSpec(name="OPTIONAL_VAR", required=False),
        )
        profile = ImageEnvProfile(
            image_family="test",
            display_name="Test",
            env_vars=vars,
        )
        required = profile.required_vars
        assert len(required) == 1
        assert required[0].name == "REQUIRED_VAR"

    def test_optional_vars_property(self):
        vars = (
            EnvVarSpec(name="REQUIRED_VAR", required=True),
            EnvVarSpec(name="OPTIONAL_VAR", required=False),
        )
        profile = ImageEnvProfile(
            image_family="test",
            display_name="Test",
            env_vars=vars,
        )
        optional = profile.optional_vars
        assert len(optional) == 1
        assert optional[0].name == "OPTIONAL_VAR"

    def test_has_required_vars(self):
        vars_with_required = (EnvVarSpec(name="VAR", required=True),)
        vars_no_required = (EnvVarSpec(name="VAR", required=False),)

        profile1 = ImageEnvProfile(
            image_family="test",
            display_name="Test",
            env_vars=vars_with_required,
        )
        assert profile1.has_required_vars()

        profile2 = ImageEnvProfile(
            image_family="test",
            display_name="Test",
            env_vars=vars_no_required,
        )
        assert not profile2.has_required_vars()

    def test_documentation_url(self):
        profile = ImageEnvProfile(
            image_family="postgres",
            display_name="PostgreSQL",
            documentation_url="https://hub.docker.com/_/postgres",
        )
        assert "docker.com" in profile.documentation_url
