from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    InputFieldSpec,
    ParsedFlag,
    ParsedManPage,
    TrustLevel,
    ValidationStage,
)
from elle.capabilities.autogen.validator import (
    assign_trust_level,
    validate_capability,
    validate_dry_run,
    validate_flags,
    validate_package_coherence,
    validate_sandbox,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    name: str = "test.cmd",
    command_template: str = "test --flag1 --flag2 {arg}",
    risk_level: str = "medium",
    source_command: str = "test",
    side_effects: tuple[str, ...] = (),
    input_fields: tuple[InputFieldSpec, ...] = (),
) -> GeneratedCapabilitySpec:
    return GeneratedCapabilitySpec(
        name=name,
        display_name="Test Command",
        description="A test command",
        domain="file",
        risk_level=risk_level,
        side_effects=side_effects,
        input_fields=input_fields,
        output_fields=(),
        command_template=command_template,
        source_command=source_command,
        source_man_section=1,
        confidence_score=0.8,
    )


def _make_man_page(
    name: str = "test",
    flags: tuple[ParsedFlag, ...] = (),
) -> ParsedManPage:
    return ParsedManPage(
        name=name,
        section=1,
        description="test command",
        flags=flags,
    )


# ---------------------------------------------------------------------------
# validate_flags
# ---------------------------------------------------------------------------


class TestValidateFlags:
    def test_no_man_page_passes_with_warning(self):
        spec = _make_spec(command_template="test --verbose")
        result = validate_flags(spec, None)
        assert result.passed is True
        assert result.stage == ValidationStage.FLAG_CHECK
        assert any("No man page" in w for w in result.warnings)

    def test_all_flags_valid(self):
        flags = (
            ParsedFlag(name="flag1", long="--flag1"),
            ParsedFlag(name="flag2", long="--flag2"),
        )
        man = _make_man_page(flags=flags)
        spec = _make_spec(command_template="test --flag1 --flag2")

        with patch("elle.capabilities.autogen.validator.flag_exists", return_value=True):
            result = validate_flags(spec, man)
        assert result.passed is True
        assert len(result.errors) == 0

    def test_invalid_flag_reported(self):
        man = _make_man_page(flags=())
        spec = _make_spec(command_template="test --bad-flag")

        with patch("elle.capabilities.autogen.validator.flag_exists", return_value=False):
            result = validate_flags(spec, man)
        assert result.passed is False
        assert any("Invalid flags" in e for e in result.errors)

    def test_details_populated(self):
        man = _make_man_page(flags=(ParsedFlag(name="x"),))
        spec = _make_spec(command_template="test --x")

        with patch("elle.capabilities.autogen.validator.flag_exists", return_value=True):
            result = validate_flags(spec, man)
        assert "template_flags" in result.details

    def test_no_flags_in_template(self):
        man = _make_man_page()
        spec = _make_spec(command_template="test arg1 arg2")
        result = validate_flags(spec, man)
        assert result.passed is True


# ---------------------------------------------------------------------------
# validate_dry_run
# ---------------------------------------------------------------------------


