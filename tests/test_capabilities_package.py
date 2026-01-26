"""Tests for Package capabilities."""

from __future__ import annotations

from unittest.mock import patch

from elle.capabilities.core.package import (
    PACKAGE_CAPABILITIES,
    PackageConflictDetectCapability,
    PackageConflictInput,
    _parse_apt_conflicts,
)


class TestPackageConflictDetectCapability:
    """Tests for package conflict detection capability."""

    def test_spec_properties(self):
        """Verify capability spec is correctly defined."""
        cap = PackageConflictDetectCapability()
        spec = cap.spec

        assert spec.name == "package.detect-conflicts"
        assert spec.domain == "package"
        assert spec.risk == "none"
        assert spec.requires_privilege is False
        assert spec.side_effects == ()
        assert spec.idempotent is True

    def test_dry_run_returns_commands(self):
        """Dry run should list commands that would be executed."""
        cap = PackageConflictDetectCapability()
        result = cap.dry_run(PackageConflictInput(package="nginx"))

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert "apt-get install --dry-run" in str(result.would_execute)
        assert "dpkg --audit" in str(result.would_execute)
        assert "apt-mark showhold" in str(result.would_execute)

    @patch("elle.capabilities.core.package._run_apt")
    @patch("elle.capabilities.core.package._get_held_packages")
    @patch("elle.capabilities.core.package._get_broken_packages")
    def test_detects_dependency_conflict(self, mock_broken, mock_held, mock_run):
        """Should detect 'Depends: X but Y is installed' patterns."""
        mock_run.return_value = (
            False,
            "",
            "Depends: libfoo (>= 2.0) but 1.5 is installed",
            1,
        )
        mock_held.return_value = []
        mock_broken.return_value = []

        cap = PackageConflictDetectCapability()
        result = cap.run(PackageConflictInput(package="broken-pkg"))

        assert result.success is True
        assert result.output.has_conflicts is True
        assert len(result.output.conflicts) > 0
        assert result.output.conflicts[0].conflict_type == "depends"
        assert result.output.conflicts[0].conflicting_package == "libfoo"

    @patch("elle.capabilities.core.package._run_apt")
    @patch("elle.capabilities.core.package._get_held_packages")
    @patch("elle.capabilities.core.package._get_broken_packages")
    def test_detects_held_packages(self, mock_broken, mock_held, mock_run):
        """Should detect held packages blocking installation."""
        mock_run.return_value = (True, "", "", 0)
        mock_held.return_value = ["nginx", "apache2"]
        mock_broken.return_value = []

        cap = PackageConflictDetectCapability()
        result = cap.run(PackageConflictInput(package="nginx"))

        # nginx is held, should be flagged
        assert result.success is True
        assert result.output.has_conflicts is True
        assert "nginx" in result.output.held_packages
        # Should have conflict for the held package
        held_conflicts = [c for c in result.output.conflicts if c.conflict_type == "held"]
        assert len(held_conflicts) > 0
        assert "Held packages" in str(result.output.recommendations)

    @patch("elle.capabilities.core.package._run_apt")
    @patch("elle.capabilities.core.package._get_held_packages")
    @patch("elle.capabilities.core.package._get_broken_packages")
    def test_detects_broken_dpkg_state(self, mock_broken, mock_held, mock_run):
        """Should detect broken dpkg state via audit."""
        mock_run.return_value = (True, "", "", 0)
        mock_held.return_value = []
        mock_broken.return_value = ["broken-package needs configuration"]

        cap = PackageConflictDetectCapability()
        result = cap.run(PackageConflictInput(package="test-pkg"))

        assert result.success is True
        assert result.output.has_conflicts is True
        assert len(result.output.broken_deps) > 0
        assert "dpkg --configure -a" in str(result.output.recommendations)

    @patch("elle.capabilities.core.package._run_apt")
    @patch("elle.capabilities.core.package._get_held_packages")
    @patch("elle.capabilities.core.package._get_broken_packages")
    def test_no_conflicts_found(self, mock_broken, mock_held, mock_run):
        """Should return empty conflicts when package is installable."""
        mock_run.return_value = (True, "nginx is already the newest version", "", 0)
        mock_held.return_value = []
        mock_broken.return_value = []

        cap = PackageConflictDetectCapability()
        result = cap.run(PackageConflictInput(package="nginx"))

        assert result.success is True
        assert result.output.has_conflicts is False
        assert len(result.output.conflicts) == 0
        assert len(result.output.broken_deps) == 0

    @patch("elle.capabilities.core.package._run_apt")
    @patch("elle.capabilities.core.package._get_held_packages")
    @patch("elle.capabilities.core.package._get_broken_packages")
    def test_evidence_includes_man_citations(self, mock_broken, mock_held, mock_run):
        """Evidence should cite relevant man pages."""
        mock_run.return_value = (True, "", "", 0)
        mock_held.return_value = []
        mock_broken.return_value = []

        cap = PackageConflictDetectCapability()
        result = cap.run(PackageConflictInput(package="test"))

        assert "apt-get(8)" in result.evidence.man_vault_citations
        assert "dpkg(1)" in result.evidence.man_vault_citations
        assert "apt-cache(8)" in result.evidence.man_vault_citations

    @patch("elle.capabilities.core.package._run_apt")
    @patch("elle.capabilities.core.package._get_held_packages")
    @patch("elle.capabilities.core.package._get_broken_packages")
    def test_detects_package_not_found(self, mock_broken, mock_held, mock_run):
        """Should detect when package is not found."""
        mock_run.return_value = (
            False,
            "",
            "E: Unable to locate package nonexistent-pkg",
            100,
        )
        mock_held.return_value = []
        mock_broken.return_value = []

        cap = PackageConflictDetectCapability()
        result = cap.run(PackageConflictInput(package="nonexistent-pkg"))

        assert result.success is True
        assert "not found" in str(result.output.recommendations).lower() or "apt update" in str(
            result.output.recommendations
        )

    @patch("elle.capabilities.core.package._run_apt")
    @patch("elle.capabilities.core.package._get_held_packages")
    @patch("elle.capabilities.core.package._get_broken_packages")
    def test_detects_breaks_conflict(self, mock_broken, mock_held, mock_run):
        """Should detect Breaks/Conflicts relationships."""
        mock_run.return_value = (
            False,
            "",
            "Breaks: libbar (<< 3.0) but 2.5 is installed",
            1,
        )
        mock_held.return_value = []
        mock_broken.return_value = []

        cap = PackageConflictDetectCapability()
        result = cap.run(PackageConflictInput(package="test-pkg"))

        assert result.success is True
        assert result.output.has_conflicts is True
        assert len(result.output.conflicts) > 0
        assert result.output.conflicts[0].conflicting_package == "libbar"


