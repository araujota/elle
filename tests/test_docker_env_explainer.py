from __future__ import annotations

"""Tests for Docker Environment Variable Explainer."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.docker.env_explainer import (
    ENV_EXPLAINER_SYSTEM,
    EnvVarExplainer,
    EnvVarExplanation,
    explain_env_var,
    get_explainer,
)
from elle.cli.docker.env_models import EnvVarSpec, ImageEnvProfile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_llm():
    """Create a mock LLM instance."""
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.generate_json.return_value = {
        "description": "A test variable for configuring something.",
        "is_sensitive": False,
        "example": "test_value",
    }
    return llm


@pytest.fixture()
def explainer(mock_llm):
    """Create an EnvVarExplainer with a mocked LLM."""
    return EnvVarExplainer(llm=mock_llm, timeout=5.0, max_tokens=128)


@pytest.fixture()
def postgres_profile():
    """Build a small postgres-like profile for testing."""
    return ImageEnvProfile(
        image_family="postgres",
        display_name="PostgreSQL",
        description="PostgreSQL database server",
        env_vars=(
            EnvVarSpec(
                name="POSTGRES_PASSWORD",
                required=True,
                sensitive=True,
                description="Password for the PostgreSQL superuser",
                example="mysecretpassword",
            ),
            EnvVarSpec(
                name="POSTGRES_USER",
                required=False,
                description="Username for the superuser",
                example="myuser",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# EnvVarExplanation model tests
# ---------------------------------------------------------------------------


class TestEnvVarExplanation:
    """Tests for the EnvVarExplanation model."""

    def test_basic_creation(self):
        exp = EnvVarExplanation(
            name="MY_VAR",
            description="Does something useful",
        )
        assert exp.name == "MY_VAR"
        assert exp.description == "Does something useful"
        assert exp.is_sensitive is False
        assert exp.example is None
        assert exp.source == "llm"

    def test_all_fields(self):
        exp = EnvVarExplanation(
            name="SECRET_KEY",
            description="An API key",
            is_sensitive=True,
            example="sk-abc123",
            source="profile",
        )
        assert exp.is_sensitive is True
        assert exp.example == "sk-abc123"
        assert exp.source == "profile"

    def test_frozen_model(self):
        exp = EnvVarExplanation(name="X", description="desc")
        with pytest.raises(Exception):
            exp.name = "Y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EnvVarExplainer.__init__ / property tests
# ---------------------------------------------------------------------------


class TestEnvVarExplainerInit:
    """Tests for EnvVarExplainer initialisation and llm property."""

    def test_default_values(self):
        explainer_inst = EnvVarExplainer()
        assert explainer_inst._llm is None
        assert explainer_inst._timeout == 30.0
        assert explainer_inst._max_tokens == 256
        assert explainer_inst._cache == {}

    def test_custom_values(self, mock_llm):
        explainer_inst = EnvVarExplainer(llm=mock_llm, timeout=10.0, max_tokens=512)
        assert explainer_inst._llm is mock_llm
        assert explainer_inst._timeout == 10.0
        assert explainer_inst._max_tokens == 512

    def test_llm_property_returns_injected_llm(self, mock_llm):
        explainer_inst = EnvVarExplainer(llm=mock_llm)
        assert explainer_inst.llm is mock_llm

    @patch("elle.rag.llm.get_llm")
    def test_llm_property_lazy_loads_when_none(self, mock_get_llm):
        fake_llm = MagicMock()
        mock_get_llm.return_value = fake_llm

        explainer_inst = EnvVarExplainer(llm=None)
        result = explainer_inst.llm

        mock_get_llm.assert_called_once()
        assert result is fake_llm

    @patch("elle.rag.llm.get_llm")
    def test_llm_property_caches_after_first_call(self, mock_get_llm):
        fake_llm = MagicMock()
        mock_get_llm.return_value = fake_llm

        explainer_inst = EnvVarExplainer(llm=None)
        _ = explainer_inst.llm
        _ = explainer_inst.llm

        mock_get_llm.assert_called_once()


# ---------------------------------------------------------------------------
# EnvVarExplainer.explain
# ---------------------------------------------------------------------------


class TestExplain:
    """Tests for the explain() convenience method."""

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_returns_description_when_explanation_found(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "postgres"
        mock_get_profile.return_value = None

        result = explainer.explain("MY_VAR", "postgres:15")
        assert result == "A test variable for configuring something."

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_returns_empty_string_when_no_explanation(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "unknown"
        mock_get_profile.return_value = None
        explainer._llm.is_available.return_value = False

        result = explainer.explain("MY_VAR", "unknown:latest")
        assert result == ""

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_force_llm_parameter_propagated(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "postgres"
        mock_get_profile.return_value = None

        result = explainer.explain("MY_VAR", "postgres:15", force_llm=True)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# EnvVarExplainer.get_explanation
# ---------------------------------------------------------------------------


class TestGetExplanation:
    """Tests for get_explanation()."""

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_returns_cached_result(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "postgres"
        cached = EnvVarExplanation(name="X", description="cached")
        explainer._cache[("X", "postgres")] = cached

        result = explainer.get_explanation("X", "postgres:15")
        assert result is cached
        # LLM should not have been called
        explainer._llm.generate_json.assert_not_called()

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_returns_profile_match(self, mock_extract, mock_get_profile, explainer, postgres_profile):
        mock_extract.return_value = "postgres"
        mock_get_profile.return_value = postgres_profile

        result = explainer.get_explanation("POSTGRES_PASSWORD", "postgres:15")

        assert result is not None
        assert result.name == "POSTGRES_PASSWORD"
        assert result.is_sensitive is True
        assert result.source == "profile"
        assert result.example == "mysecretpassword"
        assert "superuser" in result.description

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_profile_result_is_cached(self, mock_extract, mock_get_profile, explainer, postgres_profile):
        mock_extract.return_value = "postgres"
        mock_get_profile.return_value = postgres_profile

        explainer.get_explanation("POSTGRES_PASSWORD", "postgres:15")
        assert ("POSTGRES_PASSWORD", "postgres") in explainer._cache

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_profile_var_not_found_falls_through_to_llm(
        self, mock_extract, mock_get_profile, explainer, postgres_profile
    ):
        mock_extract.return_value = "postgres"
        mock_get_profile.return_value = postgres_profile

        result = explainer.get_explanation("UNKNOWN_VAR", "postgres:15")

        assert result is not None
        assert result.source == "llm"
        explainer._llm.generate_json.assert_called_once()

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_force_llm_skips_profile(self, mock_extract, mock_get_profile, explainer, postgres_profile):
        mock_extract.return_value = "postgres"
        mock_get_profile.return_value = postgres_profile

        result = explainer.get_explanation("POSTGRES_PASSWORD", "postgres:15", force_llm=True)

        assert result is not None
        # Should have asked the LLM, not used profile
        assert result.source == "llm"
        explainer._llm.generate_json.assert_called_once()

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_llm_result_is_cached(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "custom"
        mock_get_profile.return_value = None

        explainer.get_explanation("MY_VAR", "custom:latest")
        assert ("MY_VAR", "custom") in explainer._cache

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_llm_failure_returns_none(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "custom"
        mock_get_profile.return_value = None
        explainer._llm.generate_json.side_effect = RuntimeError("LLM down")

        result = explainer.get_explanation("MY_VAR", "custom:latest")
        assert result is None

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_llm_failure_logs_warning(self, mock_extract, mock_get_profile, explainer, caplog):
        """When _explain_with_llm raises (not its internal catch), warning is logged."""
        mock_extract.return_value = "custom"
        mock_get_profile.return_value = None
        # Make is_available() raise so the exception propagates out of
        # _explain_with_llm (before its own try/except block).
        explainer._llm.is_available.side_effect = RuntimeError("connection lost")

        with caplog.at_level(logging.WARNING, logger="elle.cli.docker.env_explainer"):
            result = explainer.get_explanation("MY_VAR", "custom:latest")

        assert result is None
        assert "LLM explanation failed" in caplog.text

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_no_profile_and_llm_unavailable_returns_none(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "unknown"
        mock_get_profile.return_value = None
        explainer._llm.is_available.return_value = False

        result = explainer.get_explanation("MY_VAR", "unknown:latest")
        assert result is None


# ---------------------------------------------------------------------------
# EnvVarExplainer._explain_with_llm
# ---------------------------------------------------------------------------


class TestExplainWithLlm:
    """Tests for the internal _explain_with_llm method."""

    def test_returns_none_when_llm_unavailable(self, explainer):
        explainer._llm.is_available.return_value = False
        result = explainer._explain_with_llm("MY_VAR", "myimage:latest")
        assert result is None

    def test_returns_explanation_on_success(self, explainer):
        result = explainer._explain_with_llm("MY_VAR", "myimage:latest")

        assert result is not None
        assert result.name == "MY_VAR"
        assert result.description == "A test variable for configuring something."
        assert result.is_sensitive is False
        assert result.example == "test_value"
        assert result.source == "llm"

    def test_calls_generate_json_with_correct_args(self, explainer):
        explainer._explain_with_llm("DB_HOST", "myimage:latest")

        explainer._llm.generate_json.assert_called_once()
        call_kwargs = explainer._llm.generate_json.call_args
        prompt = call_kwargs[0][0]
        assert "DB_HOST" in prompt
        assert "myimage:latest" in prompt
        assert call_kwargs[1]["system"] == ENV_EXPLAINER_SYSTEM
        assert call_kwargs[1]["max_tokens"] == 128
        assert call_kwargs[1]["timeout"] == 5.0
        assert call_kwargs[1]["temperature"] == 0.3

    def test_handles_missing_keys_in_llm_response(self, explainer):
        explainer._llm.generate_json.return_value = {}

        result = explainer._explain_with_llm("MY_VAR", "myimage:latest")

        assert result is not None
        assert result.description == ""
        assert result.is_sensitive is False
        assert result.example is None

    def test_returns_none_on_generate_json_exception(self, explainer, caplog):
        explainer._llm.generate_json.side_effect = ValueError("bad json")

        with caplog.at_level(logging.DEBUG, logger="elle.cli.docker.env_explainer"):
            result = explainer._explain_with_llm("MY_VAR", "myimage:latest")

        assert result is None
        assert "LLM explanation error" in caplog.text

    def test_handles_sensitive_response(self, explainer):
        explainer._llm.generate_json.return_value = {
            "description": "A password variable",
            "is_sensitive": True,
            "example": None,
        }
        result = explainer._explain_with_llm("DB_PASSWORD", "myimage:latest")

        assert result is not None
        assert result.is_sensitive is True
        assert result.example is None


# ---------------------------------------------------------------------------
# EnvVarExplainer.explain_batch
# ---------------------------------------------------------------------------


class TestExplainBatch:
    """Tests for explain_batch()."""

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_returns_cached_entries(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "myimg"
        mock_get_profile.return_value = None

        cached = EnvVarExplanation(name="VAR_A", description="cached A")
        explainer._cache[("VAR_A", "myimg")] = cached

        results = explainer.explain_batch(["VAR_A"], "myimg:latest")

        assert "VAR_A" in results
        assert results["VAR_A"] is cached
        explainer._llm.generate_json.assert_not_called()

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_returns_profile_matches(self, mock_extract, mock_get_profile, explainer, postgres_profile):
        mock_extract.return_value = "postgres"
        mock_get_profile.return_value = postgres_profile

        results = explainer.explain_batch(["POSTGRES_PASSWORD", "POSTGRES_USER"], "postgres:15")

        assert "POSTGRES_PASSWORD" in results
        assert results["POSTGRES_PASSWORD"].source == "profile"
        assert results["POSTGRES_PASSWORD"].is_sensitive is True
        assert "POSTGRES_USER" in results
        assert results["POSTGRES_USER"].source == "profile"

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_batch_llm_for_unresolved_vars(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "custom"
        mock_get_profile.return_value = None

        explainer._llm.generate_json.return_value = {
            "VAR_X": {
                "description": "An X variable",
                "is_sensitive": False,
                "example": "x",
            },
            "VAR_Y": {
                "description": "A Y variable",
                "is_sensitive": True,
                "example": None,
            },
        }

        results = explainer.explain_batch(["VAR_X", "VAR_Y"], "custom:latest")

        assert "VAR_X" in results
        assert results["VAR_X"].description == "An X variable"
        assert "VAR_Y" in results
        assert results["VAR_Y"].is_sensitive is True

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_batch_caches_llm_results(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "custom"
        mock_get_profile.return_value = None

        explainer._llm.generate_json.return_value = {
            "VAR_Z": {
                "description": "A Z variable",
                "is_sensitive": False,
                "example": "z",
            },
        }

        explainer.explain_batch(["VAR_Z"], "custom:latest")

        assert ("VAR_Z", "custom") in explainer._cache

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_batch_skips_llm_when_unavailable(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "custom"
        mock_get_profile.return_value = None
        explainer._llm.is_available.return_value = False

        results = explainer.explain_batch(["VAR_A"], "custom:latest")

        assert results == {}
        explainer._llm.generate_json.assert_not_called()

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_batch_mixes_cache_profile_and_llm(self, mock_extract, mock_get_profile, explainer, postgres_profile):
        mock_extract.return_value = "postgres"
        mock_get_profile.return_value = postgres_profile

        # Pre-cache one variable
        cached = EnvVarExplanation(name="CACHED_VAR", description="cached")
        explainer._cache[("CACHED_VAR", "postgres")] = cached

        # The LLM will handle UNKNOWN_VAR
        explainer._llm.generate_json.return_value = {
            "UNKNOWN_VAR": {
                "description": "LLM resolved",
                "is_sensitive": False,
                "example": None,
            },
        }

        results = explainer.explain_batch(
            ["CACHED_VAR", "POSTGRES_PASSWORD", "UNKNOWN_VAR"],
            "postgres:15",
        )

        assert results["CACHED_VAR"].description == "cached"
        assert results["POSTGRES_PASSWORD"].source == "profile"
        assert results["UNKNOWN_VAR"].description == "LLM resolved"

    @patch("elle.cli.docker.env_explainer.get_profile")
    @patch("elle.cli.docker.env_explainer.extract_image_family")
    def test_batch_llm_response_ignores_non_dict_entries(self, mock_extract, mock_get_profile, explainer):
        mock_extract.return_value = "custom"
        mock_get_profile.return_value = None

        explainer._llm.generate_json.return_value = {
            "VAR_A": "not a dict",
            "VAR_B": {
                "description": "Good entry",
                "is_sensitive": False,
                "example": None,
            },
        }

        results = explainer.explain_batch(["VAR_A", "VAR_B"], "custom:latest")

        assert "VAR_A" not in results
        assert "VAR_B" in results


# ---------------------------------------------------------------------------
# EnvVarExplainer._explain_batch_with_llm
# ---------------------------------------------------------------------------


class TestExplainBatchWithLlm:
    """Tests for the internal _explain_batch_with_llm method."""

    def test_empty_list_returns_empty_dict(self, explainer):
        result = explainer._explain_batch_with_llm([], "myimage:latest")
        assert result == {}
        explainer._llm.generate_json.assert_not_called()

    def test_calls_generate_json_with_batch_prompt(self, explainer):
        explainer._llm.generate_json.return_value = {}

        explainer._explain_batch_with_llm(["A", "B"], "myimage:latest")

        call_args = explainer._llm.generate_json.call_args
        prompt = call_args[0][0]
        assert "- A" in prompt
        assert "- B" in prompt
        assert "myimage:latest" in prompt

    def test_max_tokens_scaled_by_var_count(self, explainer):
        explainer._llm.generate_json.return_value = {}

        explainer._explain_batch_with_llm(["A", "B", "C"], "myimage:latest")

        call_kwargs = explainer._llm.generate_json.call_args[1]
        assert call_kwargs["max_tokens"] == 128 * 3

    def test_timeout_doubled_for_batch(self, explainer):
        explainer._llm.generate_json.return_value = {}

        explainer._explain_batch_with_llm(["A"], "myimage:latest")

        call_kwargs = explainer._llm.generate_json.call_args[1]
        assert call_kwargs["timeout"] == 5.0 * 2

    def test_system_prompt_modified_for_batch(self, explainer):
        explainer._llm.generate_json.return_value = {}

        explainer._explain_batch_with_llm(["A"], "myimage:latest")

        call_kwargs = explainer._llm.generate_json.call_args[1]
        assert "keys are variable names" in call_kwargs["system"]

    def test_parses_valid_batch_response(self, explainer):
        explainer._llm.generate_json.return_value = {
            "VAR_A": {
                "description": "Variable A desc",
                "is_sensitive": False,
                "example": "aaa",
            },
            "VAR_B": {
                "description": "Variable B desc",
                "is_sensitive": True,
                "example": None,
            },
        }

        results = explainer._explain_batch_with_llm(["VAR_A", "VAR_B"], "myimage:latest")

        assert "VAR_A" in results
        assert results["VAR_A"].description == "Variable A desc"
        assert results["VAR_A"].source == "llm"
        assert "VAR_B" in results
        assert results["VAR_B"].is_sensitive is True

    def test_skips_vars_missing_from_response(self, explainer):
        explainer._llm.generate_json.return_value = {
            "VAR_A": {
                "description": "only A",
                "is_sensitive": False,
                "example": None,
            },
        }

        results = explainer._explain_batch_with_llm(["VAR_A", "VAR_B"], "myimage:latest")

        assert "VAR_A" in results
        assert "VAR_B" not in results

    def test_skips_non_dict_entries(self, explainer):
        explainer._llm.generate_json.return_value = {
            "VAR_A": "just a string",
            "VAR_B": 42,
            "VAR_C": {"description": "ok", "is_sensitive": False, "example": None},
        }

        results = explainer._explain_batch_with_llm(["VAR_A", "VAR_B", "VAR_C"], "myimage:latest")

        assert "VAR_A" not in results
        assert "VAR_B" not in results
        assert "VAR_C" in results

    def test_falls_back_to_individual_on_exception(self, explainer, caplog):
        explainer._llm.generate_json.side_effect = [
            RuntimeError("batch failed"),
            # Individual fallback calls
            {"description": "single A", "is_sensitive": False, "example": None},
            {"description": "single B", "is_sensitive": False, "example": None},
        ]

        with caplog.at_level(logging.DEBUG, logger="elle.cli.docker.env_explainer"):
            results = explainer._explain_batch_with_llm(["VAR_A", "VAR_B"], "myimage:latest")

        assert "Batch LLM explanation error" in caplog.text
        assert "VAR_A" in results
        assert "VAR_B" in results

    def test_fallback_individual_calls_handle_failures_gracefully(self, explainer):
        # Batch call fails, then individual calls: first succeeds, second fails
        explainer._llm.generate_json.side_effect = [
            RuntimeError("batch failed"),
            {"description": "fallback A", "is_sensitive": False, "example": None},
            ValueError("also failed"),
        ]

        results = explainer._explain_batch_with_llm(["VAR_A", "VAR_B"], "myimage:latest")

        assert "VAR_A" in results
        assert "VAR_B" not in results

    def test_fallback_skips_unavailable_llm(self, explainer):
        # Batch fails, then LLM becomes unavailable for individual calls
        explainer._llm.generate_json.side_effect = RuntimeError("batch failed")
        # After first exception, switch is_available to False
        call_count = [0]

        def toggle_available():
            call_count[0] += 1
            return not call_count[0] > 1

        explainer._llm.is_available.side_effect = toggle_available

        results = explainer._explain_batch_with_llm(["VAR_A"], "myimage:latest")

        assert "VAR_A" not in results


# ---------------------------------------------------------------------------
# EnvVarExplainer.clear_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    """Tests for clear_cache()."""

    def test_clears_populated_cache(self, explainer):
        explainer._cache[("X", "img")] = EnvVarExplanation(name="X", description="desc")
        assert len(explainer._cache) == 1

        explainer.clear_cache()
        assert len(explainer._cache) == 0

    def test_clear_on_empty_cache_is_safe(self, explainer):
        explainer.clear_cache()
        assert len(explainer._cache) == 0


# ---------------------------------------------------------------------------
# Module-level functions: get_explainer / explain_env_var
# ---------------------------------------------------------------------------


class TestModuleLevelFunctions:
    """Tests for module-level convenience functions."""

    def test_get_explainer_returns_instance(self):
        import elle.cli.docker.env_explainer as mod

        # Reset global state
        mod._explainer = None

        instance = get_explainer()
        assert isinstance(instance, EnvVarExplainer)

    def test_get_explainer_returns_same_instance(self):
        import elle.cli.docker.env_explainer as mod

        mod._explainer = None

        first = get_explainer()
        second = get_explainer()
        assert first is second

    @patch("elle.cli.docker.env_explainer.get_explainer")
    def test_explain_env_var_delegates_to_explainer(self, mock_get_explainer):
        mock_instance = MagicMock()
        mock_instance.explain.return_value = "some explanation"
        mock_get_explainer.return_value = mock_instance

        result = explain_env_var("MY_VAR", "myimage:latest")

        assert result == "some explanation"
        mock_instance.explain.assert_called_once_with("MY_VAR", "myimage:latest")

    def test_get_explainer_reuses_existing(self):
        import elle.cli.docker.env_explainer as mod

        sentinel = EnvVarExplainer()
        mod._explainer = sentinel

        assert get_explainer() is sentinel

        # Cleanup
        mod._explainer = None


# ---------------------------------------------------------------------------
# ENV_EXPLAINER_SYSTEM constant
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for module-level constants."""

    def test_system_prompt_is_nonempty_string(self):
        assert isinstance(ENV_EXPLAINER_SYSTEM, str)
        assert len(ENV_EXPLAINER_SYSTEM) > 0

    def test_system_prompt_mentions_json(self):
        assert "JSON" in ENV_EXPLAINER_SYSTEM

    def test_system_prompt_mentions_sensitive(self):
        assert "sensitive" in ENV_EXPLAINER_SYSTEM.lower()
