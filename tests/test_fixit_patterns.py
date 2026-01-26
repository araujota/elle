"""Tests for rule-based fixit error patterns."""

from elle.cli.fixit.fallback import (
    CATEGORY_FIXES,
    ERROR_PATTERNS,
    _get_alternative,
    detect_error_category,
    extract_context_values,
)
from elle.cli.fixit.models import CommandFailure, FixitContext


class TestErrorPatternCoverage:
    """Tests for error pattern coverage."""

    def test_minimum_pattern_count(self):
        """Test that we have at least 30 error patterns."""
        assert len(ERROR_PATTERNS) >= 30

    def test_minimum_category_fixes(self):
        """Test that we have fixes for at least 20 categories."""
        assert len(CATEGORY_FIXES) >= 20

    def test_all_patterns_have_three_elements(self):
        """Test that all patterns are (regex, category, explanation) tuples."""
        for pattern in ERROR_PATTERNS:
            assert len(pattern) == 3
            assert hasattr(pattern[0], "search")  # regex
            assert isinstance(pattern[1], str)  # category
            assert isinstance(pattern[2], str)  # explanation


class TestPackageManagerPatterns:
    """Tests for package manager error patterns."""

    def test_apt_lock_detected(self):
        """Test APT lock error detection."""
        category, _ = detect_error_category("E: Could not get lock /var/lib/dpkg/lock-frontend")
        assert category == "apt_lock"

    def test_dpkg_interrupted_detected(self):
        """Test dpkg interrupted error detection."""
        category, _ = detect_error_category("E: dpkg was interrupted, you must manually run 'dpkg --configure -a'")
        assert category == "dpkg_interrupted"

    def test_broken_packages_detected(self):
        """Test broken packages error detection."""
        category, _ = detect_error_category("E: Broken packages")
        assert category == "broken_packages"

    def test_held_packages_detected(self):
        """Test held packages error detection."""
        category, _ = detect_error_category("E: Unable to correct problems, you have held broken packages")
        assert category == "held_packages"

    def test_hash_mismatch_detected(self):
        """Test Hash Sum mismatch error detection."""
        category, _ = detect_error_category("E: Failed to fetch... Hash Sum mismatch")
        assert category == "apt_hash_mismatch"

    def test_apt_key_missing_detected(self):
        """Test missing apt key error detection."""
        category, _ = detect_error_category("W: GPG error: NO_PUBKEY ABC123")
        assert category == "apt_key_missing"


