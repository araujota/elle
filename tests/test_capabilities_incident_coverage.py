"""Tests for capabilities/core/incident.py coverage.

Covers IncidentCreateCapability.run() (event linking, snapshot, notifications)
and IncidentAttachCapability.run() for all attachment types.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.capabilities.core.incident import (
    IncidentAttachCapability,
    IncidentAttachInput,
    IncidentCreateCapability,
    IncidentCreateInput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_incident(incident_id: str = "inc-001"):
    """Return a mock incident object used by the store."""
    inc = MagicMock()
    inc.incident_id = incident_id
    inc.log_snippets = []
    inc.metrics = {}
    return inc


def _store_patch():
    """Patch elle.daemon.incidents.store with the functions incident.py imports."""
    store_mod = MagicMock()
    store_mod.create_incident_draft.return_value = _mock_incident()
    store_mod.update_incident.return_value = None
    store_mod.link_events.return_value = 2
    store_mod.attach_snapshot.return_value = None
    store_mod.get_incident.return_value = _mock_incident()
    store_mod.append_action.return_value = MagicMock(step_index=1)
    return store_mod


# ===========================================================================
# IncidentCreateCapability
# ===========================================================================


class TestIncidentCreateCapabilitySpec:
    def test_spec_name(self):
        cap = IncidentCreateCapability()
        assert cap.spec.name == "incident.create"
        assert cap.spec.domain == "incident"


class TestIncidentCreateDryRun:
    def test_dry_run_contains_title(self):
        cap = IncidentCreateCapability()
        inp = IncidentCreateInput(title="disk full")
        result = cap.dry_run(inp)
        assert result.is_valid is True
        assert "disk full" in result.preview_text


class TestIncidentCreateRun:
    def test_run_basic(self):
        """Basic incident creation without events, snapshot, or notifications."""
        cap = IncidentCreateCapability()
        store_mod = _store_patch()

        inp = IncidentCreateInput(
            title="test incident",
            domain="disk",
            severity="warning",
            notify=False,
            attach_snapshot=False,
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is True
        assert result.output.incident_id == "inc-001"
        assert result.output.status == "open"
        assert result.output.snapshot_attached is False
        assert result.output.notification_sent is False
        store_mod.create_incident_draft.assert_called_once()
        store_mod.update_incident.assert_called_once()

    def test_run_with_event_linking(self):
        """Incident creation with event IDs triggers link_events."""
        cap = IncidentCreateCapability()
        store_mod = _store_patch()

        inp = IncidentCreateInput(
            title="event incident",
            event_ids=("ev-1", "ev-2"),
            notify=False,
            attach_snapshot=False,
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is True
        store_mod.link_events.assert_called_once_with("inc-001", ["ev-1", "ev-2"])

    def test_run_with_snapshot(self):
        """Incident creation with attach_snapshot=True collects a snapshot."""
        cap = IncidentCreateCapability()
        store_mod = _store_patch()

        snapshot_mod = MagicMock()
        snapshot_mod.collect_snapshot.return_value = {"cpu": 50}

        inp = IncidentCreateInput(
            title="snapshot incident",
            attach_snapshot=True,
            notify=False,
        )

        with patch.dict("sys.modules", {
            "elle.daemon.incidents.store": store_mod,
            "elle.daemon.incidents.snapshot": snapshot_mod,
        }):
            result = cap.run(inp)

        assert result.success is True
        assert result.output.snapshot_attached is True
        snapshot_mod.collect_snapshot.assert_called_once()
        store_mod.attach_snapshot.assert_called_once()

    def test_run_snapshot_failure(self):
        """When snapshot collection fails, snapshot_attached is False but creation succeeds."""
        cap = IncidentCreateCapability()
        store_mod = _store_patch()

        snapshot_mod = MagicMock()
        snapshot_mod.collect_snapshot.side_effect = RuntimeError("snap failed")

        inp = IncidentCreateInput(
            title="snap fail",
            attach_snapshot=True,
            notify=False,
        )

        with patch.dict("sys.modules", {
            "elle.daemon.incidents.store": store_mod,
            "elle.daemon.incidents.snapshot": snapshot_mod,
        }):
            result = cap.run(inp)

        assert result.success is True
        assert result.output.snapshot_attached is False

    def test_run_with_notification(self):
        """Incident creation with notify=True sends notification."""
        cap = IncidentCreateCapability()
        store_mod = _store_patch()

        notify_mod = MagicMock()
        notify_mod.notify_incident.return_value = None

        inp = IncidentCreateInput(
            title="notify incident",
            notify=True,
            attach_snapshot=False,
        )

        with patch.dict("sys.modules", {
            "elle.daemon.incidents.store": store_mod,
            "elle.daemon.notifications": notify_mod,
        }):
            result = cap.run(inp)

        assert result.success is True
        assert result.output.notification_sent is True
        notify_mod.notify_incident.assert_called_once()

    def test_run_notification_import_error(self):
        """When notifications module is unavailable, notification_sent is False."""
        cap = IncidentCreateCapability()
        store_mod = _store_patch()

        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "elle.daemon.notifications":
                raise ImportError("no notifications")
            return original_import(name, *args, **kwargs)

        inp = IncidentCreateInput(
            title="no notify",
            notify=True,
            attach_snapshot=False,
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            with patch("builtins.__import__", side_effect=mock_import):
                result = cap.run(inp)

        assert result.success is True
        assert result.output.notification_sent is False

    def test_run_notification_exception(self):
        """When notification raises a generic exception, it is caught."""
        cap = IncidentCreateCapability()
        store_mod = _store_patch()

        notify_mod = MagicMock()
        notify_mod.notify_incident.side_effect = RuntimeError("push failed")

        inp = IncidentCreateInput(
            title="notify fail",
            notify=True,
            attach_snapshot=False,
        )

        with patch.dict("sys.modules", {
            "elle.daemon.incidents.store": store_mod,
            "elle.daemon.notifications": notify_mod,
        }):
            result = cap.run(inp)

        assert result.success is True
        assert result.output.notification_sent is False

    def test_run_store_import_error(self):
        """When the incident store is not importable, returns failure."""
        cap = IncidentCreateCapability()
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "elle.daemon.incidents.store":
                raise ImportError("no store")
            return original_import(name, *args, **kwargs)

        inp = IncidentCreateInput(title="no store", notify=False)

        with patch("builtins.__import__", side_effect=mock_import):
            result = cap.run(inp)

        assert result.success is False
        assert "not available" in result.error.lower()

    def test_run_generic_exception(self):
        """When an unexpected exception occurs, returns failure."""
        cap = IncidentCreateCapability()
        store_mod = MagicMock()
        store_mod.create_incident_draft.side_effect = RuntimeError("db crash")

        inp = IncidentCreateInput(title="crash", notify=False)

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is False
        assert "db crash" in result.error


# ===========================================================================
# IncidentAttachCapability
# ===========================================================================


class TestIncidentAttachSpec:
    def test_spec_name(self):
        cap = IncidentAttachCapability()
        assert cap.spec.name == "incident.attach"


class TestIncidentAttachDryRun:
    def test_dry_run_preview(self):
        cap = IncidentAttachCapability()
        inp = IncidentAttachInput(incident_id="inc-001", attach_type="log")
        result = cap.dry_run(inp)
        assert result.is_valid is True
        assert "log" in result.preview_text


class TestIncidentAttachRun:
    def test_attach_action(self):
        """Attach an action record."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="action",
            action_kind="shell",
            command="ls -la",
            success=True,
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is True
        assert "action" in result.output.attach_type
        store_mod.append_action.assert_called_once()

    def test_attach_action_missing_kind(self):
        """Attaching action without action_kind returns failure."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="action",
            # action_kind is None
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is False
        assert "action_kind" in result.error

    def test_attach_log(self):
        """Attach a log snippet."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="log",
            log_snippet="kernel: OOM killer activated",
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is True
        assert "log" in result.output.details.lower()
        store_mod.update_incident.assert_called_once()

    def test_attach_log_missing_snippet(self):
        """Attaching log without log_snippet returns failure."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="log",
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is False
        assert "log_snippet" in result.error

    def test_attach_metric(self):
        """Attach metric key-value pairs."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="metric",
            metrics={"cpu_pct": 95.5, "mem_pct": 88.0},
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is True
        assert "2 metrics" in result.output.details

    def test_attach_metric_empty(self):
        """Attaching metric with empty dict returns failure."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="metric",
            metrics={},
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is False
        assert "metrics" in result.error.lower()

    def test_attach_event(self):
        """Attach event IDs."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="event",
            event_ids=("ev-1", "ev-2"),
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is True
        assert "Linked" in result.output.details

    def test_attach_event_missing_ids(self):
        """Attaching event without event_ids returns failure."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="event",
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is False
        assert "event_ids" in result.error

    def test_attach_snapshot(self):
        """Attach a system snapshot."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()
        snapshot_mod = MagicMock()
        snapshot_mod.collect_snapshot.return_value = {"load": 1.5}

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="snapshot",
            snapshot_which="post",
        )

        with patch.dict("sys.modules", {
            "elle.daemon.incidents.store": store_mod,
            "elle.daemon.incidents.snapshot": snapshot_mod,
        }):
            result = cap.run(inp)

        assert result.success is True
        assert "post" in result.output.details.lower()

    def test_attach_snapshot_failure(self):
        """When snapshot collection fails, returns failure."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()
        snapshot_mod = MagicMock()
        snapshot_mod.collect_snapshot.side_effect = RuntimeError("no /proc")

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="snapshot",
        )

        with patch.dict("sys.modules", {
            "elle.daemon.incidents.store": store_mod,
            "elle.daemon.incidents.snapshot": snapshot_mod,
        }):
            result = cap.run(inp)

        assert result.success is False
        assert "snapshot" in result.error.lower()

    def test_attach_incident_not_found(self):
        """When incident does not exist, returns failure."""
        cap = IncidentAttachCapability()
        store_mod = _store_patch()
        store_mod.get_incident.return_value = None  # Not found

        inp = IncidentAttachInput(
            incident_id="nonexistent",
            attach_type="log",
            log_snippet="test",
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_attach_store_import_error(self):
        """When store is unavailable, returns failure."""
        cap = IncidentAttachCapability()
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "elle.daemon.incidents.store":
                raise ImportError("no store")
            return original_import(name, *args, **kwargs)

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="log",
            log_snippet="test",
        )

        with patch("builtins.__import__", side_effect=mock_import):
            result = cap.run(inp)

        assert result.success is False
        assert "not available" in result.error.lower()

    def test_attach_generic_exception(self):
        """When an unexpected exception occurs, returns failure."""
        cap = IncidentAttachCapability()
        store_mod = MagicMock()
        store_mod.get_incident.side_effect = RuntimeError("db fail")

        inp = IncidentAttachInput(
            incident_id="inc-001",
            attach_type="log",
            log_snippet="test",
        )

        with patch.dict("sys.modules", {"elle.daemon.incidents.store": store_mod}):
            result = cap.run(inp)

        assert result.success is False
        assert "db fail" in result.error
