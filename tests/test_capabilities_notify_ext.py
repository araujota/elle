from __future__ import annotations

"""Extended tests for Notification capabilities.

Covers the uncovered lines in elle.capabilities.core.notify:
- NotifySendCapability.run() success / exception paths
- NotifySendCapability._fallback_notify() all branches
- NotifyAlertCapability.run() all paths (desktop, ntfy, errors)
- NotifyAlertCapability._send_ntfy() all branches
- Input model validation edge cases
"""

import json
import subprocess
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.core.notify import (
    NotifyAlertCapability,
    NotifyAlertInput,
    NotifyAlertOutput,
    NotifySendCapability,
    NotifySendInput,
    NotifySendOutput,
)
from elle.capabilities.models import CapabilityResult

# =============================================================================
# NotifySendCapability.run() -- success through notification service
# =============================================================================


class TestNotifySendRunSuccess:
    """Tests for the happy-path through the internal notification service."""

    def test_run_success_normal_urgency(self):
        """Successful send maps 'normal' urgency and returns backend=notification_service."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Hello", body="World", urgency="normal")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "low"
        mock_urgency.NORMAL = "normal"
        mock_urgency.CRITICAL = "critical"

        mock_notify_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            result = cap.run(inp)

        assert result.success is True
        assert result.output.sent is True
        assert result.output.backend == "notification_service"
        assert result.output.notification_id is None
        assert len(result.side_effects_applied) == 1
        assert result.side_effects_applied[0].kind == "notification_sent"
        assert result.evidence.rationale == "Sent desktop notification: Hello"
        mock_notify_fn.assert_called_once_with(
            title="Hello",
            body="World",
            urgency="normal",
            icon=None,
        )

    def test_run_success_low_urgency(self):
        """Urgency 'low' is mapped correctly."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Lo", urgency="low")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "low_val"
        mock_urgency.NORMAL = "normal_val"
        mock_urgency.CRITICAL = "critical_val"

        mock_notify_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            result = cap.run(inp)

        assert result.success is True
        mock_notify_fn.assert_called_once()
        call_kwargs = mock_notify_fn.call_args.kwargs
        assert call_kwargs["urgency"] == "low_val"

    def test_run_success_critical_urgency(self):
        """Urgency 'critical' is mapped correctly."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Crit", urgency="critical")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "low_val"
        mock_urgency.NORMAL = "normal_val"
        mock_urgency.CRITICAL = "critical_val"

        mock_notify_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            result = cap.run(inp)

        assert result.success is True
        call_kwargs = mock_notify_fn.call_args.kwargs
        assert call_kwargs["urgency"] == "critical_val"

    def test_run_with_icon(self):
        """Icon kwarg is forwarded to notify()."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Icon", icon="dialog-information")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "l"
        mock_urgency.NORMAL = "n"
        mock_urgency.CRITICAL = "c"

        mock_notify_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            cap.run(inp)

        assert mock_notify_fn.call_args.kwargs["icon"] == "dialog-information"


# =============================================================================
# NotifySendCapability.run() -- ImportError triggers _fallback_notify
# =============================================================================


class TestNotifySendRunImportError:
    """When elle.daemon.notifications is not importable, _fallback_notify is called."""

    def test_run_import_error_calls_fallback(self):
        """ImportError in the run body triggers _fallback_notify."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Fallback", body="body")

        sentinel_result = CapabilityResult(success=True)

        with patch.object(cap, "_fallback_notify", return_value=sentinel_result) as fb:
            # Remove any cached daemon module so the import inside run() raises
            with patch.dict("sys.modules", {"elle.daemon.notifications": None}):
                result = cap.run(inp)

        fb.assert_called_once()
        assert result is sentinel_result


# =============================================================================
# NotifySendCapability.run() -- generic Exception
# =============================================================================


class TestNotifySendRunException:
    """A non-ImportError exception in run() returns success=False with error string."""

    def test_run_generic_exception(self):
        """Generic RuntimeError is caught and surfaced as error."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Err")

        mock_notify_fn = MagicMock(side_effect=RuntimeError("dbus died"))
        mock_urgency = MagicMock()
        mock_urgency.LOW = "l"
        mock_urgency.NORMAL = "n"
        mock_urgency.CRITICAL = "c"

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            result = cap.run(inp)

        assert result.success is False
        assert "dbus died" in result.error


