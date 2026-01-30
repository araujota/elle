"""Tests for incident pattern seed data generation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from elle.daemon.incidents.seeds import (
    SEED_PATTERNS,
    IncidentPatternSeed,
    get_pattern_by_fingerprint,
    get_patterns_by_domain,
    search_patterns,
    seed_incident_patterns,
)


# =============================================================================
# IncidentPatternSeed Model
# =============================================================================


class TestIncidentPatternSeed:
    """Tests for IncidentPatternSeed model."""

    def test_create(self) -> None:
        seed = IncidentPatternSeed(
            fingerprint="test_pattern",
            title="Test Pattern",
            domain="test",
            severity="warning",
            symptoms=("error occurred", "something failed"),
            diagnosis="This is a test error.",
            suggested_fixes=("try again", "check config"),
        )
        assert seed.fingerprint == "test_pattern"
        assert seed.title == "Test Pattern"
        assert seed.domain == "test"
        assert seed.severity == "warning"
        assert seed.symptoms == ("error occurred", "something failed")
        assert seed.diagnosis == "This is a test error."
        assert seed.suggested_fixes == ("try again", "check config")
        assert seed.capability_refs == ()

    def test_with_capability_refs(self) -> None:
        seed = IncidentPatternSeed(
            fingerprint="with_caps",
            title="Pattern with Capabilities",
            domain="service",
            severity="error",
            symptoms=("service failed",),
            diagnosis="Service crash.",
            suggested_fixes=("restart it",),
            capability_refs=("service.restart", "service.status"),
        )
        assert seed.capability_refs == ("service.restart", "service.status")

    def test_frozen(self) -> None:
        seed = IncidentPatternSeed(
            fingerprint="frozen",
            title="Frozen",
            domain="test",
            severity="info",
            symptoms=("msg",),
            diagnosis="diag",
            suggested_fixes=("fix",),
        )
        with pytest.raises(Exception):
            seed.title = "Changed"

    def test_missing_required_fields(self) -> None:
        with pytest.raises(Exception):
            IncidentPatternSeed(
                fingerprint="incomplete",
                title="Incomplete",
            )

    def test_serialization_roundtrip(self) -> None:
        seed = IncidentPatternSeed(
            fingerprint="roundtrip",
            title="Roundtrip",
            domain="net",
            severity="critical",
            symptoms=("timeout", "connection lost"),
            diagnosis="Network failure.",
            suggested_fixes=("check cable", "restart interface"),
            capability_refs=("network.diagnose",),
        )
        data = seed.model_dump()
        restored = IncidentPatternSeed.model_validate(data)
        assert restored == seed
        assert restored.fingerprint == "roundtrip"
        assert restored.capability_refs == ("network.diagnose",)


# =============================================================================
# SEED_PATTERNS Constant
# =============================================================================


class TestSeedPatterns:
    """Tests for the SEED_PATTERNS constant."""

    def test_is_tuple(self) -> None:
        assert isinstance(SEED_PATTERNS, tuple)

    def test_not_empty(self) -> None:
        assert len(SEED_PATTERNS) > 0

    def test_all_are_incident_pattern_seeds(self) -> None:
        for pattern in SEED_PATTERNS:
            assert isinstance(pattern, IncidentPatternSeed)

    def test_unique_fingerprints(self) -> None:
        fingerprints = [p.fingerprint for p in SEED_PATTERNS]
        assert len(fingerprints) == len(set(fingerprints)), "Fingerprints must be unique"

    def test_all_have_symptoms(self) -> None:
        for pattern in SEED_PATTERNS:
            assert len(pattern.symptoms) > 0, f"{pattern.fingerprint} has no symptoms"

    def test_all_have_suggested_fixes(self) -> None:
        for pattern in SEED_PATTERNS:
            assert len(pattern.suggested_fixes) > 0, f"{pattern.fingerprint} has no fixes"

    def test_all_have_non_empty_diagnosis(self) -> None:
        for pattern in SEED_PATTERNS:
            assert len(pattern.diagnosis) > 0, f"{pattern.fingerprint} has no diagnosis"

    def test_all_have_non_empty_title(self) -> None:
        for pattern in SEED_PATTERNS:
            assert len(pattern.title) > 0, f"{pattern.fingerprint} has no title"

    def test_domains_are_known(self) -> None:
        known_domains = {"db", "service", "net", "docker", "pkg", "auth", "disk", "shell"}
        for pattern in SEED_PATTERNS:
            assert pattern.domain in known_domains, (
                f"{pattern.fingerprint} has unknown domain: {pattern.domain}"
            )

    def test_severities_are_known(self) -> None:
        known_severities = {"info", "warning", "error", "critical"}
        for pattern in SEED_PATTERNS:
            assert pattern.severity in known_severities, (
                f"{pattern.fingerprint} has unknown severity: {pattern.severity}"
            )

    def test_expected_fingerprints_present(self) -> None:
        fingerprints = {p.fingerprint for p in SEED_PATTERNS}
        expected = {
            "db_auth_failed",
            "db_connection_refused",
            "service_start_failed",
            "address_in_use",
            "docker_not_running",
            "image_not_found",
            "apt_lock",
            "broken_packages",
            "permission_denied",
            "no_space_left",
            "readonly_fs",
            "connection_refused",
            "dns_resolution_failed",
            "command_not_found",
        }
        assert expected.issubset(fingerprints)


# =============================================================================
# get_pattern_by_fingerprint
# =============================================================================


class TestGetPatternByFingerprint:
    """Tests for get_pattern_by_fingerprint function."""

    def test_existing_pattern(self) -> None:
        pattern = get_pattern_by_fingerprint("db_auth_failed")
        assert pattern is not None
        assert pattern.fingerprint == "db_auth_failed"
        assert pattern.domain == "db"

    def test_nonexistent_pattern(self) -> None:
        result = get_pattern_by_fingerprint("nonexistent_fingerprint")
        assert result is None

    def test_empty_string(self) -> None:
        result = get_pattern_by_fingerprint("")
        assert result is None

    def test_all_patterns_findable(self) -> None:
        for pattern in SEED_PATTERNS:
            found = get_pattern_by_fingerprint(pattern.fingerprint)
            assert found is not None
            assert found.fingerprint == pattern.fingerprint

    def test_returns_correct_type(self) -> None:
        pattern = get_pattern_by_fingerprint("no_space_left")
        assert isinstance(pattern, IncidentPatternSeed)

    def test_service_start_failed(self) -> None:
        pattern = get_pattern_by_fingerprint("service_start_failed")
        assert pattern is not None
        assert pattern.domain == "service"
        assert "Failed to start" in pattern.symptoms

    def test_docker_not_running(self) -> None:
        pattern = get_pattern_by_fingerprint("docker_not_running")
        assert pattern is not None
        assert pattern.domain == "docker"


# =============================================================================
# get_patterns_by_domain
# =============================================================================


class TestGetPatternsByDomain:
    """Tests for get_patterns_by_domain function."""

    def test_db_domain(self) -> None:
        patterns = get_patterns_by_domain("db")
        assert len(patterns) >= 2
        for p in patterns:
            assert p.domain == "db"

    def test_service_domain(self) -> None:
        patterns = get_patterns_by_domain("service")
        assert len(patterns) >= 1
        for p in patterns:
            assert p.domain == "service"

    def test_net_domain(self) -> None:
        patterns = get_patterns_by_domain("net")
        assert len(patterns) >= 2
        for p in patterns:
            assert p.domain == "net"

    def test_docker_domain(self) -> None:
        patterns = get_patterns_by_domain("docker")
        assert len(patterns) >= 2
        for p in patterns:
            assert p.domain == "docker"

    def test_pkg_domain(self) -> None:
        patterns = get_patterns_by_domain("pkg")
        assert len(patterns) >= 2
        for p in patterns:
            assert p.domain == "pkg"

    def test_disk_domain(self) -> None:
        patterns = get_patterns_by_domain("disk")
        assert len(patterns) >= 2
        for p in patterns:
            assert p.domain == "disk"

    def test_auth_domain(self) -> None:
        patterns = get_patterns_by_domain("auth")
        assert len(patterns) >= 1
        for p in patterns:
            assert p.domain == "auth"

    def test_shell_domain(self) -> None:
        patterns = get_patterns_by_domain("shell")
        assert len(patterns) >= 1
        for p in patterns:
            assert p.domain == "shell"

    def test_nonexistent_domain(self) -> None:
        patterns = get_patterns_by_domain("nonexistent")
        assert patterns == []

    def test_empty_domain(self) -> None:
        patterns = get_patterns_by_domain("")
        assert patterns == []

    def test_returns_list(self) -> None:
        patterns = get_patterns_by_domain("db")
        assert isinstance(patterns, list)


# =============================================================================
# search_patterns
# =============================================================================


class TestSearchPatterns:
    """Tests for search_patterns function."""

    def test_db_auth_failure(self) -> None:
        matches = search_patterns("password authentication failed for user postgres")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "db_auth_failed" in fingerprints

    def test_connection_refused(self) -> None:
        matches = search_patterns("Connection refused to port 5432")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "connection_refused" in fingerprints or "db_connection_refused" in fingerprints

    def test_service_failed(self) -> None:
        matches = search_patterns("Failed to start nginx.service")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "service_start_failed" in fingerprints

    def test_docker_not_running(self) -> None:
        matches = search_patterns("Cannot connect to the Docker daemon")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "docker_not_running" in fingerprints

    def test_no_space_left(self) -> None:
        matches = search_patterns("No space left on device")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "no_space_left" in fingerprints

    def test_dns_failure(self) -> None:
        matches = search_patterns("could not resolve host example.com")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "dns_resolution_failed" in fingerprints

    def test_command_not_found(self) -> None:
        matches = search_patterns("bash: htop: command not found")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "command_not_found" in fingerprints

    def test_permission_denied(self) -> None:
        matches = search_patterns("Permission denied when accessing /etc/shadow")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "permission_denied" in fingerprints

    def test_apt_lock(self) -> None:
        matches = search_patterns("Could not get lock /var/lib/dpkg/lock-frontend")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "apt_lock" in fingerprints

    def test_no_match(self) -> None:
        matches = search_patterns("Everything is working perfectly fine")
        assert matches == []

    def test_empty_input(self) -> None:
        matches = search_patterns("")
        assert matches == []

    def test_case_insensitive(self) -> None:
        matches = search_patterns("PERMISSION DENIED accessing resource")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "permission_denied" in fingerprints

    def test_returns_list(self) -> None:
        result = search_patterns("test")
        assert isinstance(result, list)

    def test_no_duplicates(self) -> None:
        # Even if multiple symptoms match, the pattern should appear only once
        matches = search_patterns("Connection refused, could not connect to server")
        fingerprints = [m.fingerprint for m in matches]
        assert len(fingerprints) == len(set(fingerprints))

    def test_address_in_use(self) -> None:
        matches = search_patterns("Address already in use on port 80")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "address_in_use" in fingerprints

    def test_readonly_fs(self) -> None:
        matches = search_patterns("Read-only file system error when writing")
        assert len(matches) >= 1
        fingerprints = {m.fingerprint for m in matches}
        assert "readonly_fs" in fingerprints


# =============================================================================
# seed_incident_patterns (async)
# =============================================================================


class TestSeedIncidentPatterns:
    """Tests for seed_incident_patterns async function."""

    @pytest.fixture()
    def mock_store(self) -> MagicMock:
        store = MagicMock()
        store.get_by_fingerprint.return_value = None
        store.create_seed_incident.return_value = None
        return store

    @pytest.fixture()
    def mock_store_all_exist(self) -> MagicMock:
        store = MagicMock()
        store.get_by_fingerprint.return_value = {"exists": True}
        return store

    @pytest.mark.asyncio()
    async def test_seeds_all_patterns(self, mock_store: MagicMock) -> None:
        count = await seed_incident_patterns(mock_store)
        assert count == len(SEED_PATTERNS)
        assert mock_store.create_seed_incident.call_count == len(SEED_PATTERNS)

    @pytest.mark.asyncio()
    async def test_skips_existing_patterns(self, mock_store_all_exist: MagicMock) -> None:
        count = await seed_incident_patterns(mock_store_all_exist)
        assert count == 0
        mock_store_all_exist.create_seed_incident.assert_not_called()

    @pytest.mark.asyncio()
    async def test_partial_seed(self) -> None:
        store = MagicMock()
        # First pattern exists, rest do not
        first_fingerprint = SEED_PATTERNS[0].fingerprint

        def fake_get(fp: str) -> dict | None:
            if fp == first_fingerprint:
                return {"exists": True}
            return None

        store.get_by_fingerprint.side_effect = fake_get
        store.create_seed_incident.return_value = None

        count = await seed_incident_patterns(store)
        assert count == len(SEED_PATTERNS) - 1

    @pytest.mark.asyncio()
    async def test_handles_exceptions(self) -> None:
        store = MagicMock()
        store.get_by_fingerprint.return_value = None
        store.create_seed_incident.side_effect = RuntimeError("DB error")

        count = await seed_incident_patterns(store)
        # All should fail but not raise
        assert count == 0

    @pytest.mark.asyncio()
    async def test_create_seed_incident_called_with_correct_args(
        self, mock_store: MagicMock
    ) -> None:
        await seed_incident_patterns(mock_store)
        # Check the first call has correct keyword arguments
        first_call = mock_store.create_seed_incident.call_args_list[0]
        kwargs = first_call.kwargs
        first_pattern = SEED_PATTERNS[0]
        assert kwargs["fingerprint"] == first_pattern.fingerprint
        assert kwargs["title"] == first_pattern.title
        assert kwargs["domain"] == first_pattern.domain
        assert kwargs["severity"] == first_pattern.severity
        assert kwargs["symptoms"] == first_pattern.symptoms
        assert kwargs["diagnosis"] == first_pattern.diagnosis
        assert kwargs["suggested_fixes"] == first_pattern.suggested_fixes

    @pytest.mark.asyncio()
    async def test_returns_int(self, mock_store: MagicMock) -> None:
        count = await seed_incident_patterns(mock_store)
        assert isinstance(count, int)
