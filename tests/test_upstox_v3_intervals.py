"""Upstox V3 interval mapping."""

from __future__ import annotations

import pytest

from backend.upstox_client import _v3_unit_interval


def test_minute_intervals() -> None:
    assert _v3_unit_interval("1minute") == ("minutes", 1)
    assert _v3_unit_interval("5minute") == ("minutes", 5)


def test_calendar_intervals() -> None:
    assert _v3_unit_interval("day") == ("days", 1)
    assert _v3_unit_interval("week") == ("weeks", 1)
    assert _v3_unit_interval("month") == ("months", 1)


def test_unsupported_interval() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        _v3_unit_interval("2hour")
