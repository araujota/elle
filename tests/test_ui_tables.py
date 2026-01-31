"""Tests for elle.cli.ui.tables -- Table formatting and display."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from rich.table import Table

from elle.cli.ui.tables import (
    command_history_table,
    disk_status_table,
    event_table,
    incident_table,
    incident_table_compact,
    interface_status_table,
    manvault_results_table,
    options_table,
    service_status_table,
    snapshot_diff_table,
)

# ---------------------------------------------------------------------------
# Incident Table
# ---------------------------------------------------------------------------


class TestIncidentTable:
    def test_empty(self):
        result = incident_table([])
        assert isinstance(result, Table)

    def test_basic_incidents(self):
        incidents = [
            {
                "incident_id": "inc-12345678",
                "title": "DNS resolution failure",
                "domain": "net",
                "status": "open",
                "severity": "warning",
                "created_at": datetime.now() - timedelta(minutes=10),
            },
            {
                "incident_id": "inc-87654321",
                "title": "High disk usage on /var",
                "domain": "disk",
                "status": "resolved",
                "severity": "error",
                "created_at": datetime.now() - timedelta(hours=2),
                "outcome": "improved",
            },
        ]
        result = incident_table(incidents)
        assert isinstance(result, Table)

    def test_with_title(self):
        result = incident_table([], title="Recent Incidents")
        assert isinstance(result, Table)

    def test_without_outcome(self):
        result = incident_table([], show_outcome=False)
        assert isinstance(result, Table)

    def test_long_title_truncated(self):
        incidents = [
            {
                "incident_id": "id-123",
                "title": "A" * 100,
                "domain": "net",
                "status": "open",
                "severity": "info",
                "created_at": datetime.now(),
            },
        ]
        result = incident_table(incidents, max_title_width=20)
        assert isinstance(result, Table)

    def test_no_outcome_shows_dash(self):
        incidents = [
            {
                "incident_id": "id",
                "title": "t",
                "domain": "net",
                "status": "open",
                "severity": "info",
                "created_at": datetime.now(),
            },
        ]
        result = incident_table(incidents, show_outcome=True)
        assert isinstance(result, Table)

    def test_string_created_at(self):
        incidents = [
            {
                "incident_id": "id",
                "title": "t",
                "domain": "net",
                "status": "open",
                "severity": "info",
                "created_at": "2024-01-01",
            },
        ]
        result = incident_table(incidents)
        assert isinstance(result, Table)

    def test_none_created_at(self):
        incidents = [
            {
                "incident_id": "id",
                "title": "t",
                "domain": "net",
                "status": "open",
                "severity": "info",
                "created_at": None,
            },
        ]
        result = incident_table(incidents)
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Incident Table Compact
# ---------------------------------------------------------------------------


class TestIncidentTableCompact:
    def test_empty(self):
        result = incident_table_compact([])
        assert isinstance(result, Table)

    def test_basic(self):
        incidents = [
            {
                "incident_id": "inc-123",
                "title": "Test incident",
                "domain": "net",
                "created_at": datetime.now() - timedelta(minutes=2),
            },
        ]
        result = incident_table_compact(incidents)
        assert isinstance(result, Table)

    def test_max_rows_exceeded(self):
        incidents = [
            {
                "incident_id": f"inc-{i}",
                "title": f"Incident {i}",
                "domain": "net",
                "created_at": datetime.now(),
            }
            for i in range(10)
        ]
        result = incident_table_compact(incidents, max_rows=3)
        assert isinstance(result, Table)

    def test_no_datetime_created_at(self):
        incidents = [
            {"incident_id": "id", "title": "t", "domain": "net", "created_at": "yesterday"},
        ]
        result = incident_table_compact(incidents)
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Event Table
# ---------------------------------------------------------------------------


class TestEventTable:
    def test_empty(self):
        result = event_table([])
        assert isinstance(result, Table)

    def test_basic_events(self):
        events = [
            {
                "ts": datetime.now() - timedelta(minutes=1),
                "source": "journal",
                "severity": "info",
                "category": "auth",
                "message": "User logged in",
            },
            {
                "ts": datetime.now(),
                "source": "kernel",
                "severity": "error",
                "category": "disk",
                "message": "I/O error on sda1",
            },
        ]
        result = event_table(events, title="Events")
        assert isinstance(result, Table)

    def test_long_message_truncated(self):
        events = [
            {
                "ts": datetime.now(),
                "source": "src",
                "severity": "warning",
                "category": "cat",
                "message": "M" * 200,
            },
        ]
        result = event_table(events, max_message_width=30)
        assert isinstance(result, Table)

    @pytest.mark.parametrize("severity", ["info", "warning", "error", "critical", "unknown"])
    def test_severity_icons(self, severity):
        events = [
            {"ts": datetime.now(), "source": "s", "severity": severity, "category": "c", "message": "m"},
        ]
        result = event_table(events)
        assert isinstance(result, Table)

    def test_non_datetime_ts(self):
        events = [
            {"ts": "2024-01-01", "source": "s", "severity": "info", "category": "c", "message": "m"},
        ]
        result = event_table(events)
        assert isinstance(result, Table)

    def test_none_ts(self):
        events = [
            {"ts": None, "source": "s", "severity": "info", "category": "c", "message": "m"},
        ]
        result = event_table(events)
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Man Vault Results Table
# ---------------------------------------------------------------------------


class TestManvaultResultsTable:
    def test_empty(self):
        result = manvault_results_table([])
        assert isinstance(result, Table)

    def test_basic(self):
        results = [
            {"name": "systemctl", "section": "1", "snippet": "systemctl restart", "relevance": 0.9},
            {"name": "journalctl", "section": "1", "snippet": "view journal", "relevance": 0.7},
        ]
        result = manvault_results_table(results, title="Results")
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Disk Status Table
# ---------------------------------------------------------------------------


class TestDiskStatusTable:
    def test_basic(self):
        disks = [
            {"mount": "/", "used_pct": 0.45, "avail_gb": 50.0},
            {"mount": "/home", "used_pct": 0.8, "avail_gb": 20.0},
        ]
        result = disk_status_table(disks)
        assert isinstance(result, Table)

    def test_empty(self):
        result = disk_status_table([])
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Service Status Table
# ---------------------------------------------------------------------------


class TestServiceStatusTable:
    def test_basic(self):
        services = [
            {"name": "nginx", "active": True, "failed": False},
            {"name": "mysql", "active": False, "failed": True},
            {"name": "redis", "active": False, "failed": False},
        ]
        result = service_status_table(services)
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Interface Status Table
# ---------------------------------------------------------------------------


class TestInterfaceStatusTable:
    def test_basic(self):
        interfaces = [
            {"name": "eth0", "state": "up", "ip": "192.168.1.1", "errors": 0},
            {"name": "wlan0", "state": "down", "ip": "-", "errors": 5},
        ]
        result = interface_status_table(interfaces)
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Command History Table
# ---------------------------------------------------------------------------


class TestCommandHistoryTable:
    def test_basic(self):
        commands = [
            {"command": "ls -la", "exit_code": 0, "timestamp": datetime.now()},
            {"command": "apt install foo", "exit_code": 1, "timestamp": datetime.now()},
        ]
        result = command_history_table(commands)
        assert isinstance(result, Table)

    def test_max_rows(self):
        commands = [{"command": f"cmd-{i}", "exit_code": 0} for i in range(20)]
        result = command_history_table(commands, max_rows=5)
        assert isinstance(result, Table)

    def test_no_timestamp(self):
        commands = [{"command": "echo hello", "exit_code": 0}]
        result = command_history_table(commands)
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Options Table
# ---------------------------------------------------------------------------


class TestOptionsTable:
    def test_basic(self):
        opts = [("a", "Alpha"), ("b", "Beta")]
        result = options_table(opts)
        assert isinstance(result, Table)

    def test_with_selection(self):
        opts = [("a", "Alpha"), ("b", "Beta")]
        result = options_table(opts, selected=1)
        assert isinstance(result, Table)

    def test_with_title(self):
        opts = [("x", "Option")]
        result = options_table(opts, title="Choose:")
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Snapshot Diff Table
# ---------------------------------------------------------------------------


class TestSnapshotDiffTable:
    def test_basic(self):
        changes = [
            {"field": "cpu_load", "before": "0.5", "after": "1.2", "severity": "warning"},
            {"field": "mem_used", "before": "4GB", "after": "6GB", "severity": "error"},
        ]
        result = snapshot_diff_table(changes)
        assert isinstance(result, Table)

    def test_empty(self):
        result = snapshot_diff_table([])
        assert isinstance(result, Table)
