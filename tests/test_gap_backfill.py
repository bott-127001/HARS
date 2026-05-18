"""Gap% backfill (ensure_gap_if_incomplete) and per-symbol gap in scan rows."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
import pytz

from backend.data_manager import mgr
from backend.scan_service import compute_scan_rows

IST = pytz.timezone("Asia/Kolkata")


def _freeze_scheduler_ist(monkeypatch: pytest.MonkeyPatch, hour: int, minute: int) -> datetime:
    fixed = IST.localize(datetime(2026, 5, 18, hour, minute, 0))

    class DatetimeShim:
        @staticmethod
        def now(tz: Any = None) -> datetime:
            if tz is not None:
                return fixed.astimezone(tz) if fixed.tzinfo else tz.localize(fixed.replace(tzinfo=None))
            return fixed

        fromisoformat = staticmethod(dt.datetime.fromisoformat)

    monkeypatch.setattr("backend.scheduler.datetime", DatetimeShim)
    monkeypatch.setattr("backend.market_time.datetime", DatetimeShim)
    return fixed


@pytest.mark.asyncio
async def test_gap_backfill_fires_when_gap_cache_incomplete_after_0918(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.scheduler as sch

    _freeze_scheduler_ist(monkeypatch, 10, 0)
    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr(sch.upstox_client, "feed_is_halted", lambda: False)

    gap = AsyncMock()
    monkeypatch.setattr(sch, "market_open_gap_job", gap)

    mgr.gap_cache.clear()
    mgr.cache_state = "WARMING_UP_GAP"
    mgr.active_stocks = [
        {"symbol": "AAA", "instrument_key": "ik1", "active": True},
        {"symbol": "BBB", "instrument_key": "ik2", "active": True},
    ]

    await sch.ensure_gap_if_incomplete()

    gap.assert_awaited_once()


@pytest.mark.asyncio
async def test_gap_backfill_does_not_fire_before_0918(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.scheduler as sch

    _freeze_scheduler_ist(monkeypatch, 9, 10)
    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr(sch.upstox_client, "feed_is_halted", lambda: False)

    gap = AsyncMock()
    monkeypatch.setattr(sch, "market_open_gap_job", gap)

    mgr.gap_cache.clear()
    mgr.cache_state = "READY"
    mgr.active_stocks = [{"symbol": "AAA", "instrument_key": "ik1", "active": True}]

    await sch.ensure_gap_if_incomplete()

    gap.assert_not_awaited()


@pytest.mark.asyncio
async def test_gap_backfill_does_not_fire_when_market_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.scheduler as sch

    _freeze_scheduler_ist(monkeypatch, 16, 0)
    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr(sch.upstox_client, "feed_is_halted", lambda: False)

    gap = AsyncMock()
    monkeypatch.setattr(sch, "market_open_gap_job", gap)

    mgr.gap_cache.clear()
    mgr.cache_state = "READY"
    mgr.active_stocks = [{"symbol": "AAA", "instrument_key": "ik1", "active": True}]

    await sch.ensure_gap_if_incomplete()

    gap.assert_not_awaited()


def test_per_symbol_gap_partial_cache() -> None:
    idx = pd.date_range("2026-05-18 09:00", periods=25, freq="5min", tz=IST)
    stock_df = pd.DataFrame(
        {
            "open": np.linspace(1, 2, 25),
            "high": np.linspace(2, 3, 25),
            "low": np.linspace(0.5, 1, 25),
            "close": np.linspace(1.5, 2.5, 25),
            "volume": np.linspace(1e6, 2e6, 25),
        },
        index=idx,
    )

    mgr.cache_state = "READY"
    mgr.active_stocks = [
        {"symbol": "AAA", "instrument_key": "ik1", "active": True},
        {"symbol": "BBB", "instrument_key": "ik2", "active": True},
        {"symbol": "CCC", "instrument_key": "ik3", "active": True},
    ]
    mgr.gap_cache = {
        "AAA": {"gap_pct": 1.5, "today_open": 1.0, "yesterday_close": 1.0},
        "BBB": {"gap_pct": -0.5, "today_open": 1.0, "yesterday_close": 1.0},
    }
    for sym in ("AAA", "BBB", "CCC"):
        mgr.rolling_cache[sym] = stock_df.copy()

    rows = compute_scan_rows(None)
    assert len(rows) == 3
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["AAA"]["gap_pct"] == 1.5
    assert by_sym["BBB"]["gap_pct"] == -0.5
    assert by_sym["CCC"]["gap_pct"] is None