# =============================================================================
# NotifySendCapability._fallback_notify() -- all branches
# =============================================================================


class TestFallbackNotify:
    """Tests for the notify-send subprocess fallback."""

    @patch("subprocess.run")
    def test_fallback_success_minimal(self, mock_run):
        """Successful fallback with minimal input (no icon, no body, default timeout)."""
        mock_run.return_value = MagicMock(returncode=0)
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Minimal", urgency="normal")

        result = cap._fallback_notify(inp, 0.0)

        assert result.success is True
        assert result.output.sent is True
        assert result.output.backend == "notify-send"
        # Verify the args passed to subprocess
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "notify-send"
        assert "--urgency" in call_args
        assert "normal" in call_args
        assert "Minimal" in call_args

    @patch("subprocess.run")
    def test_fallback_success_with_icon_and_body(self, mock_run):
        """Icon and body are appended when provided."""
        mock_run.return_value = MagicMock(returncode=0)
        cap = NotifySendCapability()
        inp = NotifySendInput(
            title="WithIcon",
            body="Some body",
            icon="info",
            urgency="low",
            timeout_ms=10000,
        )

        result = cap._fallback_notify(inp, 0.0)

        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert "--icon" in call_args
        assert "info" in call_args
        assert "--expire-time" in call_args
        assert "10000" in call_args
        assert "WithIcon" in call_args
        assert "Some body" in call_args

    @patch("subprocess.run")
    def test_fallback_success_zero_timeout(self, mock_run):
        """timeout_ms=0 means no --expire-time is added."""
        mock_run.return_value = MagicMock(returncode=0)
        cap = NotifySendCapability()
        inp = NotifySendInput(title="NoExpire", timeout_ms=0)

        cap._fallback_notify(inp, 0.0)

        call_args = mock_run.call_args[0][0]
        assert "--expire-time" not in call_args

    @patch("subprocess.run")
    def test_fallback_success_no_body(self, mock_run):
        """Empty body means the body argument is not appended."""
        mock_run.return_value = MagicMock(returncode=0)
        cap = NotifySendCapability()
        inp = NotifySendInput(title="TitleOnly", body="")

        cap._fallback_notify(inp, 0.0)

        call_args = mock_run.call_args[0][0]
        # Last element should be the title only (no empty body appended)
        assert call_args[-1] == "TitleOnly"

    @patch("subprocess.run")
    def test_fallback_nonzero_returncode(self, mock_run):
        """Non-zero returncode yields success=False with stderr in error."""
        mock_run.return_value = MagicMock(returncode=1, stderr="no display")
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Fail")

        result = cap._fallback_notify(inp, 0.0)

        assert result.success is False
        assert "no display" in result.error

    @patch("subprocess.run")
    def test_fallback_evidence_commands_executed(self, mock_run):
        """Successful fallback records commands_executed in evidence."""
        mock_run.return_value = MagicMock(returncode=0)
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Evidence")

        result = cap._fallback_notify(inp, 0.0)

        assert len(result.evidence.commands_executed) == 1
        assert "notify-send" in result.evidence.commands_executed[0]

    @patch(
        "subprocess.run",
        side_effect=FileNotFoundError("not found"),
    )
    def test_fallback_file_not_found(self, mock_run):
        """FileNotFoundError yields descriptive error."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Missing")

        result = cap._fallback_notify(inp, 0.0)

        assert result.success is False
        assert "No notification system available" in result.error

    @patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="notify-send", timeout=5),
    )
    def test_fallback_timeout(self, mock_run):
        """subprocess.TimeoutExpired yields timed-out error."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="Slow")

        result = cap._fallback_notify(inp, 0.0)

        assert result.success is False
        assert "timed out" in result.error


# =============================================================================
# NotifyAlertCapability.run() -- desktop notification paths
# =============================================================================


