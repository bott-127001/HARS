"""§9 — Sequential mocked day: Job 0 → 0b → PendingSignal dedup."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


@pytest.mark.asyncio
async def test_sequence_and_pending_dedup(fake_db: Any, monkeypatch: pytest.MonkeyPatch, reset_mgr: Any) -> None:
    import backend.scheduler as sch
    import backend.signal_tracker as st_mod
    from backend import upstox_client as uc

    rows500: list[list[Any]] = []
    start = pd.Timestamp("2026-05-01 09:15:00", tz=IST)
    for i in range(500):
        ts = (start + pd.Timedelta(minutes=5 * i)).isoformat()
        rows500.append([ts, 100.0, 101.0, 99.0, 100.0 + i * 0.001, 1e6])

    tm845 = IST.localize(datetime(2026, 5, 11, 8, 45, 0))

    class DT845:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return tm845 if tz is None else tm845.astimezone(tz)

    monkeypatch.setattr(sch, "datetime", DT845)
    monkeypatch.setattr("backend.market_time.datetime", DT845)
    monkeypatch.setattr(uc, "feed_is_halted", lambda: False)
    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr(sch, "instruments_refresh", AsyncMock())
    monkeypatch.setattr(sch.mgr, "seed_bootstrap_instruments_if_empty", AsyncMock())

    async def noop_before() -> None:
        if uc.feed_is_halted():
            raise RuntimeError("halted")

    monkeypatch.setattr(uc, "_before_request", noop_before)

    async def fetch_pm(ik: str, interval: str, *_a: Any, **_kw: Any) -> list[list[Any]]:
        if interval == "5minute":
            return rows500
        return []

    monkeypatch.setattr(sch, "fetch_historical_candles", fetch_pm)
    monkeypatch.setattr(uc, "fetch_historical_candles", fetch_pm)

    reset_mgr.cache_state = "WARMING_UP"
    reset_mgr.active_stocks = [{"symbol": "S00", "instrument_key": "ik0", "active": True}]

    await sch.pre_market_job()
    assert reset_mgr.cache_state == "WARMING_UP_GAP"

    tm918 = IST.localize(datetime(2026, 5, 11, 9, 18, 0))

    class DT918:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return tm918 if tz is None else tm918.astimezone(tz)

    monkeypatch.setattr(sch, "datetime", DT918)
    monkeypatch.setattr("backend.market_time.datetime", DT918)
    monkeypatch.setattr(sch.mgr, "mongo_holiday_dates", AsyncMock(return_value=set()))
    monkeypatch.setattr(sch, "prev_trading_date", lambda *_a, **_kw: "2026-05-08")

    async def fetch_gap(
        ik: str,
        interval: str,
        fd: str,
        _td: str,
    ) -> list[list[Any]]:
        if interval != "1minute":
            return []
        if fd == "2026-05-11":
            return [["2026-05-11T09:15:00+05:30", 100.0, 101.0, 99.0, 100.0, 1e6]]
        return [["2026-05-08T15:25:00+05:30", 99.0, 100.0, 98.0, 98.5, 1e6]]

    monkeypatch.setattr(sch, "fetch_historical_candles", fetch_gap)
    monkeypatch.setattr(uc, "fetch_historical_candles", fetch_gap)
    await sch.market_open_gap_job()
    assert reset_mgr.cache_state == "READY"

    monkeypatch.setattr(st_mod, "_today_str", lambda: "2026-05-11")
    from backend.signal_tracker import pending_tracker

    ok1 = await pending_tracker.try_create_pending(
        symbol="S00",
        entry_price=100.0,
        target_pct=1.5,
        stop_pct=1.0,
        regime="MEAN_REVERTING",
    )
    ok2 = await pending_tracker.try_create_pending(
        symbol="S00",
        entry_price=100.0,
        target_pct=1.5,
        stop_pct=1.0,
        regime="MEAN_REVERTING",
    )
    assert ok1 is True
    assert ok2 is False

