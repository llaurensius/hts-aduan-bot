"""Time utility functions."""

from datetime import datetime, timezone


def utcnow_iso() -> str:
    """Return current UTC time in ISO 8601 format with +00:00 suffix.

    Returns:
        str: ISO 8601 UTC timestamp string (e.g. '2026-09-17T03:00:00.123456+00:00').
    """
    return datetime.now(timezone.utc).isoformat()