class TestNotifyAlertRunDesktop:
    """Tests for the desktop notification arm of NotifyAlertCapability.run()."""

    def test_run_desktop_success_no_incident(self):
        """Successful desktop notification without incident_id uses notify()."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Info", body="msg", severity="info")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "low"
        mock_urgency.NORMAL = "normal"
        mock_urgency.CRITICAL = "critical"

        mock_notify_fn = MagicMock()
        mock_notify_incident_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(
                    notify=mock_notify_fn,
                    notify_incident=mock_notify_incident_fn,
                ),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            result = cap.run(inp)

        assert result.success is True
        assert result.output.sent_desktop is True
        mock_notify_fn.assert_called_once()
        mock_notify_incident_fn.assert_not_called()
        # Severity "info" maps to LOW urgency
        assert mock_notify_fn.call_args.kwargs["urgency"] == "low"

    def test_run_desktop_success_with_incident_id(self):
        """When incident_id is provided, notify_incident is used instead."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Inc", body="details", severity="error", incident_id="inc-999")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "low"
        mock_urgency.NORMAL = "normal"
        mock_urgency.CRITICAL = "critical"

        mock_notify_fn = MagicMock()
        mock_notify_incident_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(
                    notify=mock_notify_fn,
                    notify_incident=mock_notify_incident_fn,
                ),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            result = cap.run(inp)

        assert result.success is True
        assert result.output.sent_desktop is True
        mock_notify_fn.assert_not_called()
        mock_notify_incident_fn.assert_called_once_with(
            title="Inc",
            severity="error",
            domain="alert",
            incident_id="inc-999",
            summary="details",
        )

    def test_run_desktop_import_error(self):
        """ImportError on daemon import => sent_desktop=False, error recorded."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Oops", severity="warning")

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": None,
            },
        ):
            result = cap.run(inp)

        assert result.success is False
        assert result.output.sent_desktop is False
        assert "Desktop notification service not available" in result.error

    def test_run_desktop_generic_exception(self):
        """A runtime exception in the desktop arm is caught and recorded."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Boom", severity="critical")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "l"
        mock_urgency.NORMAL = "n"
        mock_urgency.CRITICAL = "c"

        mock_notify_fn = MagicMock(side_effect=OSError("dbus gone"))

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn, notify_incident=MagicMock()),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            result = cap.run(inp)

        assert result.success is False
        assert result.output.sent_desktop is False
        assert "Desktop: dbus gone" in result.error


# =============================================================================
# NotifyAlertCapability.run() -- severity-to-urgency mapping
# =============================================================================


class TestNotifyAlertSeverityMapping:
    """Verify all four severity levels are mapped to the correct urgency."""

    @pytest.mark.parametrize(
        "severity,expected_urgency_attr",
        [
            ("info", "LOW"),
            ("warning", "NORMAL"),
            ("error", "CRITICAL"),
            ("critical", "CRITICAL"),
        ],
    )
    def test_severity_maps_to_urgency(self, severity, expected_urgency_attr):
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Map", severity=severity)

        mock_urgency = MagicMock()
        mock_urgency.LOW = "LOW_VAL"
        mock_urgency.NORMAL = "NORMAL_VAL"
        mock_urgency.CRITICAL = "CRITICAL_VAL"

        expected_val = getattr(mock_urgency, expected_urgency_attr)
        mock_notify_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn, notify_incident=MagicMock()),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            cap.run(inp)

        assert mock_notify_fn.call_args.kwargs["urgency"] == expected_val


# =============================================================================
# NotifyAlertCapability.run() -- ntfy arm
# =============================================================================


class TestNotifyAlertRunNtfy:
    """Tests for the ntfy push notification arm of NotifyAlertCapability.run()."""

    def test_run_ntfy_success(self):
        """When push_ntfy=True and _send_ntfy returns an id, sent_ntfy is True."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Push", severity="warning", push_ntfy=True, ntfy_topic="t")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "l"
        mock_urgency.NORMAL = "n"
        mock_urgency.CRITICAL = "c"
        mock_notify_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn, notify_incident=MagicMock()),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            with patch.object(cap, "_send_ntfy", return_value="msg-id-42"):
                result = cap.run(inp)

        assert result.success is True
        assert result.output.sent_ntfy is True
        assert result.output.ntfy_message_id == "msg-id-42"

    def test_run_ntfy_returns_none(self):
        """If _send_ntfy returns None, sent_ntfy is False."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Push", severity="warning", push_ntfy=True, ntfy_topic="t")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "l"
        mock_urgency.NORMAL = "n"
        mock_urgency.CRITICAL = "c"
        mock_notify_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn, notify_incident=MagicMock()),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            with patch.object(cap, "_send_ntfy", return_value=None):
                result = cap.run(inp)

        # Desktop succeeded but ntfy did not
        assert result.success is True  # desktop still succeeded
        assert result.output.sent_ntfy is False

    def test_run_ntfy_exception(self):
        """Exception in _send_ntfy is caught; error is recorded."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Err", severity="info", push_ntfy=True, ntfy_topic="t")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "l"
        mock_urgency.NORMAL = "n"
        mock_urgency.CRITICAL = "c"
        mock_notify_fn = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=mock_notify_fn, notify_incident=MagicMock()),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            with patch.object(cap, "_send_ntfy", side_effect=ConnectionError("net down")):
                result = cap.run(inp)

        # Desktop still succeeded so overall success is True, but ntfy warning
        assert result.success is True
        assert result.output.sent_ntfy is False
        assert any("ntfy" in w for w in result.warnings)

    def test_run_both_fail(self):
        """When desktop fails AND ntfy fails, overall success is False."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Total", severity="critical", push_ntfy=True, ntfy_topic="t")

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": None,
            },
        ):
            with patch.object(cap, "_send_ntfy", side_effect=RuntimeError("net err")):
                result = cap.run(inp)

        assert result.success is False
        assert result.output.sent_desktop is False
        assert result.output.sent_ntfy is False
        assert result.error is not None


