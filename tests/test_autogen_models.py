"""Tests for autogen models."""

from __future__ import annotations

import pytest

from elle.capabilities.autogen.models import (
    CORE_COMMANDS,
    DiscoveryResult,
    FlagType,
    FullValidationResult,
    GeneratedCapabilitySpec,
    InputFieldSpec,
    InstalledBinary,
    OutputFieldSpec,
    ParsedFlag,
    ParsedManPage,
    ParsedSynopsis,
    StoredCapability,
    TrustLevel,
    ValidationResult,
    ValidationStage,
)


class TestInstalledBinary:
    """Tests for InstalledBinary model."""

    def test_create(self):
        """Test creating an installed binary."""
        binary = InstalledBinary(
            name="ls",
            path="/usr/bin/ls",
            package="coreutils",
            version="8.32",
            man_available=True,
            man_section=1,
            man_path="/usr/share/man/man1/ls.1.gz",
        )

        assert binary.name == "ls"
        assert binary.path == "/usr/bin/ls"
        assert binary.man_available is True

    def test_frozen(self):
        """Test model is frozen."""
        binary = InstalledBinary(name="ls", path="/usr/bin/ls")

        with pytest.raises(Exception):
            binary.name = "changed"


class TestDiscoveryResult:
    """Tests for DiscoveryResult model."""

    def test_create(self):
        """Test creating discovery result."""
        result = DiscoveryResult(
            binaries=(InstalledBinary(name="ls", path="/usr/bin/ls"),),
            total_scanned=100,
            with_man_pages=50,
        )

        assert len(result.binaries) == 1
        assert result.total_scanned == 100

    def test_empty(self):
        """Test empty discovery result."""
        result = DiscoveryResult()
        assert len(result.binaries) == 0
        assert result.total_scanned == 0


class TestParsedFlag:
    """Tests for ParsedFlag model."""

    def test_create(self):
        """Test creating parsed flag."""
        flag = ParsedFlag(
            name="verbose",
            short="-v",
            long="--verbose",
            flag_type=FlagType.BOOL,
            description="Enable verbose output",
        )

        assert flag.name == "verbose"
        assert flag.flag_type == FlagType.BOOL

    def test_flag_types(self):
        """Test all flag types."""
        assert FlagType.BOOL.value == "bool"
        assert FlagType.STRING.value == "str"
        assert FlagType.INT.value == "int"
        assert FlagType.PATH.value == "path"
        assert FlagType.LIST.value == "list"


class TestParsedSynopsis:
    """Tests for ParsedSynopsis model."""

    def test_create(self):
        """Test creating parsed synopsis."""
        synopsis = ParsedSynopsis(
            raw="ls [OPTIONS] [FILE]...",
            command="ls",
            subcommands=("list", "tree"),
            positional_args=("file",),
            has_options=True,
        )

        assert synopsis.command == "ls"
        assert "list" in synopsis.subcommands


class TestParsedManPage:
    """Tests for ParsedManPage model."""

    def test_create(self):
        """Test creating parsed man page."""
        man_page = ParsedManPage(
            name="systemctl",
            section=1,
            description="Control the systemd system and service manager",
            flags=(ParsedFlag(name="help", short="-h", long="--help", flag_type=FlagType.BOOL),),
        )

        assert man_page.name == "systemctl"
        assert man_page.section == 1
        assert len(man_page.flags) == 1


class TestGeneratedCapabilitySpec:
    """Tests for GeneratedCapabilitySpec model."""

    def test_create(self):
        """Test creating generated spec."""
        spec = GeneratedCapabilitySpec(
            name="systemctl.restart",
            display_name="Systemctl Restart",
            description="Restart a systemd service",
            domain="service",
            risk_level="medium",
            side_effects=("service_change",),
            input_fields=(InputFieldSpec(name="unit", field_type="str", description="Unit name"),),
            output_fields=(OutputFieldSpec(name="success", field_type="bool", description="Success"),),
            command_template="systemctl restart {unit}",
            source_command="systemctl",
            confidence_score=0.85,
        )

        assert spec.name == "systemctl.restart"
        assert spec.risk_level == "medium"
        assert "{unit}" in spec.command_template

    def test_confidence_bounds(self):
        """Test confidence score bounds."""
        # Valid
        spec = GeneratedCapabilitySpec(
            name="test",
            domain="file",
            command_template="test",
            source_command="test",
            confidence_score=0.5,
        )
        assert spec.confidence_score == 0.5

        # Out of bounds should raise
        with pytest.raises(Exception):
            GeneratedCapabilitySpec(
                name="test",
                domain="file",
                command_template="test",
                source_command="test",
                confidence_score=1.5,
            )


class TestValidationResult:
    """Tests for ValidationResult model."""

    def test_create(self):
        """Test creating validation result."""
        result = ValidationResult(
            stage=ValidationStage.FLAG_CHECK,
            passed=True,
            errors=(),
            warnings=("Minor issue",),
        )

        assert result.stage == ValidationStage.FLAG_CHECK
        assert result.passed is True
        assert len(result.warnings) == 1

    def test_validation_stages(self):
        """Test all validation stages."""
        assert ValidationStage.FLAG_CHECK.value == "flag_check"
        assert ValidationStage.DRY_RUN.value == "dry_run"
        assert ValidationStage.SANDBOX.value == "sandbox"
        assert ValidationStage.TRUST_ASSIGN.value == "trust_assign"


class TestFullValidationResult:
    """Tests for FullValidationResult model."""

    def test_create(self):
        """Test creating full validation result."""
        result = FullValidationResult(
            capability_name="test.capability",
            stages=(
                ValidationResult(stage=ValidationStage.FLAG_CHECK, passed=True),
                ValidationResult(stage=ValidationStage.DRY_RUN, passed=True),
            ),
            overall_passed=True,
            trust_level=TrustLevel.OFFICIAL,
        )

        assert result.capability_name == "test.capability"
        assert result.overall_passed is True
        assert len(result.stages) == 2


class TestTrustLevel:
    """Tests for TrustLevel enum."""

    def test_levels(self):
        """Test all trust levels."""
        assert TrustLevel.CORE.value == "core"
        assert TrustLevel.OFFICIAL.value == "official"
        assert TrustLevel.THIRD_PARTY.value == "third_party"


class TestStoredCapability:
    """Tests for StoredCapability model."""

    def test_create(self):
        """Test creating stored capability."""
        stored = StoredCapability(
            id="cap-123",
            capability_name="test.cap",
            spec_json="{}",
            input_model_code="class Input: pass",
            output_model_code="class Output: pass",
            capability_class_code="class Cap: pass",
            source_command="test",
            man_page_hash="abc123",
            trust_level=TrustLevel.OFFICIAL,
            approved=True,
            enabled=True,
        )

        assert stored.id == "cap-123"
        assert stored.approved is True


class TestCoreCommands:
    """Tests for CORE_COMMANDS set."""

    def test_common_commands_included(self):
        """Test common commands are in core set."""
        assert "systemctl" in CORE_COMMANDS
        assert "docker" in CORE_COMMANDS
        assert "apt" in CORE_COMMANDS
        assert "ip" in CORE_COMMANDS
        assert "cp" in CORE_COMMANDS
        assert "mv" in CORE_COMMANDS

    def test_is_frozenset(self):
        """Test CORE_COMMANDS is immutable."""
        assert isinstance(CORE_COMMANDS, frozenset)
