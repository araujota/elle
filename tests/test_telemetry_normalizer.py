"""Tests for the telemetry normalizer."""

import pytest
from datetime import UTC, datetime

from elle.daemon.telemetry.normalizer import (
    Normalizer,
    detect_category,
    extract_entity,
    priority_to_severity,
    parse_timestamp,
    generate_fingerprint,
)


class TestPriorityMapping:
    """Tests for priority to severity mapping."""

    def test_emergency_is_critical(self):
        assert priority_to_severity(0) == "critical"

    def test_alert_is_critical(self):
        assert priority_to_severity(1) == "critical"

    def test_crit_is_critical(self):
        assert priority_to_severity(2) == "critical"

    def test_error_is_error(self):
        assert priority_to_severity(3) == "error"

    def test_warning_is_warning(self):
        assert priority_to_severity(4) == "warning"

    def test_notice_is_notice(self):
        assert priority_to_severity(5) == "notice"

    def test_info_is_info(self):
        assert priority_to_severity(6) == "info"

    def test_debug_is_debug(self):
        assert priority_to_severity(7) == "debug"

    def test_string_priority(self):
        assert priority_to_severity("3") == "error"
        assert priority_to_severity("6") == "info"

    def test_none_defaults_to_info(self):
        assert priority_to_severity(None) == "info"

    def test_invalid_defaults_to_info(self):
        assert priority_to_severity("invalid") == "info"
        assert priority_to_severity(99) == "info"


class TestCategoryDetection:
    """Tests for category detection from message content."""

    def test_oom_detection(self):
        assert detect_category("Out of memory: Killed process 1234") == "oom"
        assert detect_category("oom-kill: constraint=NONE") == "oom"
        assert detect_category("invoked oom-killer: gfp_mask=0x123") == "oom"
        assert detect_category("cannot allocate memory") == "oom"

    def test_disk_detection(self):
        assert detect_category("I/O error, dev sda, sector 1234") == "disk"
        assert detect_category("Buffer I/O error on device nvme0n1") == "disk"
        assert detect_category("No space left on device") == "disk"
        assert detect_category("ENOSPC: disk full") == "disk"

    def test_net_detection(self):
        assert detect_category("eth0: link up") == "net"
        assert detect_category("carrier lost on enp0s3") == "net"
        assert detect_category("connection refused") == "net"
        assert detect_category("NetworkManager: device eth0 state change") == "net"
        assert detect_category("DHCP lease expired") == "net"

    def test_auth_detection(self):
        assert detect_category("pam_unix: authentication failure") == "auth"
        assert detect_category("sshd: Failed password for root") == "auth"
        assert detect_category("sudo: incorrect password attempts") == "auth"
        assert detect_category("invalid user admin from 192.168.1.1") == "auth"

    def test_service_detection(self):
        assert detect_category("Started nginx.service") == "service"
        assert detect_category("Failed to start postgresql.service") == "service"
        assert detect_category("Unit docker.service entered failed state") == "service"

    def test_thermal_detection(self):
        assert detect_category("thermal throttling triggered") == "thermal"
        assert detect_category("CPU0 package temperature above threshold") == "thermal"

    def test_smart_detection(self):
        assert detect_category("SMART Health Status: PASSED") == "smart"
        assert detect_category("smartd: Device /dev/sda - attention") == "smart"

    def test_pkg_detection(self):
        assert detect_category("apt-get install package") == "pkg"
        assert detect_category("dpkg: dependency problems") == "pkg"
        assert detect_category("unmet dependencies for package") == "pkg"

    def test_docker_detection(self):
        assert detect_category("docker container started") == "docker"
        assert detect_category("containerd: starting container") == "docker"

    def test_fs_detection(self):
        assert detect_category("EXT4-fs error") == "fs"
        assert detect_category("mount: /dev/sda1 mounted on /mnt") == "fs"

    def test_unknown_is_other(self):
        assert detect_category("Just a random log message") == "other"
        assert detect_category("") == "other"


class TestEntityExtraction:
    """Tests for entity extraction from messages."""

    def test_service_extraction(self):
        msg = "Started nginx.service"
        entity = extract_entity(msg, "service", {})
        assert entity == "service:nginx"

    def test_service_from_raw(self):
        raw = {"_SYSTEMD_UNIT": "postgresql.service"}
        entity = extract_entity("Unit entered failed state", "service", raw)
        assert entity == "service:postgresql"

    def test_interface_extraction(self):
        msg = "eth0: link is down"
        entity = extract_entity(msg, "net", {})
        assert entity == "interface:eth0"

        msg = "enp0s3: carrier lost"
        entity = extract_entity(msg, "net", {})
        assert entity == "interface:enp0s3"

    def test_disk_extraction(self):
        msg = "I/O error on /dev/sda"
        entity = extract_entity(msg, "disk", {})
        assert entity == "disk:/dev/sda"

        msg = "Error on /dev/nvme0n1p2"
        entity = extract_entity(msg, "disk", {})
        assert entity == "disk:/dev/nvme0n1p2"

    def test_container_extraction(self):
        msg = "container abc123def456 started"
        entity = extract_entity(msg, "docker", {})
        assert entity == "container:abc123def456"

    def test_no_entity(self):
        msg = "Just a random message"
        entity = extract_entity(msg, "other", {})
        assert entity is None