# =============================================================================
# NotifyAlertCapability.run() -- result structure details
# =============================================================================


class TestNotifyAlertResultStructure:
    """Verify side_effects_applied, warnings, and error fields."""

    def _make_successful_result(self, push_ntfy=False, ntfy_returns="id"):
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="S", severity="warning", push_ntfy=push_ntfy, ntfy_topic="t")

        mock_urgency = MagicMock()
        mock_urgency.LOW = "l"
        mock_urgency.NORMAL = "n"
        mock_urgency.CRITICAL = "c"

        with patch.dict(
            "sys.modules",
            {
                "elle.daemon.notifications": MagicMock(notify=MagicMock(), notify_incident=MagicMock()),
                "elle.daemon.notifications.models": MagicMock(NotificationUrgency=mock_urgency),
            },
        ):
            if push_ntfy:
                with patch.object(cap, "_send_ntfy", return_value=ntfy_returns):
                    return cap.run(inp)
            else:
                return cap.run(inp)

    def test_side_effects_applied_on_success(self):
        result = self._make_successful_result()
        assert len(result.side_effects_applied) == 1
        assert result.side_effects_applied[0].target == "desktop"

    def test_side_effects_applied_when_ntfy_only(self):
        """When desktop fails but ntfy succeeds, target should be ntfy."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="N", severity="info", push_ntfy=True, ntfy_topic="t")

        with patch.dict("sys.modules", {"elle.daemon.notifications": None}):
            with patch.object(cap, "_send_ntfy", return_value="ntfy-id"):
                result = cap.run(inp)

        assert result.success is True
        assert result.output.sent_ntfy is True
        assert result.output.sent_desktop is False
        assert result.side_effects_applied[0].target == "ntfy"

    def test_no_side_effects_when_both_fail(self):
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="F", severity="error", push_ntfy=True, ntfy_topic="t")

        with patch.dict("sys.modules", {"elle.daemon.notifications": None}):
            with patch.object(cap, "_send_ntfy", side_effect=RuntimeError("x")):
                result = cap.run(inp)

        assert result.side_effects_applied == ()

    def test_warnings_when_desktop_fails_ntfy_succeeds(self):
        """Errors list is surfaced as warnings when overall success is True."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="W", severity="info", push_ntfy=True, ntfy_topic="t")

        with patch.dict("sys.modules", {"elle.daemon.notifications": None}):
            with patch.object(cap, "_send_ntfy", return_value="ok"):
                result = cap.run(inp)

        assert result.success is True
        assert len(result.warnings) > 0
        assert result.error is None

    def test_execution_time_is_nonnegative(self):
        result = self._make_successful_result()
        assert result.execution_time_ms >= 0


# =============================================================================
# NotifyAlertCapability._send_ntfy() -- all branches
# =============================================================================