class TestDockerPatterns:
    """Tests for Docker error patterns."""

    def test_docker_not_running_detected(self):
        """Test Docker daemon not running detection."""
        category, _ = detect_error_category("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
        assert category == "docker_not_running"

    def test_container_not_found_detected(self):
        """Test container not found detection."""
        category, _ = detect_error_category("Error: No such container: mycontainer")
        assert category == "container_not_found"

    def test_image_not_found_detected(self):
        """Test image not found detection."""
        category, _ = detect_error_category("Error response from daemon: manifest for nginx:invalid not found")
        assert category == "image_not_found"

    def test_port_in_use_detected(self):
        """Test port already allocated detection."""
        category, _ = detect_error_category("Error response from daemon: driver failed: port is already allocated")
        assert category == "port_in_use"

    def test_container_runtime_error_detected(self):
        """Test OCI runtime error detection."""
        category, _ = detect_error_category("Error response from daemon: OCI runtime create failed: container_linux.go")
        assert category == "container_runtime_error"


class TestFilesystemPatterns:
    """Tests for filesystem error patterns."""

    def test_readonly_fs_detected(self):
        """Test read-only filesystem detection."""
        category, _ = detect_error_category("touch: cannot touch 'file': Read-only file system")
        assert category == "readonly_fs"

    def test_device_busy_detected(self):
        """Test device busy detection."""
        category, _ = detect_error_category("umount: /mnt/data: Device or resource busy")
        assert category == "device_busy"

    def test_stale_nfs_detected(self):
        """Test stale NFS handle detection."""
        category, _ = detect_error_category("ls: cannot access '/mnt/nfs': Stale file handle")
        assert category == "stale_nfs"

    def test_fs_corrupt_detected(self):
        """Test filesystem corruption detection."""
        category, _ = detect_error_category("EXT4-fs error: Structure needs cleaning")
        assert category == "fs_corrupt"

    def test_io_error_detected(self):
        """Test I/O error detection."""
        category, _ = detect_error_category("dd: error writing '/dev/sda': Input/output error")
        assert category == "io_error"


class TestServicePatterns:
    """Tests for service/systemd error patterns."""

    def test_service_start_failed_detected(self):
        """Test service start failure detection."""
        category, _ = detect_error_category("Job for nginx.service failed because the control process exited")
        assert category == "service_start_failed"

    def test_address_in_use_detected(self):
        """Test address already in use detection."""
        category, _ = detect_error_category("nginx: [emerg] bind() to 0.0.0.0:80 failed: Address already in use")
        assert category == "address_in_use"

    def test_service_dep_failed_detected(self):
        """Test service dependency failed detection."""
        category, _ = detect_error_category("Job for myservice.service failed because a dependency failed")
        assert category == "service_dep_failed"

    def test_unit_not_found_detected(self):
        """Test unit not found detection."""
        category, _ = detect_error_category("Unit myservice.service not found")
        assert category == "unit_not_found"


class TestDatabasePatterns:
    """Tests for database error patterns."""

    def test_db_locked_detected(self):
        """Test database locked detection."""
        category, _ = detect_error_category("sqlite3.OperationalError: database is locked")
        assert category == "db_locked"

    def test_db_auth_failed_detected(self):
        """Test database auth failed detection."""
        category, _ = detect_error_category('FATAL: password authentication failed for user "postgres"')
        assert category == "db_auth_failed"

    def test_db_connection_refused_postgres(self):
        """Test PostgreSQL connection refused detection."""
        category, _ = detect_error_category("could not connect to server: Connection refused on port 5432")
        assert category == "db_connection_refused"

    def test_db_connection_refused_mysql(self):
        """Test MySQL connection refused detection."""
        category, _ = detect_error_category(
            "ERROR 2003 (HY000): Can't connect to MySQL server on 'localhost' (Connection refused):3306"
        )
        assert category == "db_connection_refused"


class TestCategoryFixes:
    """Tests for category fix suggestions."""

    def test_apt_lock_has_fixes(self):
        """Test that apt_lock has fix suggestions."""
        assert "apt_lock" in CATEGORY_FIXES
        assert len(CATEGORY_FIXES["apt_lock"]) >= 1

    def test_docker_not_running_has_fixes(self):
        """Test that docker_not_running has fix suggestions."""
        assert "docker_not_running" in CATEGORY_FIXES
        fixes = CATEGORY_FIXES["docker_not_running"]
        assert len(fixes) >= 2
        # Should include starting docker
        commands = [f["command"] for f in fixes]
        assert any("start docker" in cmd for cmd in commands)

    def test_readonly_fs_has_fixes(self):
        """Test that readonly_fs has fix suggestions."""
        assert "readonly_fs" in CATEGORY_FIXES
        assert len(CATEGORY_FIXES["readonly_fs"]) >= 1

    def test_service_start_failed_has_fixes(self):
        """Test that service_start_failed has fix suggestions."""
        assert "service_start_failed" in CATEGORY_FIXES
        fixes = CATEGORY_FIXES["service_start_failed"]
        # Should suggest checking logs
        commands = [f["command"] for f in fixes]
        assert any("journalctl" in cmd for cmd in commands)

    def test_all_new_categories_have_fixes(self):
        """Test that all new categories have fix suggestions."""
        new_categories = [
            "apt_lock",
            "dpkg_interrupted",
            "broken_packages",
            "docker_not_running",
            "container_not_found",
            "readonly_fs",
            "device_busy",
            "service_start_failed",
            "address_in_use",
            "db_locked",
            "db_auth_failed",
        ]
        for cat in new_categories:
            assert cat in CATEGORY_FIXES, f"Missing fixes for {cat}"
            assert len(CATEGORY_FIXES[cat]) >= 1, f"No fixes for {cat}"


class TestContextExtraction:
    """Tests for context value extraction."""

    def test_extract_service_from_error(self):
        """Test extracting service name from systemd error."""
        failure = CommandFailure(
            command="systemctl start myapp",
            exit_code=1,
            stderr="Job for myapp.service failed because the control process exited",
        )
        context = FixitContext(failure=failure)
        values = extract_context_values(context)
        assert values["service"] == "myapp"

    def test_extract_port_from_error(self):
        """Test extracting port from bind error."""
        failure = CommandFailure(
            command="nginx",
            exit_code=1,
            stderr="nginx: [emerg] bind() to 0.0.0.0:8080 failed",
        )
        context = FixitContext(failure=failure)
        values = extract_context_values(context)
        assert values["port"] == "8080"

    def test_extract_container_from_error(self):
        """Test extracting container name from docker error."""
        failure = CommandFailure(
            command="docker logs mycontainer",
            exit_code=1,
            stderr="Error: No such container: mycontainer",
        )
        context = FixitContext(failure=failure)
        values = extract_context_values(context)
        assert values["container"] == "mycontainer"

    def test_extract_service_from_systemctl_command(self):
        """Test extracting service from systemctl command."""
        failure = CommandFailure(
            command="systemctl restart nginx",
            exit_code=1,
            stderr="Failed to restart nginx.service",
        )
        context = FixitContext(failure=failure)
        values = extract_context_values(context)
        assert values["service"] == "nginx"


class TestAlternatives:
    """Tests for alternative approach suggestions."""

    def test_apt_lock_has_alternative(self):
        """Test that apt_lock has alternative suggestion."""
        alt = _get_alternative("apt_lock")
        assert alt is not None
        assert "lock" in alt.lower() or "wait" in alt.lower()

    def test_docker_not_running_has_alternative(self):
        """Test that docker_not_running has alternative suggestion."""
        alt = _get_alternative("docker_not_running")
        assert alt is not None
        assert "start" in alt.lower() or "docker" in alt.lower()

    def test_io_error_has_alternative(self):
        """Test that io_error has alternative suggestion."""
        alt = _get_alternative("io_error")
        assert alt is not None
        assert "hardware" in alt.lower() or "disk" in alt.lower()

    def test_db_auth_failed_has_alternative(self):
        """Test that db_auth_failed has alternative suggestion."""
        alt = _get_alternative("db_auth_failed")
        assert alt is not None
        assert "password" in alt.lower() or "auth" in alt.lower()
