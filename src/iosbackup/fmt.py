"""Formatting helpers (locale-independent, English)."""

from __future__ import annotations

from datetime import datetime, timezone

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "–"
    value = float(value)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while abs(value) >= 1000 and i < len(units) - 1:
        value /= 1000
        i += 1
    if i == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {units[i]}"


def format_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "–"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone()
    return f"{dt.day} {_MONTHS[dt.month - 1]} {dt.year}, {dt:%H:%M}"
