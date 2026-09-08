"""Tests for the value coercion helpers."""

from __future__ import annotations

import pytest

from custom_components.syncx.const import ANIMATION_FLOW_MAP
from custom_components.syncx.coordinator import (
    duration_to_minutes,
    to_float,
    to_int,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("53.16", 53.16),
        ("940.1 kWh", 940.1),
        ("670.48 kg CO2e", 670.48),
        ("null kWh", None),
        ("", None),
        (None, None),
        (11.17, 11.17),
        (3, 3.0),
        (True, None),
        ("-1.5", -1.5),
        ("no digits here", None),
    ],
)
def test_to_float(raw, expected) -> None:
    """Values arrive as bare numbers, numeric strings and strings with units."""
    assert to_float(raw) == expected


def test_to_int_truncates() -> None:
    """Integer coercion goes through the same tolerant parser."""
    assert to_int("1788841841") == 1788841841
    assert to_int("null") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0:00", 0.0), ("0:05", 5.0), ("2:30", 150.0), ("bad", None), (None, None)],
)
def test_duration_to_minutes(raw, expected) -> None:
    """Backup durations arrive as H:MM strings."""
    assert duration_to_minutes(raw) == expected


def test_flow_map_covers_the_documented_codes() -> None:
    """The transcribed flow table keeps its expected shape."""
    assert ANIMATION_FLOW_MAP["4.12"] == (
        "solar_to_center",
        "center_to_battery",
        "center_to_grid",
        "center_to_home",
    )
    assert "999.9" not in ANIMATION_FLOW_MAP


def test_local_day_start_uses_the_site_timezone() -> None:
    """Midnight is resolved in the plant's own timezone, not the server's."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from custom_components.syncx.coordinator import local_day_start

    kolkata = local_day_start("Asia/Kolkata")
    as_local = datetime.fromtimestamp(kolkata, tz=ZoneInfo("Asia/Kolkata"))
    assert (as_local.hour, as_local.minute, as_local.second) == (0, 0, 0)

    # An unknown or missing zone falls back to UTC rather than raising.
    utc_start = local_day_start("Not/AZone")
    assert datetime.fromtimestamp(utc_start, tz=UTC).hour == 0
    assert local_day_start(None) == utc_start
