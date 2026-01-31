"""Shared serialization utilities for ELLE storage.

Consolidates the duplicated ``_json_dumps``, ``_json_loads``,
``_serialize_datetime``, ``_parse_datetime`` helpers that were
previously copied into every store module.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def json_dumps(obj: Any) -> str:
    """Serialize *obj* to a JSON string.

    Non-serializable objects are converted via ``str()``.

    Args:
        obj: Any JSON-serializable object.

    Returns:
        JSON string.
    """
    return json.dumps(obj, default=str)


def json_loads(raw: str | None) -> Any:
    """Deserialize a JSON string.

    Returns an empty dict for empty / null input instead of raising.
    Passes through ``dict`` and ``list`` objects unchanged (useful when
    the DB driver has already decoded JSONB columns).

    Args:
        raw: JSON string, pre-parsed dict/list, or None.

    Returns:
        Parsed Python object, or ``{}``.
    """
    if not raw:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    return json.loads(raw)


def serialize_datetime(dt: datetime | None) -> str | None:
    """Convert a datetime to an ISO 8601 string with timezone.

    If the datetime is naive, it is assumed to be UTC.

    Args:
        dt: A datetime instance, or None.

    Returns:
        ISO 8601 string, or None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def parse_datetime(raw: str | None) -> datetime | None:
    """Parse an ISO 8601 string to a timezone-aware datetime.

    If the parsed result is naive, UTC is assumed.

    Args:
        raw: ISO 8601 string, or None.

    Returns:
        A timezone-aware datetime, or None.
    """
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Replaces the deprecated ``datetime.utcnow()`` pattern.

    Returns:
        Current UTC datetime with tzinfo set.
    """
    return datetime.now(timezone.utc)