class TestTimestampParsing:
    """Tests for timestamp parsing from raw data."""

    def test_realtime_timestamp(self):
        # Microseconds since epoch
        raw = {"__REALTIME_TIMESTAMP": "1704067200000000"}  # 2024-01-01 00:00:00 UTC
        ts = parse_timestamp(raw)
        assert ts.year == 2024
        assert ts.month == 1
        assert ts.day == 1

    def test_source_realtime_timestamp(self):
        raw = {"_SOURCE_REALTIME_TIMESTAMP": "1704067200000000"}
        ts = parse_timestamp(raw)
        assert ts.year == 2024

    def test_missing_timestamp(self):
        raw = {}
        ts = parse_timestamp(raw)
        # Should return current time
        assert (datetime.now(UTC) - ts).total_seconds() < 1

    def test_invalid_timestamp(self):
        raw = {"__REALTIME_TIMESTAMP": "invalid"}
        ts = parse_timestamp(raw)
        # Should return current time
        assert (datetime.now(UTC) - ts).total_seconds() < 1


class TestFingerprintGeneration:
    """Tests for fingerprint generation."""

    def test_same_input_same_fingerprint(self):
        fp1 = generate_fingerprint("disk", "disk:/dev/sda", "I/O error on device")
        fp2 = generate_fingerprint("disk", "disk:/dev/sda", "I/O error on device")
        assert fp1 == fp2

    def test_different_category_different_fingerprint(self):
        fp1 = generate_fingerprint("disk", "disk:/dev/sda", "error message")
        fp2 = generate_fingerprint("net", "disk:/dev/sda", "error message")
        assert fp1 != fp2

    def test_different_entity_different_fingerprint(self):
        fp1 = generate_fingerprint("disk", "disk:/dev/sda", "I/O error")
        fp2 = generate_fingerprint("disk", "disk:/dev/sdb", "I/O error")
        assert fp1 != fp2

    def test_fingerprint_length(self):
        fp = generate_fingerprint("test", "entity:x", "message")
        assert len(fp) == 16


class TestNormalizer:
    """Tests for the Normalizer class."""

    def test_normalize_journal_event(self):
        normalizer = Normalizer(dedupe_window_sec=60)
        raw = {
            "_SOURCE": "journal",
            "MESSAGE": "Started nginx.service",
            "PRIORITY": "6",
            "__REALTIME_TIMESTAMP": "1704067200000000",
        }

        event = normalizer.normalize(raw)

        assert event is not None
        assert event.source == "journal"
        assert event.severity == "info"
        assert event.category == "service"
        assert "nginx" in event.message

    def test_normalize_kernel_event(self):
        normalizer = Normalizer(dedupe_window_sec=60)
        raw = {
            "_SOURCE": "kernel",
            "_TRANSPORT": "kernel",
            "MESSAGE": "eth0: link is down, carrier lost",
            "PRIORITY": "4",
        }

        event = normalizer.normalize(raw)

        assert event is not None
        assert event.source == "kernel"
        assert event.severity == "warning"
        assert event.category == "net"
        assert event.entity == "interface:eth0"

    def test_deduplication(self):
        normalizer = Normalizer(dedupe_window_sec=60)
        raw = {
            "_SOURCE": "journal",
            "MESSAGE": "Started nginx.service",
            "PRIORITY": "6",
        }

        # First event should be processed
        event1 = normalizer.normalize(raw)
        assert event1 is not None

        # Duplicate should be filtered
        event2 = normalizer.normalize(raw)
        assert event2 is None

        # Stats should reflect deduplication
        stats = normalizer.get_stats()
        assert stats["processed"] == 2
        assert stats["deduplicated"] == 1

    def test_empty_message_filtered(self):
        normalizer = Normalizer()
        raw = {
            "_SOURCE": "journal",
            "MESSAGE": "",
            "PRIORITY": "6",
        }

        event = normalizer.normalize(raw)
        assert event is None

    def test_batch_normalize(self):
        normalizer = Normalizer(dedupe_window_sec=0)  # Disable dedup for this test
        raw_events = [
            {"_SOURCE": "journal", "MESSAGE": "Started nginx.service", "PRIORITY": "6"},
            {"_SOURCE": "journal", "MESSAGE": "Network connection dropped on eth0", "PRIORITY": "4"},
            {"_SOURCE": "journal", "MESSAGE": "Out of memory: Killed process", "PRIORITY": "3"},
        ]

        events = normalizer.normalize_batch(raw_events)
        assert len(events) == 3
        assert events[0].severity == "info"
        assert events[1].severity == "warning"
        assert events[2].severity == "error"

    def test_stats_tracking(self):
        normalizer = Normalizer()

        # Normalize some events - use messages that clearly match categories
        normalizer.normalize({"_SOURCE": "journal", "MESSAGE": "Started nginx.service - A high performance web server", "PRIORITY": "6"})
        normalizer.normalize({"_SOURCE": "kernel", "MESSAGE": "I/O error on /dev/sda, sector 1234", "PRIORITY": "3"})

        stats = normalizer.get_stats()
        assert stats["processed"] == 2
        assert "service" in stats["by_category"]
        assert "disk" in stats["by_category"]

    def test_reset_stats(self):
        normalizer = Normalizer()
        normalizer.normalize({"_SOURCE": "journal", "MESSAGE": "test", "PRIORITY": "6"})

        normalizer.reset_stats()
        stats = normalizer.get_stats()

        assert stats["processed"] == 0
        assert stats["deduplicated"] == 0
        assert stats["by_category"] == {}
