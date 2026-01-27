"""Notification capabilities.

Provides typed, policy-governed operations for notifications:
- notify.send - Send desktop notification
- notify.alert - High-priority alert with optional ntfy push
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    SideEffect,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Models
# =============================================================================


class NotifySendInput(BaseModel):
    """Input for notify.send operation."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(
        description="Notification title",
        max_length=100,
    )
    body: str = Field(
        default="",
        description="Notification body text",
        max_length=500,
    )
    urgency: Literal["low", "normal", "critical"] = Field(
        default="normal",
        description="Notification urgency level",
    )
    icon: str | None = Field(
        default=None,
        description="Icon name or path",
    )
    timeout_ms: int = Field(
        default=5000,
        ge=0,
        le=30000,
        description="Auto-dismiss timeout in milliseconds (0 = no timeout)",
    )


class NotifySendOutput(BaseModel):
    """Output from notify.send operation."""

    model_config = ConfigDict(frozen=True)

    sent: bool = Field(description="Whether notification was sent")
    notification_id: str | None = Field(
        default=None,
        description="Notification ID if available",
    )
    backend: str = Field(
        default="unknown",
        description="Notification backend used",
    )


class NotifyAlertInput(BaseModel):
    """Input for notify.alert operation."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(
        description="Alert title",
        max_length=100,
    )
    body: str = Field(
        default="",
        description="Alert body text",
        max_length=1000,
    )
    severity: Literal["info", "warning", "error", "critical"] = Field(
        default="warning",
        description="Alert severity level",
    )
    push_ntfy: bool = Field(
        default=False,
        description="Also send to ntfy push notification",
    )
    ntfy_topic: str | None = Field(
        default=None,
        description="ntfy topic (uses default from config if not specified)",
    )
    tags: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Tags/emojis for ntfy notification",
    )
    actions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Action button labels",
    )
    incident_id: str | None = Field(
        default=None,
        description="Related incident ID for click-to-investigate",
    )


class NotifyAlertOutput(BaseModel):
    """Output from notify.alert operation."""

    model_config = ConfigDict(frozen=True)

    sent_desktop: bool = Field(
        default=False,
        description="Whether desktop notification was sent",
    )
    sent_ntfy: bool = Field(
        default=False,
        description="Whether ntfy notification was sent",
    )
    notification_id: str | None = Field(
        default=None,
        description="Desktop notification ID",
    )
    ntfy_message_id: str | None = Field(
        default=None,
        description="ntfy message ID",
    )


# =============================================================================
# Notify Send Capability
# =============================================================================


class NotifySendCapability(BaseCapability[NotifySendInput, NotifySendOutput]):
    """Send a desktop notification."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="notify.send",
            summary="Send a desktop notification",
            domain="notification",
            risk="none",
            side_effects=(
                SideEffect(
                    kind="notification_sent",
                    target="desktop",
                    reversible=False,
                    description="Desktop notification displayed",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="NotifySendInput",
            output_schema_name="NotifySendOutput",
        )

    def dry_run(self, input: NotifySendInput) -> DryRunResult:
        """Preview notification send."""
        return DryRunResult(
            would_execute=(f"notify: {input.title}",),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would send notification: '{input.title}'",
            is_valid=True,
        )

    def run(self, input: NotifySendInput) -> CapabilityResult:
        """Send the notification."""
        start_time = time.time()

        try:
            from elle.daemon.notifications import notify
            from elle.daemon.notifications.models import NotificationUrgency

            # Map urgency to notification urgency
            urgency_map: dict[str, NotificationUrgency] = {
                "low": NotificationUrgency.LOW,
                "normal": NotificationUrgency.NORMAL,
                "critical": NotificationUrgency.CRITICAL,
            }

            # Send via notification service
            _result = notify(  # Result available but notification_id extracted async
                title=input.title,
                body=input.body,
                urgency=urgency_map.get(input.urgency, NotificationUrgency.NORMAL),
                icon=input.icon,
            )

            return CapabilityResult(
                success=True,
                output=NotifySendOutput(
                    sent=True,
                    notification_id=None,  # Available in async result
                    backend="notification_service",
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="notification_sent",
                        target="desktop",
                        reversible=False,
                        description=f"Sent notification: {input.title}",
                    ),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Sent desktop notification: {input.title}",
                ),
            )

        except ImportError:
            # Notification service not available, try fallback
            logger.warning("Notification service not available, trying fallback")
            return self._fallback_notify(input, start_time)

        except Exception as e:
            logger.exception(f"Failed to send notification: {e}")
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def _fallback_notify(
        self,
        input: NotifySendInput,
        start_time: float,
    ) -> CapabilityResult:
        """Fallback notification via notify-send command."""
        import subprocess

        try:
            # Try notify-send command
            args = ["notify-send"]

            if input.urgency:
                args.extend(["--urgency", input.urgency])

            if input.icon:
                args.extend(["--icon", input.icon])

            if input.timeout_ms > 0:
                args.extend(["--expire-time", str(input.timeout_ms)])

            args.append(input.title)
            if input.body:
                args.append(input.body)

            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                return CapabilityResult(
                    success=True,
                    output=NotifySendOutput(
                        sent=True,
                        notification_id=None,
                        backend="notify-send",
                    ),
                    side_effects_applied=(
                        SideEffect(
                            kind="notification_sent",
                            target="desktop",
                            reversible=False,
                            description=f"Sent notification: {input.title}",
                        ),
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        commands_executed=(" ".join(args),),
                        rationale=f"Sent notification via notify-send: {input.title}",
                    ),
                )
            else:
                return CapabilityResult(
                    success=False,
                    error=f"notify-send failed: {result.stderr}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

        except FileNotFoundError:
            return CapabilityResult(
                success=False,
                error="No notification system available (neither service nor notify-send)",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        except subprocess.TimeoutExpired:
            return CapabilityResult(
                success=False,
                error="notify-send timed out",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Notify Alert Capability
# =============================================================================


class NotifyAlertCapability(BaseCapability[NotifyAlertInput, NotifyAlertOutput]):
    """Send a high-priority alert with optional ntfy push."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="notify.alert",
            summary="Send high-priority alert with optional ntfy push notification",
            domain="notification",
            risk="none",
            side_effects=(
                SideEffect(
                    kind="notification_sent",
                    target="desktop",
                    reversible=False,
                    description="Alert notification displayed",
                ),
                SideEffect(
                    kind="notification_sent",
                    target="ntfy",
                    reversible=False,
                    description="Push notification sent (if enabled)",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="NotifyAlertInput",
            output_schema_name="NotifyAlertOutput",
        )

    def dry_run(self, input: NotifyAlertInput) -> DryRunResult:
        """Preview alert send."""
        targets = ["desktop"]
        if input.push_ntfy:
            targets.append(f"ntfy:{input.ntfy_topic or 'default'}")

        return DryRunResult(
            would_execute=(
                f"alert [{input.severity}]: {input.title}",
                f"targets: {', '.join(targets)}",
            ),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would send {input.severity} alert: '{input.title}' to {', '.join(targets)}",
            is_valid=True,
        )

    def run(self, input: NotifyAlertInput) -> CapabilityResult:
        """Send the alert."""
        start_time = time.time()

        sent_desktop = False
        sent_ntfy = False
        notification_id = None
        ntfy_message_id = None
        errors: list[str] = []

        # 1. Send desktop notification
        try:
            from elle.daemon.notifications import notify, notify_incident
            from elle.daemon.notifications.models import NotificationUrgency

            # Map severity to urgency
            severity_urgency: dict[str, NotificationUrgency] = {
                "info": NotificationUrgency.LOW,
                "warning": NotificationUrgency.NORMAL,
                "error": NotificationUrgency.CRITICAL,
                "critical": NotificationUrgency.CRITICAL,
            }

            if input.incident_id:
                # Use incident notification for click-to-investigate
                notify_incident(
                    title=input.title,
                    severity=input.severity,
                    domain="alert",
                    incident_id=input.incident_id,
                    summary=input.body,
                )
            else:
                # Regular notification
                notify(
                    title=input.title,
                    body=input.body,
                    urgency=severity_urgency.get(input.severity, NotificationUrgency.NORMAL),
                )
            sent_desktop = True

        except ImportError:
            logger.warning("Notification service not available")
            errors.append("Desktop notification service not available")
        except Exception as e:
            logger.exception(f"Desktop notification failed: {e}")
            errors.append(f"Desktop: {e}")

        # 2. Send ntfy push if requested
        if input.push_ntfy:
            try:
                ntfy_message_id = self._send_ntfy(input)
                sent_ntfy = ntfy_message_id is not None
            except Exception as e:
                logger.exception(f"ntfy notification failed: {e}")
                errors.append(f"ntfy: {e}")

        # Determine overall success
        success = sent_desktop or sent_ntfy

        return CapabilityResult(
            success=success,
            output=NotifyAlertOutput(
                sent_desktop=sent_desktop,
                sent_ntfy=sent_ntfy,
                notification_id=notification_id,
                ntfy_message_id=ntfy_message_id,
            ),
            error="; ".join(errors) if errors and not success else None,
            warnings=tuple(errors) if errors and success else (),
            side_effects_applied=(
                SideEffect(
                    kind="notification_sent",
                    target="desktop" if sent_desktop else "ntfy",
                    reversible=False,
                    description=f"Sent alert: {input.title}",
                ),
            )
            if success
            else (),
            execution_time_ms=int((time.time() - start_time) * 1000),
            evidence=CapabilityEvidence(
                rationale=f"Sent {input.severity} alert: {input.title}",
            ),
        )

    def _send_ntfy(self, input: NotifyAlertInput) -> str | None:
        """Send notification via ntfy.

        Args:
            input: Alert input with ntfy configuration.

        Returns:
            Message ID if successful, None otherwise.
        """
        import json
        import urllib.error
        import urllib.request

        # Get topic from input or default
        topic = input.ntfy_topic
        if not topic:
            # Try to get from config
            try:
                from elle.daemon.config import get_config

                config = get_config()
                topic = getattr(config, "ntfy_topic", None)
            except Exception:
                pass

        if not topic:
            topic = "elle-alerts"

        # Build ntfy URL
        url = f"https://ntfy.sh/{topic}"

        # Build headers
        headers = {
            "Content-Type": "application/json",
        }

        # Map severity to ntfy priority
        priority_map = {
            "info": "default",
            "warning": "high",
            "error": "urgent",
            "critical": "max",
        }

        # Build payload
        payload: dict[str, str | list[str] | list[dict[str, str]]] = {
            "topic": topic,
            "title": input.title,
            "message": input.body or input.title,
            "priority": priority_map.get(input.severity, "default"),
        }

        # Add tags
        if input.tags:
            payload["tags"] = list(input.tags)
        else:
            # Default tags based on severity
            severity_tags: dict[str, list[str]] = {
                "info": ["information_source"],
                "warning": ["warning"],
                "error": ["x"],
                "critical": ["rotating_light"],
            }
            payload["tags"] = severity_tags.get(input.severity, [])

        # Add actions if any
        if input.actions:
            payload["actions"] = [{"action": "view", "label": action} for action in input.actions]

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers)

            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
                msg_id = result.get("id")
                return str(msg_id) if msg_id is not None else None

        except urllib.error.URLError as e:
            logger.warning(f"ntfy request failed: {e}")
            return None
        except Exception as e:
            logger.warning(f"ntfy error: {e}")
            return None


# =============================================================================
# Exports
# =============================================================================

NOTIFY_CAPABILITIES = [
    NotifySendCapability,
    NotifyAlertCapability,
]
