"""Missed pre-market Job 0 recovery on late server start."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytz

from backend.data_manager import mgr

IST = pytz.timezone("Asia/Kolkata")


def _freeze_ist(monkeypatch: pytest.MonkeyPatch, hour: int, minute: int) -> datetime:
    fixed = IST.localize(datetime(2026, 5, 18, hour, minute, 0))

    class DatetimeShim:
        @staticmethod
        def now(tz: Any = None) -> datetime:
            if tz is not None:
                return fixed.astimezone(tz) if fixed.tzinfo else tz.localize(fixed.replace(tzinfo=None))
            return fixed

        fromisoformat = staticmethod(dt.datetime.fromisoformat)

    monkeypatch.setattr("backend.market_time.datetime", DatetimeShim)
    monkeypatch.setattr("backend.scheduler.datetime", DatetimeShim)
    return fixed


@pytest.mark.asyncio
async def test_recovery_fires_when_daily_session_missing_after_0845(
    fake_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.main as main

    fixed = _freeze_ist(monkeypatch, 10, 0)
    monkeypatch.setattr(main, "now_ist", lambda: fixed)
    monkeypatch.setattr("backend.market_time.now_ist", lambda: fixed)
    monkeypatch.setattr(main, "should_skip_precalc_jobs", AsyncMock(return_value=False))

    pm = AsyncMock()
    gap = AsyncMock()
    monkeypatch.setattr(main, "pre_market_job", pm)
    monkeypatch.setattr(main, "market_open_gap_job", gap)

    await main._run_premarket_recovery_if_needed()

    pm.assert_awaited_once()
    assert pm.await_args.kwargs.get("recovery") is True
    assert pm.await_args.kwargs.get("history_to_date") is not None
    gap.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_recovery_when_market_closed(
    fake_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.main as main

    fixed = _freeze_ist(monkeypatch, 16, 0)
    monkeypatch.setattr(main, "now_ist", lambda: fixed)
    monkeypatch.setattr("backend.market_time.now_ist", lambda: fixed)
    monkeypatch.setattr(main, "should_skip_precalc_jobs", AsyncMock(return_value=False))

    pm = AsyncMock()
    monkeypatch.setattr(main, "pre_market_job", pm)

    mgr.cache_state = "READY"
    mgr.regime = "MEAN_REVERTING"

    await main._run_premarket_recovery_if_needed()

    pm.assert_not_awaited()
    assert mgr.cache_state == "INSUFFICIENT"
    assert mgr.regime == "UNKNOWN"

    doc = await fake_db["daily_session"].find_one({"date": "2026-05-18"})
    assert doc is not None
    assert doc["cache_state"] == "INSUFFICIENT"
