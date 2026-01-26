"""Tests for semantic diff generation.

Tests the semantic diff generation for system snapshots
and config file changes.
"""

from datetime import datetime, timedelta

from elle.daemon.incidents.models import (
    ConfigFileState,
    PackageState,
    SemanticChange,
    SemanticDiff,
    SystemSnapshot,
)
from elle.daemon.incidents.semantic_diff import (
    _config_significance,
    _diff_configs,
    _diff_docker,
    _diff_interfaces,
    _diff_kernel,
    _diff_packages,
    _diff_services,
    build_config_state,
    compute_file_hash,
    generate_semantic_diff,
)


def make_snapshot(**overrides) -> SystemSnapshot:
    """Create a SystemSnapshot with defaults."""
    defaults = {
        "os": "Ubuntu 24.04",
        "kernel": "6.5.0-generic",
        "uptime_sec": 86400,
        "hostname": "test-host",
        "cpu_load": (0.5, 0.4, 0.3),
        "mem_total_mb": 16384,
        "mem_free_mb": 8192,
        "mem_available_mb": 10240,
        "collected_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    return SystemSnapshot(**defaults)


class TestSemanticChangeModel:
    """Tests for SemanticChange model."""

    def test_semantic_change_creation(self):
        """Test creating a SemanticChange."""
        change = SemanticChange(
            category="config",
            description="SSH root login disabled",
            technical_detail="/etc/ssh/sshd_config: PermitRootLogin no",
            significance="high",
        )
        assert change.category == "config"
        assert change.description == "SSH root login disabled"
        assert change.significance == "high"

    def test_semantic_change_defaults(self):
        """Test SemanticChange defaults."""
        change = SemanticChange(
            category="service",
            description="nginx restarted",
        )
        assert change.technical_detail == ""
        assert change.significance == "medium"


class TestSemanticDiffModel:
    """Tests for SemanticDiff model."""

    def test_semantic_diff_creation(self):
        """Test creating a SemanticDiff."""
        now = datetime.utcnow()
        then = now - timedelta(hours=1)
        diff = SemanticDiff(
            from_time=then,
            to_time=now,
            changes=(
                SemanticChange(
                    category="kernel",
                    description="Kernel updated",
                    significance="high",
                ),
            ),
        )
        assert diff.from_time == then
        assert diff.to_time == now
        assert len(diff.changes) == 1

    def test_semantic_diff_summary_empty(self):
        """Test summary with no changes."""
        diff = SemanticDiff(
            from_time=datetime.utcnow(),
            to_time=datetime.utcnow(),
        )
        assert "No significant changes" in diff.summary()

    def test_semantic_diff_summary_with_changes(self):
        """Test summary with changes."""
        diff = SemanticDiff(
            from_time=datetime.utcnow(),
            to_time=datetime.utcnow(),
            changes=(
                SemanticChange(
                    category="kernel",
                    description="Kernel updated",
                    significance="high",
                ),
                SemanticChange(
                    category="service",
                    description="nginx restarted",
                    significance="medium",
                ),
            ),
        )
        summary = diff.summary()
        assert "Kernel updated" in summary

    def test_semantic_diff_summary_truncates(self):
        """Test summary truncates with many changes."""
        changes = tuple(
            SemanticChange(
                category="service",
                description=f"Service {i} changed",
                significance="high",
            )
            for i in range(10)
        )
        diff = SemanticDiff(
            from_time=datetime.utcnow(),
            to_time=datetime.utcnow(),
            changes=changes,
        )
        summary = diff.summary()
        assert "+7 more" in summary


class TestKernelDiff:
    """Tests for kernel diff detection."""

    def test_kernel_change_detected(self):
        """Test kernel version change is detected."""
        before = make_snapshot(kernel="6.5.0-generic")
        after = make_snapshot(kernel="6.6.0-generic")
        changes = _diff_kernel(before, after)
        assert len(changes) == 1
        assert changes[0].category == "kernel"
        assert "6.5.0" in changes[0].description
        assert "6.6.0" in changes[0].description
        assert changes[0].significance == "high"

    def test_no_kernel_change(self):
        """Test no change when kernel is same."""
        before = make_snapshot(kernel="6.5.0-generic")
        after = make_snapshot(kernel="6.5.0-generic")
        changes = _diff_kernel(before, after)
        assert len(changes) == 0


class TestServiceDiff:
    """Tests for service state diff detection."""

    def test_service_started(self):
        """Test service start is detected."""
        before_services = ({"name": "nginx", "active": False, "failed": False},)
        after_services = ({"name": "nginx", "active": True, "failed": False},)
        changes = _diff_services(before_services, after_services)
        assert len(changes) == 1
        assert "nginx" in changes[0].description
        assert "started" in changes[0].description

    def test_service_stopped(self):
        """Test service stop is detected."""
        before_services = ({"name": "nginx", "active": True, "failed": False},)
        after_services = ({"name": "nginx", "active": False, "failed": False},)
        changes = _diff_services(before_services, after_services)
        assert len(changes) == 1
        assert "stopped" in changes[0].description

    def test_service_failed(self):
        """Test service failure is detected when service was already stopped."""
        # Service was already inactive, now enters failed state
        before_services = ({"name": "nginx", "active": False, "failed": False},)
        after_services = ({"name": "nginx", "active": False, "failed": True},)
        changes = _diff_services(before_services, after_services)
        assert any("failed" in c.description for c in changes)

    def test_service_recovered(self):
        """Test service recovery is detected."""
        before_services = ({"name": "nginx", "active": False, "failed": True},)
        after_services = ({"name": "nginx", "active": True, "failed": False},)
        changes = _diff_services(before_services, after_services)
        # Started and recovered
        assert len(changes) >= 1


class TestInterfaceDiff:
    """Tests for network interface diff detection."""

    def test_interface_up(self):
        """Test interface coming up is detected."""
        before = ({"name": "eth0", "state": "down", "ip": None},)
        after = ({"name": "eth0", "state": "up", "ip": "192.168.1.1"},)
        changes = _diff_interfaces(before, after)
        assert any("came up" in c.description for c in changes)

    def test_interface_down(self):
        """Test interface going down is detected."""
        before = ({"name": "eth0", "state": "up", "ip": "192.168.1.1"},)
        after = ({"name": "eth0", "state": "down", "ip": None},)
        changes = _diff_interfaces(before, after)
        assert any("went down" in c.description for c in changes)
        assert any(c.significance == "high" for c in changes)

    def test_ip_change(self):
        """Test IP address change is detected."""
        before = ({"name": "eth0", "state": "up", "ip": "192.168.1.1"},)
        after = ({"name": "eth0", "state": "up", "ip": "192.168.1.2"},)
        changes = _diff_interfaces(before, after)
        assert any("IP address changed" in c.description for c in changes)

    def test_loopback_ignored(self):
        """Test loopback interface is ignored."""
        before = ({"name": "lo", "state": "up", "ip": "127.0.0.1"},)
        after = ({"name": "lo", "state": "down", "ip": None},)
        changes = _diff_interfaces(before, after)
        assert len(changes) == 0


class TestDockerDiff:
    """Tests for Docker container diff detection."""

    def test_containers_started(self):
        """Test container start is detected."""
        before = make_snapshot(docker_running=1)
        after = make_snapshot(docker_running=3)
        changes = _diff_docker(before, after)
        assert len(changes) == 1
        assert "2" in changes[0].description
        assert "started" in changes[0].description

    def test_containers_stopped(self):
        """Test container stop is detected."""
        before = make_snapshot(docker_running=3)
        after = make_snapshot(docker_running=1)
        changes = _diff_docker(before, after)
        assert len(changes) == 1
        assert "stopped" in changes[0].description

    def test_containers_exited(self):
        """Test container exit is detected."""
        before = make_snapshot(docker_exited=0)
        after = make_snapshot(docker_exited=2)
        changes = _diff_docker(before, after)
        assert len(changes) == 1
        assert "exited" in changes[0].description


class TestPackageDiff:
    """Tests for package version diff detection."""

    def test_package_upgrade_detected(self):
        """Test package upgrade is detected."""
        before = make_snapshot(
            packages=(PackageState(name="nginx", version="1.18.0", is_bedrock=False),),
        )
        after = make_snapshot(
            packages=(PackageState(name="nginx", version="1.20.0", is_bedrock=False),),
        )
        changes = _diff_packages(before, after)
        assert len(changes) == 1
        assert changes[0].category == "package"
        assert "1.18.0" in changes[0].description
        assert "1.20.0" in changes[0].description
        assert "upgraded" in changes[0].description

    def test_bedrock_package_upgrade_is_high_significance(self):
        """Test bedrock package upgrade has high significance."""
        before = make_snapshot(
            packages=(PackageState(name="systemd", version="253", is_bedrock=True),),
        )
        after = make_snapshot(
            packages=(PackageState(name="systemd", version="254", is_bedrock=True),),
        )
        changes = _diff_packages(before, after)
        assert len(changes) == 1
        assert changes[0].significance == "high"

    def test_package_installed_detected(self):
        """Test new package installation is detected."""
        before = make_snapshot(packages=())
        after = make_snapshot(
            packages=(PackageState(name="nginx", version="1.18.0", is_bedrock=False),),
        )
        changes = _diff_packages(before, after)
        assert len(changes) == 1
        assert "installed" in changes[0].description
        assert "nginx" in changes[0].description

    def test_package_removed_detected(self):
        """Test package removal is detected."""
        before = make_snapshot(
            packages=(PackageState(name="nginx", version="1.18.0", is_bedrock=False),),
        )
        after = make_snapshot(packages=())
        changes = _diff_packages(before, after)
        assert len(changes) == 1
        assert "removed" in changes[0].description
        assert "nginx" in changes[0].description

    def test_no_change_no_diff(self):
        """Test no changes produces no diff."""
        packages = (PackageState(name="nginx", version="1.18.0"),)
        before = make_snapshot(packages=packages)
        after = make_snapshot(packages=packages)
        changes = _diff_packages(before, after)
        assert len(changes) == 0

    def test_multiple_package_changes(self):
        """Test multiple package changes are detected."""
        before = make_snapshot(
            packages=(
                PackageState(name="nginx", version="1.18.0"),
                PackageState(name="apache2", version="2.4.0"),
            ),
        )
        after = make_snapshot(
            packages=(
                PackageState(name="nginx", version="1.20.0"),  # upgraded
                # apache2 removed
                PackageState(name="traefik", version="2.9.0"),  # installed
            ),
        )
        changes = _diff_packages(before, after)
        assert len(changes) == 3
        categories = {c.description.split()[0] for c in changes}
        assert "nginx" in categories or any("nginx" in c.description for c in changes)


class TestConfigDiff:
    """Tests for config file diff detection."""

    def test_config_modified(self):
        """Test config modification is detected."""
        before = (
            ConfigFileState(
                path="/etc/ssh/sshd_config",
                sha256_after="abc123",
            ),
        )
        after = (
            ConfigFileState(
                path="/etc/ssh/sshd_config",
                sha256_after="def456",
            ),
        )
        changes = _diff_configs(before, after)
        assert len(changes) == 1
        assert "sshd_config" in changes[0].description
        assert changes[0].category == "config"

    def test_new_config_tracked(self):
        """Test new config file is detected."""
        before: tuple = ()
        after = (
            ConfigFileState(
                path="/etc/nginx/nginx.conf",
                sha256_after="abc123",
            ),
        )
        changes = _diff_configs(before, after)
        assert len(changes) == 1
        assert "nginx.conf" in changes[0].description


class TestConfigSignificance:
    """Tests for config file significance determination."""

    def test_ssh_is_high(self):
        """Test SSH config is high significance."""
        assert _config_significance("/etc/ssh/sshd_config") == "high"

    def test_sudoers_is_high(self):
        """Test sudoers is high significance."""
        assert _config_significance("/etc/sudoers") == "high"

    def test_fstab_is_medium(self):
        """Test fstab is medium significance."""
        assert _config_significance("/etc/fstab") == "medium"

    def test_unknown_is_low(self):
        """Test unknown config is low significance."""
        assert _config_significance("/etc/myapp/config.yaml") == "low"


class TestGenerateSemanticDiff:
    """Tests for the main generate_semantic_diff function."""

    def test_generate_full_diff(self):
        """Test generating a full semantic diff."""
        before = make_snapshot(
            kernel="6.5.0-generic",
            services=({"name": "nginx", "active": True, "failed": False},),
        )
        after = make_snapshot(
            kernel="6.6.0-generic",
            services=({"name": "nginx", "active": False, "failed": False},),
        )
        config_before = (
            ConfigFileState(
                path="/etc/nginx/nginx.conf",
                sha256_after="old",
            ),
        )
        config_after = (
            ConfigFileState(
                path="/etc/nginx/nginx.conf",
                sha256_after="new",
            ),
        )

        diff = generate_semantic_diff(before, after, config_before, config_after)

        assert diff.from_time == before.collected_at
        assert diff.to_time == after.collected_at
        assert len(diff.changes) >= 2  # kernel + service at minimum

        categories = {c.category for c in diff.changes}
        assert "kernel" in categories
        assert "service" in categories


class TestFileHashUtilities:
    """Tests for file hash utility functions."""

    def test_compute_file_hash(self, tmp_path):
        """Test computing file hash."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")
        hash_result = compute_file_hash(test_file)
        assert hash_result is not None
        assert len(hash_result) == 64  # SHA256 hex length

    def test_compute_file_hash_nonexistent(self, tmp_path):
        """Test hash of nonexistent file returns None."""
        hash_result = compute_file_hash(tmp_path / "nonexistent.txt")
        assert hash_result is None

    def test_build_config_state(self, tmp_path):
        """Test building config state from file."""
        test_file = tmp_path / "config.conf"
        test_file.write_text("setting=value")

        state = build_config_state(
            path=test_file,
            sha256_before="oldhash",
            backup_path="/backup/path",
        )

        assert state.path == str(test_file)
        assert state.sha256_before == "oldhash"
        assert state.sha256_after is not None
        assert state.size_bytes > 0
        assert state.mtime is not None
        assert state.backup_path == "/backup/path"

    def test_build_config_state_nonexistent(self, tmp_path):
        """Test building state for nonexistent file."""
        state = build_config_state(
            path=tmp_path / "nonexistent.conf",
            sha256_before="oldhash",
        )

        assert state.sha256_after is None
        assert state.size_bytes == 0
        assert state.mtime is None