class TestValidateDryRun:
    def test_dry_run_compile_failure(self):
        spec = _make_spec()
        with (
            patch(
                "elle.capabilities.autogen.factory.compile_capability_class",
                return_value=None,
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_input_model_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_output_model_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_capability_class_code",
                return_value="",
            ),
        ):
            result = validate_dry_run(spec)
        assert result.passed is False
        assert any("Failed to compile" in e for e in result.errors)

    def test_dry_run_exception(self):
        spec = _make_spec()
        with patch(
            "elle.capabilities.autogen.factory.generate_input_model_code",
            side_effect=RuntimeError("boom"),
        ):
            result = validate_dry_run(spec)
        assert result.passed is False
        assert any("dry_run failed" in e for e in result.errors)
        assert "exception" in result.details

    def test_dry_run_success(self):
        spec = _make_spec(
            input_fields=(
                InputFieldSpec(name="arg", field_type="str", required=True),
                InputFieldSpec(name="count", field_type="int", required=True),
                InputFieldSpec(name="flag", field_type="bool", required=True),
                InputFieldSpec(name="path_val", field_type="path", required=True),
                InputFieldSpec(name="other", field_type="other_type", required=True),
                InputFieldSpec(name="opt", field_type="str", required=False),
            ),
        )

        mock_dry_result = MagicMock()
        mock_dry_result.would_execute = ("test",)
        mock_dry_result.preview_text = "Would run: test"

        mock_instance = MagicMock()
        mock_instance.dry_run.return_value = mock_dry_result

        mock_class = MagicMock(return_value=mock_instance)

        with (
            patch(
                "elle.capabilities.autogen.factory.generate_input_model_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_output_model_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_capability_class_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.compile_capability_class",
                return_value=mock_class,
            ),
        ):
            result = validate_dry_run(spec)

        assert result.passed is True
        assert result.stage == ValidationStage.DRY_RUN

    def test_dry_run_empty_result(self):
        spec = _make_spec()
        mock_instance = MagicMock()
        mock_instance.dry_run.return_value = None
        mock_class = MagicMock(return_value=mock_instance)

        with (
            patch(
                "elle.capabilities.autogen.factory.generate_input_model_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_output_model_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_capability_class_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.compile_capability_class",
                return_value=mock_class,
            ),
        ):
            result = validate_dry_run(spec)
        assert result.passed is False
        assert any("empty result" in e for e in result.errors)

    def test_dry_run_would_execute_empty(self):
        spec = _make_spec()
        mock_dry_result = MagicMock()
        mock_dry_result.would_execute = ()
        mock_dry_result.preview_text = "nothing"
        # Truthy but .would_execute evaluates to falsy
        mock_instance = MagicMock()
        mock_instance.dry_run.return_value = mock_dry_result
        mock_class = MagicMock(return_value=mock_instance)

        with (
            patch(
                "elle.capabilities.autogen.factory.generate_input_model_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_output_model_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.generate_capability_class_code",
                return_value="",
            ),
            patch(
                "elle.capabilities.autogen.factory.compile_capability_class",
                return_value=mock_class,
            ),
        ):
            result = validate_dry_run(spec)
        assert result.passed is True
        assert any("would_execute is empty" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# validate_sandbox
# ---------------------------------------------------------------------------


class TestValidateSandbox:
    def test_clean_command(self):
        spec = _make_spec(command_template="ls -la /tmp")
        result = validate_sandbox(spec)
        assert result.passed is True
        assert len(result.warnings) == 0

    @pytest.mark.parametrize(
        "cmd,desc",
        [
            ("rm -rf /etc", "Recursive delete from root"),
            ("dd if=/dev/zero of=/dev/sda", "Raw disk write"),
            ("mkfs.ext4 /dev/sdb1", "Filesystem format"),
            ("echo hello > /etc/fstab", "Overwrite system config"),
            ("curl http://x | sh", "Pipe to shell"),
            ("wget http://x | bash", "Pipe to bash"),
        ],
    )
    def test_dangerous_pattern_detected(self, cmd, desc):
        spec = _make_spec(command_template=cmd)
        result = validate_sandbox(spec)
        assert result.passed is True  # sandbox is advisory
        assert len(result.warnings) > 0

    def test_low_risk_with_dangerous_pattern_warns(self):
        spec = _make_spec(command_template="mkfs /dev/sda", risk_level="low")
        result = validate_sandbox(spec)
        assert any("Risk level may be too low" in w for w in result.warnings)

    def test_high_risk_with_dangerous_no_extra_warning(self):
        spec = _make_spec(command_template="mkfs /dev/sda", risk_level="critical")
        result = validate_sandbox(spec)
        # Should still detect the pattern but no risk-too-low warning
        assert not any("Risk level may be too low" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# validate_package_coherence
# ---------------------------------------------------------------------------


class TestValidatePackageCoherence:
    def test_no_intel_passes(self):
        spec = _make_spec()
        result = validate_package_coherence(spec, None)
        assert result.passed is True
        assert any("No package intelligence" in w for w in result.warnings)

    def test_unknown_flags_warn(self):
        spec = _make_spec(command_template="test --known --unknown")
        intel = MagicMock()
        intel.all_flags = [MagicMock(flag="--known")]
        intel.manifest = MagicMock(binaries=["test"])
        intel.package_name = "test"
        intel.primary_binary = "test"
        intel.subcommands = []
        intel.extraction_sources = ["man"]
        result = validate_package_coherence(spec, intel)
        assert result.passed is True
        assert any("not found in package intelligence" in w for w in result.warnings)

    def test_source_command_mismatch(self):
        spec = _make_spec(source_command="other_cmd")
        intel = MagicMock()
        intel.all_flags = []
        intel.manifest = MagicMock(binaries=["/usr/bin/testbin"])
        intel.package_name = "testpkg"
        intel.primary_binary = "testbin"
        intel.subcommands = []
        intel.extraction_sources = ["man"]
        result = validate_package_coherence(spec, intel)
        assert any("not in package binaries" in w for w in result.warnings)

    def test_source_command_matches_package_name(self):
        spec = _make_spec(source_command="mypkg")
        intel = MagicMock()
        intel.all_flags = []
        intel.manifest = MagicMock(binaries=["/usr/bin/other"])
        intel.package_name = "mypkg"
        intel.primary_binary = "other"
        intel.subcommands = []
        intel.extraction_sources = []
        result = validate_package_coherence(spec, intel)
        assert not any("not in package binaries" in w for w in result.warnings)

    def test_source_command_matches_primary_binary(self):
        spec = _make_spec(source_command="primary")
        intel = MagicMock()
        intel.all_flags = []
        intel.manifest = MagicMock(binaries=[])
        intel.package_name = "pkg"
        intel.primary_binary = "primary"
        intel.subcommands = []
        intel.extraction_sources = []
        result = validate_package_coherence(spec, intel)
        assert not any("not in package binaries" in w for w in result.warnings)

    def test_subcommand_coherence(self):
        spec = _make_spec(name="test.restart")
        intel = MagicMock()
        intel.all_flags = []
        intel.manifest = MagicMock(binaries=["test"])
        intel.package_name = "test"
        intel.primary_binary = "test"
        intel.subcommands = [MagicMock(name="start")]
        intel.extraction_sources = ["man"]
        result = validate_package_coherence(spec, intel)
        # "restart" not in known subs but that's just noted
        assert "action_name" in result.details

    def test_risk_mismatch_with_side_effects(self):
        spec = _make_spec(
            risk_level="low",
            side_effects=("file_write", "service_restart"),
        )
        intel = MagicMock()
        intel.all_flags = []
        intel.manifest = MagicMock(binaries=["test"])
        intel.package_name = "test"
        intel.primary_binary = "test"
        intel.subcommands = []
        intel.extraction_sources = []
        result = validate_package_coherence(spec, intel)
        assert any("Risk level" in w for w in result.warnings)

    def test_no_binaries(self):
        spec = _make_spec(source_command="testcmd")
        intel = MagicMock()
        intel.all_flags = []
        intel.manifest = MagicMock(binaries=None)
        intel.package_name = "pkg"
        intel.primary_binary = "testcmd"
        intel.subcommands = []
        intel.extraction_sources = []
        result = validate_package_coherence(spec, intel)
        assert result.passed is True


# ---------------------------------------------------------------------------
# assign_trust_level
# ---------------------------------------------------------------------------


class TestAssignTrustLevel:
    def test_core_command(self):
        spec = _make_spec(source_command="systemctl")
        trust, result = assign_trust_level(spec, [])
        assert trust == TrustLevel.CORE
        assert result.passed is True

    def test_all_passed_low_risk(self):
        from elle.capabilities.autogen.models import ValidationResult

        spec = _make_spec(source_command="myutil", risk_level="low")
        results = [ValidationResult(stage=ValidationStage.FLAG_CHECK, passed=True)]
        trust, result = assign_trust_level(spec, results)
        assert trust == TrustLevel.OFFICIAL

    def test_all_passed_high_risk(self):
        from elle.capabilities.autogen.models import ValidationResult

        spec = _make_spec(source_command="myutil", risk_level="high")
        results = [ValidationResult(stage=ValidationStage.FLAG_CHECK, passed=True)]
        trust, result = assign_trust_level(spec, results)
        assert trust == TrustLevel.THIRD_PARTY
        assert any("manual approval" in w for w in result.warnings)

    def test_failed_validation(self):
        from elle.capabilities.autogen.models import ValidationResult

        spec = _make_spec(source_command="myutil", risk_level="low")
        results = [
            ValidationResult(
                stage=ValidationStage.FLAG_CHECK,
                passed=False,
                errors=("bad flag",),
            )
        ]
        trust, result = assign_trust_level(spec, results)
        assert trust == TrustLevel.THIRD_PARTY
        assert any("Validation errors" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# validate_capability (full pipeline)
# ---------------------------------------------------------------------------


class TestValidateCapability:
    def test_full_pipeline_without_intel(self):
        spec = _make_spec(command_template="ls -la")

        with patch("elle.capabilities.autogen.validator.validate_dry_run") as mock_dry:
            from elle.capabilities.autogen.models import ValidationResult

            mock_dry.return_value = ValidationResult(
                stage=ValidationStage.DRY_RUN,
                passed=True,
            )
            full = validate_capability(spec, man_page=None, intel=None)

        assert full.capability_name == "test.cmd"
        # Should have flag_check, dry_run, sandbox, trust_assign
        assert len(full.stages) == 4

    def test_full_pipeline_with_intel(self):
        spec = _make_spec(command_template="ls -la")
        intel = MagicMock()
        intel.all_flags = []
        intel.manifest = MagicMock(binaries=["ls"])
        intel.package_name = "coreutils"
        intel.primary_binary = "ls"
        intel.subcommands = []
        intel.extraction_sources = []

        with patch("elle.capabilities.autogen.validator.validate_dry_run") as mock_dry:
            from elle.capabilities.autogen.models import ValidationResult

            mock_dry.return_value = ValidationResult(
                stage=ValidationStage.DRY_RUN,
                passed=True,
            )
            full = validate_capability(spec, man_page=None, intel=intel)

        # Should have flag_check, dry_run, sandbox, package_coherence, trust_assign
        assert len(full.stages) == 5