class TestParseAptConflicts:
    """Tests for the apt conflict parsing helper."""

    def test_parses_depends_conflict(self):
        """Should parse Depends: X but Y is installed."""
        stderr = "Depends: libfoo (>= 2.0) but 1.5 is installed"
        conflicts = _parse_apt_conflicts(stderr)

        assert len(conflicts) == 1
        assert conflicts[0].conflicting_package == "libfoo"
        assert conflicts[0].conflict_type == "depends"
        assert conflicts[0].required_version == ">= 2.0"
        assert conflicts[0].installed_version == "1.5"

    def test_parses_breaks_conflict(self):
        """Should parse Breaks: X but Y is installed."""
        stderr = "Breaks: libbar (<< 3.0) but 2.5 is installed"
        conflicts = _parse_apt_conflicts(stderr)

        assert len(conflicts) == 1
        assert conflicts[0].conflicting_package == "libbar"
        assert conflicts[0].conflict_type == "breaks"

    def test_parses_multiple_conflicts(self):
        """Should parse multiple conflict lines."""
        stderr = """Depends: libfoo (>= 2.0) but 1.5 is installed
Depends: libbar (>= 1.0) but 0.5 is installed"""
        conflicts = _parse_apt_conflicts(stderr)

        assert len(conflicts) == 2

    def test_empty_output(self):
        """Should return empty list for no conflicts."""
        conflicts = _parse_apt_conflicts("")
        assert len(conflicts) == 0


class TestPackageCapabilitiesRegistry:
    """Tests for Package capabilities registration."""

    def test_all_capabilities_exported(self):
        """Test all capabilities are in PACKAGE_CAPABILITIES."""
        assert len(PACKAGE_CAPABILITIES) == 5

        names = {cap().spec.name for cap in PACKAGE_CAPABILITIES}
        expected = {
            "package.install",
            "package.remove",
            "package.update",
            "package.info",
            "package.detect-conflicts",
        }
        assert names == expected
