#!/usr/bin/env python3
"""Test client for elled-telemetryd.

Connects to the telemetryd socket and validates event format.
"""

import asyncio
import json
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

SOCKET_PATH = Path("/run/elle/telemetry.sock")

REQUIRED_FIELDS = {"ts", "source", "severity", "category", "message", "fingerprint"}

VALID_SOURCES = {"journal", "kernel", "probe", "ebpf", "docker", "inotify"}

VALID_SEVERITIES = {"debug", "info", "notice", "warning", "error", "critical"}

VALID_CATEGORIES = {
    "oom", "disk", "net", "auth", "service", "thermal",
    "smart", "fs", "pkg", "docker", "kernel_panic", "other"
}


def validate_event(event: dict[str, Any]) -> list[str]:
    """Validate an event against the protocol spec.

    Returns list of validation errors (empty if valid).
    """
    errors = []

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in event:
            errors.append(f"Missing required field: {field}")

    if errors:
        return errors  # Can't validate further without required fields

    # Validate timestamp
    ts = event.get("ts", 0)
    if not isinstance(ts, int) or ts <= 0:
        errors.append(f"Invalid timestamp: {ts}")

    # Validate source
    source = event.get("source", "")
    if source not in VALID_SOURCES:
        errors.append(f"Invalid source: {source}")

    # Validate severity
    severity = event.get("severity", "")
    if severity not in VALID_SEVERITIES:
        errors.append(f"Invalid severity: {severity}")

    # Validate category
    category = event.get("category", "")
    if category not in VALID_CATEGORIES:
        errors.append(f"Invalid category: {category}")

    # Validate message
    message = event.get("message", "")
    if not isinstance(message, str) or len(message) == 0:
        errors.append(f"Invalid message: {message!r}")

    # Validate fingerprint
    fingerprint = event.get("fingerprint", "")
    if not isinstance(fingerprint, str) or len(fingerprint) != 16:
        errors.append(f"Invalid fingerprint length: {len(fingerprint)} (expected 16)")

    # Validate entity format if present
    entity = event.get("entity")
    if entity is not None:
        if not isinstance(entity, str):
            errors.append(f"Invalid entity type: {type(entity)}")
        elif ":" not in entity:
            errors.append(f"Invalid entity format (missing ':'): {entity}")

    return errors


async def test_connection(socket_path: Path, timeout: float = 5.0) -> bool:
    """Test connection to telemetryd socket.

    Returns True if connection successful.
    """
    if not socket_path.exists():
        print(f"FAIL: Socket not found: {socket_path}")
        return False

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(socket_path)),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        print(f"PASS: Connected to {socket_path}")
        return True
    except asyncio.TimeoutError:
        print(f"FAIL: Connection timeout")
        return False
    except Exception as e:
        print(f"FAIL: Connection error: {e}")
        return False


async def test_event_stream(
    socket_path: Path,
    num_events: int = 10,
    timeout: float = 30.0,
) -> tuple[int, int]:
    """Read and validate events from the socket.

    Returns tuple of (valid_count, error_count).
    """
    valid = 0
    errors = 0

    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        print(f"Waiting for {num_events} events (timeout: {timeout}s)...")

        try:
            async with asyncio.timeout(timeout):
                while valid + errors < num_events:
                    line = await reader.readline()
                    if not line:
                        print("Connection closed by server")
                        break

                    try:
                        event = json.loads(line.decode("utf-8"))
                        validation_errors = validate_event(event)

                        if validation_errors:
                            print(f"INVALID: {validation_errors}")
                            print(f"  Event: {event}")
                            errors += 1
                        else:
                            print(f"VALID: [{event['severity']}] {event['category']}: {event['message'][:60]}...")
                            valid += 1

                    except json.JSONDecodeError as e:
                        print(f"INVALID JSON: {e}")
                        errors += 1

        except asyncio.TimeoutError:
            print(f"Timeout waiting for events (received {valid + errors})")

        writer.close()
        await writer.wait_closed()

    except Exception as e:
        print(f"Error: {e}")

    return valid, errors


async def main():
    socket_path = SOCKET_PATH
    if len(sys.argv) > 1:
        socket_path = Path(sys.argv[1])

    print(f"Testing telemetryd at {socket_path}")
    print("=" * 60)

    # Test connection
    print("\n1. Connection Test")
    if not await test_connection(socket_path):
        sys.exit(1)

    # Test event stream
    print("\n2. Event Stream Test")
    valid, errors = await test_event_stream(socket_path, num_events=10, timeout=30.0)

    print("\n" + "=" * 60)
    print(f"Results: {valid} valid, {errors} invalid")

    if errors > 0:
        print("FAIL: Some events failed validation")
        sys.exit(1)
    elif valid == 0:
        print("WARN: No events received")
        sys.exit(0)
    else:
        print("PASS: All events valid")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