class TestSendNtfy:
    """Tests for the private _send_ntfy method."""

    def _make_input(self, **kwargs):
        defaults = {"title": "T", "body": "B", "severity": "warning", "push_ntfy": True}
        defaults.update(kwargs)
        return NotifyAlertInput(**defaults)

    # ------ topic resolution ------

    @patch("urllib.request.urlopen")
    def test_topic_from_input(self, mock_urlopen):
        """When ntfy_topic is provided in input, it is used directly."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "abc"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic="my-topic")
        result = cap._send_ntfy(inp)

        assert result == "abc"
        # Verify the URL contains the topic
        req_obj = mock_urlopen.call_args[0][0]
        assert "my-topic" in req_obj.full_url

    @patch("urllib.request.urlopen")
    def test_topic_from_config(self, mock_urlopen):
        """When ntfy_topic is None, falls back to config."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "cfg"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        mock_config = MagicMock()
        mock_config.ntfy_topic = "from-config"

        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic=None)

        with patch.dict(
            "sys.modules",
            {"elle.daemon.config": MagicMock(get_config=MagicMock(return_value=mock_config))},
        ):
            result = cap._send_ntfy(inp)

        assert result == "cfg"
        req_obj = mock_urlopen.call_args[0][0]
        assert "from-config" in req_obj.full_url

    @patch("urllib.request.urlopen")
    def test_topic_default_elle_alerts(self, mock_urlopen):
        """When ntfy_topic is None and config is unavailable, default is elle-alerts."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "def"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic=None)

        # Make config import fail
        with patch.dict("sys.modules", {"elle.daemon.config": None}):
            result = cap._send_ntfy(inp)

        assert result == "def"
        req_obj = mock_urlopen.call_args[0][0]
        assert "elle-alerts" in req_obj.full_url

    @patch("urllib.request.urlopen")
    def test_topic_config_getattr_missing(self, mock_urlopen):
        """Config exists but ntfy_topic attr is missing => falls through to elle-alerts."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "na"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        mock_config = MagicMock(spec=[])  # no ntfy_topic attribute

        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic=None)

        with patch.dict(
            "sys.modules",
            {"elle.daemon.config": MagicMock(get_config=MagicMock(return_value=mock_config))},
        ):
            cap._send_ntfy(inp)

        req_obj = mock_urlopen.call_args[0][0]
        assert "elle-alerts" in req_obj.full_url

    # ------ payload construction ------

    @patch("urllib.request.urlopen")
    def test_payload_custom_tags(self, mock_urlopen):
        """Custom tags are included in payload."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "t"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(tags=("fire", "skull"), ntfy_topic="x")
        cap._send_ntfy(inp)

        req_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(req_obj.data.decode())
        assert payload["tags"] == ["fire", "skull"]

    @patch("urllib.request.urlopen")
    @pytest.mark.parametrize(
        "severity,expected_tags",
        [
            ("info", ["information_source"]),
            ("warning", ["warning"]),
            ("error", ["x"]),
            ("critical", ["rotating_light"]),
        ],
    )
    def test_payload_default_severity_tags(self, mock_urlopen, severity, expected_tags):
        """When no custom tags, severity-based tags are applied."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "t"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(severity=severity, tags=(), ntfy_topic="x")
        cap._send_ntfy(inp)

        req_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(req_obj.data.decode())
        assert payload["tags"] == expected_tags

    @patch("urllib.request.urlopen")
    @pytest.mark.parametrize(
        "severity,expected_priority",
        [
            ("info", "default"),
            ("warning", "high"),
            ("error", "urgent"),
            ("critical", "max"),
        ],
    )
    def test_payload_priority_mapping(self, mock_urlopen, severity, expected_priority):
        """Severity is correctly mapped to ntfy priority."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "t"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(severity=severity, ntfy_topic="x")
        cap._send_ntfy(inp)

        req_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(req_obj.data.decode())
        assert payload["priority"] == expected_priority

    @patch("urllib.request.urlopen")
    def test_payload_actions(self, mock_urlopen):
        """Action labels are correctly structured."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "t"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(actions=("Investigate", "Dismiss"), ntfy_topic="x")
        cap._send_ntfy(inp)

        req_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(req_obj.data.decode())
        assert payload["actions"] == [
            {"action": "view", "label": "Investigate"},
            {"action": "view", "label": "Dismiss"},
        ]

    @patch("urllib.request.urlopen")
    def test_payload_no_actions(self, mock_urlopen):
        """When no actions are provided, 'actions' key should not appear."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "t"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(actions=(), ntfy_topic="x")
        cap._send_ntfy(inp)

        req_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(req_obj.data.decode())
        assert "actions" not in payload

    @patch("urllib.request.urlopen")
    def test_payload_body_fallback_to_title(self, mock_urlopen):
        """When body is empty, message field uses the title."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "t"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(body="", ntfy_topic="x")
        cap._send_ntfy(inp)

        req_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(req_obj.data.decode())
        assert payload["message"] == inp.title

    @patch("urllib.request.urlopen")
    def test_payload_content_type_header(self, mock_urlopen):
        """Request has application/json content type."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": "t"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic="x")
        cap._send_ntfy(inp)

        req_obj = mock_urlopen.call_args[0][0]
        assert req_obj.get_header("Content-type") == "application/json"

    # ------ response handling ------

    @patch("urllib.request.urlopen")
    def test_response_no_id(self, mock_urlopen):
        """When response JSON has no 'id' field, returns None."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"status": "ok"}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic="x")
        result = cap._send_ntfy(inp)

        assert result is None

    @patch("urllib.request.urlopen")
    def test_response_id_is_integer(self, mock_urlopen):
        """Integer id is converted to string."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"id": 12345}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic="x")
        result = cap._send_ntfy(inp)

        assert result == "12345"

    # ------ error handling ------

    @patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("DNS fail"),
    )
    def test_url_error_returns_none(self, mock_urlopen):
        """URLError returns None."""
        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic="x")
        result = cap._send_ntfy(inp)

        assert result is None

    @patch(
        "urllib.request.urlopen",
        side_effect=ValueError("bad url"),
    )
    def test_generic_exception_returns_none(self, mock_urlopen):
        """Any non-URLError exception also returns None."""
        cap = NotifyAlertCapability()
        inp = self._make_input(ntfy_topic="x")
        result = cap._send_ntfy(inp)

        assert result is None


# =============================================================================
# NotifyAlertCapability -- dry_run edge cases
# =============================================================================


class TestNotifyAlertDryRunEdgeCases:
    """Additional dry_run tests for edge-case coverage."""

    def test_dry_run_ntfy_default_topic(self):
        """When push_ntfy=True and ntfy_topic is None, preview says 'default'."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="X", severity="info", push_ntfy=True, ntfy_topic=None)

        result = cap.dry_run(inp)

        assert "default" in result.preview_text
        assert result.is_valid is True

    def test_dry_run_no_ntfy(self):
        """When push_ntfy=False, only desktop appears in targets."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Y", severity="warning", push_ntfy=False)

        result = cap.dry_run(inp)

        assert "ntfy" not in result.preview_text
        assert "desktop" in result.preview_text.lower() or "desktop" in str(result.would_execute)

    def test_dry_run_would_execute_includes_severity(self):
        """would_execute tuple includes the severity."""
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="Z", severity="error", push_ntfy=False)

        result = cap.dry_run(inp)

        assert any("error" in s for s in result.would_execute)


# =============================================================================
# NotifySendCapability -- dry_run and spec edge cases
# =============================================================================


class TestNotifySendDryRunEdgeCases:
    """Additional dry_run and spec tests for coverage."""

    def test_dry_run_would_execute_content(self):
        """would_execute tuple includes 'notify: <title>'."""
        cap = NotifySendCapability()
        inp = NotifySendInput(title="MyTitle")

        result = cap.dry_run(inp)

        assert result.would_execute == ("notify: MyTitle",)
        assert result.would_modify == ()
        assert result.estimated_risk == "none"
        assert result.requires_confirmation is False

    def test_spec_side_effects(self):
        """Spec side_effects is correct."""
        cap = NotifySendCapability()
        spec = cap.spec
        assert len(spec.side_effects) == 1
        assert spec.side_effects[0].kind == "notification_sent"
        assert spec.side_effects[0].target == "desktop"
        assert spec.side_effects[0].reversible is False

    def test_spec_details(self):
        """Full spec details beyond name/domain/risk."""
        cap = NotifySendCapability()
        spec = cap.spec
        assert spec.requires_privilege is False
        assert spec.idempotent is False
        assert spec.trust_level == "core"
        assert spec.version == "1.0.0"
        assert spec.input_schema_name == "NotifySendInput"
        assert spec.output_schema_name == "NotifySendOutput"


# =============================================================================
# NotifyAlertCapability -- spec edge cases
# =============================================================================


class TestNotifyAlertSpecEdgeCases:
    """Additional spec tests for NotifyAlertCapability."""

    def test_spec_has_two_side_effects(self):
        cap = NotifyAlertCapability()
        spec = cap.spec
        assert len(spec.side_effects) == 2
        targets = {se.target for se in spec.side_effects}
        assert targets == {"desktop", "ntfy"}

    def test_spec_details(self):
        cap = NotifyAlertCapability()
        spec = cap.spec
        assert spec.requires_privilege is False
        assert spec.idempotent is False
        assert spec.trust_level == "core"
        assert spec.version == "1.0.0"
        assert spec.input_schema_name == "NotifyAlertInput"
        assert spec.output_schema_name == "NotifyAlertOutput"
        assert spec.summary == "Send high-priority alert with optional ntfy push notification"


# =============================================================================
# Input model validation
# =============================================================================


class TestInputModelValidation:
    """Verify Pydantic validation on input models."""

    def test_notify_send_input_defaults(self):
        inp = NotifySendInput(title="T")
        assert inp.body == ""
        assert inp.urgency == "normal"
        assert inp.icon is None
        assert inp.timeout_ms == 5000

    def test_notify_send_input_frozen(self):
        inp = NotifySendInput(title="T")
        with pytest.raises(Exception):
            inp.title = "Changed"

    def test_notify_send_input_max_length_title(self):
        with pytest.raises(Exception):
            NotifySendInput(title="x" * 101)

    def test_notify_send_input_max_length_body(self):
        with pytest.raises(Exception):
            NotifySendInput(title="T", body="x" * 501)

    def test_notify_send_input_timeout_bounds(self):
        with pytest.raises(Exception):
            NotifySendInput(title="T", timeout_ms=-1)
        with pytest.raises(Exception):
            NotifySendInput(title="T", timeout_ms=30001)

    def test_notify_send_input_invalid_urgency(self):
        with pytest.raises(Exception):
            NotifySendInput(title="T", urgency="extreme")

    def test_notify_alert_input_defaults(self):
        inp = NotifyAlertInput(title="A")
        assert inp.body == ""
        assert inp.severity == "warning"
        assert inp.push_ntfy is False
        assert inp.ntfy_topic is None
        assert inp.tags == ()
        assert inp.actions == ()
        assert inp.incident_id is None

    def test_notify_alert_input_frozen(self):
        inp = NotifyAlertInput(title="A")
        with pytest.raises(Exception):
            inp.title = "Changed"

    def test_notify_alert_input_max_length_title(self):
        with pytest.raises(Exception):
            NotifyAlertInput(title="x" * 101)

    def test_notify_alert_input_max_length_body(self):
        with pytest.raises(Exception):
            NotifyAlertInput(title="A", body="x" * 1001)

    def test_notify_alert_input_invalid_severity(self):
        with pytest.raises(Exception):
            NotifyAlertInput(title="A", severity="minor")


# =============================================================================
# Output model validation
# =============================================================================


class TestOutputModelValidation:
    """Verify output model defaults and frozen behavior."""

    def test_notify_send_output_defaults(self):
        out = NotifySendOutput(sent=True)
        assert out.notification_id is None
        assert out.backend == "unknown"

    def test_notify_send_output_frozen(self):
        out = NotifySendOutput(sent=True)
        with pytest.raises(Exception):
            out.sent = False

    def test_notify_alert_output_defaults(self):
        out = NotifyAlertOutput()
        assert out.sent_desktop is False
        assert out.sent_ntfy is False
        assert out.notification_id is None
        assert out.ntfy_message_id is None

    def test_notify_alert_output_frozen(self):
        out = NotifyAlertOutput()
        with pytest.raises(Exception):
            out.sent_desktop = True


# =============================================================================
# BaseCapability default methods (verify, rollback)
# =============================================================================


class TestBaseCapabilityDefaults:
    """Verify the inherited verify() and rollback() defaults work for notify caps."""

    def test_notify_send_verify(self):
        cap = NotifySendCapability()
        inp = NotifySendInput(title="V")
        result = cap.verify(inp)
        assert result.passed is True

    def test_notify_send_rollback(self):
        cap = NotifySendCapability()
        inp = NotifySendInput(title="R")
        fake_result = CapabilityResult(success=True)
        rb = cap.rollback(inp, fake_result)
        assert rb.success is False
        assert "not supported" in rb.error.lower()

    def test_notify_alert_verify(self):
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="V")
        result = cap.verify(inp)
        assert result.passed is True

    def test_notify_alert_rollback(self):
        cap = NotifyAlertCapability()
        inp = NotifyAlertInput(title="R")
        fake_result = CapabilityResult(success=True)
        rb = cap.rollback(inp, fake_result)
        assert rb.success is False
        assert "not supported" in rb.error.lower()
