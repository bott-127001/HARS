"""§4 — Scheduler jobs with frozen IST clock and mocked I/O."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


def _candles_rows(n: int) -> list[list[Any]]:
    out: list[list[Any]] = []
    start = pd.Timestamp("2026-05-11 09:00:00", tz=IST)
    for i in range(n):
        ts = (start + pd.Timedelta(minutes=5 * i)).isoformat()
        o, h, l, c = 100.0 + i * 0.01, 101.0 + i * 0.01, 99.0, 100.5 + i * 0.01
        v = 1_000_000.0
        out.append([ts, o, h, l, c, v])
    return out


@pytest.mark.asyncio
async def test_job0_fires_monday_0845(
    fake_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    reset_mgr: Any,
) -> None:
    """Exercise pre_market core + Mongo session (patched I/O)."""
    import backend.scheduler as sch
    from backend import upstox_client as uc

    fixed = IST.localize(datetime(2026, 5, 11, 8, 45, 0))

    class DTX:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            if tz is not None:
                return fixed.astimezone(tz) if fixed.tzinfo else tz.localize(fixed)
            return fixed

    calls = {"classify": 0}

    def classify_side(idx_r: Any, vix_r: Any) -> tuple[str, float, float]:
        calls["classify"] += 1
        return "MEAN_REVERTING", 0.41, 0.42

    monkeypatch.setattr(sch, "datetime", DTX)
    monkeypatch.setattr("backend.market_time.datetime", DTX)
    monkeypatch.setattr(sch.mgr.engine, "classify_regime", classify_side)
    monkeypatch.setattr(uc, "feed_is_halted", lambda: False)
    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr(sch, "instruments_refresh", AsyncMock())
    monkeypatch.setattr(sch.mgr, "seed_bootstrap_instruments_if_empty", AsyncMock())

    async def noop_before() -> None:
        if uc.feed_is_halted():
            raise RuntimeError("halted")

    monkeypatch.setattr(uc, "_before_request", noop_before)

    async def fake_fetch(ik: str, interval: str, *_r: Any, **_kw: Any) -> list[list[Any]]:
        return _candles_rows(500) if interval == "5minute" else []

    monkeypatch.setattr(sch, "fetch_historical_candles", fake_fetch)
    monkeypatch.setattr(uc, "fetch_historical_candles", fake_fetch)

    reset_mgr.cache_state = "WARMING_UP"
    reset_mgr.active_stocks = [{"symbol": "TEST", "instrument_key": "ik|TEST", "active": True}]
    await sch.pre_market_job()

    assert calls["classify"] == 1
    assert reset_mgr.cache_state == "WARMING_UP_GAP"
    doc = await fake_db["daily_session"].find_one({"date": "2026-05-11"})
    assert doc is not None
    assert doc.get("h_idx") == 0.41


@pytest.mark.asyncio
async def test_job0_skips_saturday(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.scheduler as sch

    sat = IST.localize(datetime(2026, 5, 9, 8, 45, 0))

    class DTX:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return sat if tz is None else sat.astimezone(tz)

    called = {"fetch": 0}

    async def no_fetch(*_a: Any, **_kw: Any) -> list[list[Any]]:
        called["fetch"] += 1
        return []

    monkeypatch.setattr(sch, "datetime", DTX)
    monkeypatch.setattr(sch, "fetch_historical_candles", no_fetch)
    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=True))

    await sch.pre_market_job()
    assert called["fetch"] == 0


@pytest.mark.asyncio
async def test_job0_skips_nse_holiday(fake_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.scheduler as sch

    fixed = IST.localize(datetime(2026, 5, 12, 8, 45, 0))

    class DTX:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(sch, "datetime", DTX)
    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=True))
    await fake_db["market_holidays"].insert_one({"date": "2026-05-12"})

    called = {"f": 0}

    async def no_fetch(*_a: Any, **_kw: Any) -> list[list[Any]]:
        called["f"] += 1
        return []

    monkeypatch.setattr(sch, "fetch_historical_candles", no_fetch)
    await sch.pre_market_job()
    assert called["f"] == 0


@pytest.mark.asyncio
async def test_job0b_sleep_stagger_per_fetch(
    fake_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    reset_mgr: Any,
) -> None:
    import backend.upstox_client as uc
    from backend import scheduler as sch

    ist_now = IST.localize(datetime(2026, 5, 11, 9, 18, 0))

    class DTX:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return ist_now if tz is None else ist_now.astimezone(tz)

    monkeypatch.setattr(sch, "datetime", DTX)
    monkeypatch.setattr("backend.market_time.datetime", DTX)

    monkeypatch.setattr(uc, "feed_is_halted", lambda: False)
    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr(sch.mgr, "mongo_holiday_dates", AsyncMock(return_value=set()))
    monkeypatch.setattr(sch, "prev_trading_date", lambda *_a, **_kw: "2026-05-08")

    async def noop_acquire() -> None:
        return None

    monkeypatch.setattr("backend.rate_limiter.rate_limiter.acquire", noop_acquire)

    stock_rows = [
        [f"2026-05-11T09:15:00+05:30", 100.0, 101.0, 99.0, 100.0, 1e6],
        [f"2026-05-11T09:16:00+05:30", 100.0, 101.0, 99.0, 100.0, 1e6],
    ]
    prev_rows = [
        [f"2026-05-08T15:20:00+05:30", 99.0, 100.0, 98.0, 99.5, 1e6],
    ]

    sleeps: list[float] = []

    async def track_sleep(dt: float) -> None:
        sleeps.append(float(dt))

    async def fake_fetch(
        ik: str,
        interval: str,
        fd: str,
        td: str,
    ) -> list[list[Any]]:
        await track_sleep(0.2)
        if interval == "1minute":
            if fd == "2026-05-11":
                return stock_rows
            return prev_rows
        return []

    from backend import upstox_client as uc

    monkeypatch.setattr(sch, "fetch_historical_candles", fake_fetch)
    monkeypatch.setattr(uc, "fetch_historical_candles", fake_fetch)

    reset_mgr.cache_state = "WARMING_UP_GAP"
    syms = [(f"S{i:02d}", f"ik{i}") for i in range(50)]
    reset_mgr.active_stocks = [{"symbol": a, "instrument_key": b, "active": True} for a, b in syms]

    await sch.market_open_gap_job()

    assert len(sleeps) >= 100
    assert all(abs(s - 0.2) < 1e-6 for s in sleeps[:99] if s > 0)
    assert reset_mgr.cache_state == "READY"
    assert len(reset_mgr.gap_cache) == 50


@pytest.mark.asyncio
async def test_job1_respects_market_hours_and_no_classify(
    fake_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    reset_mgr: Any,
) -> None:
    import backend.scheduler as sch

    ist_open = IST.localize(datetime(2026, 5, 11, 9, 25, 15))

    class DTX:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return ist_open if tz is None else ist_open.astimezone(tz)

    monkeypatch.setattr(sch, "datetime", DTX)
    monkeypatch.setattr("backend.market_time.datetime", DTX)
    monkeypatch.setattr(sch.upstox_client, "feed_is_halted", lambda: False)
    monkeypatch.setattr(sch, "is_market_open", lambda: True)

    async def noop_acquire() -> None:
        return None

    monkeypatch.setattr("backend.rate_limiter.rate_limiter.acquire", noop_acquire)
    fixed_row = [
        IST.localize(datetime(2026, 5, 11, 9, 20, 0)).isoformat(),
        1.0,
        2.0,
        0.5,
        1.5,
        1e6,
    ]
    monkeypatch.setattr(sch, "select_last_closed_5m", lambda *_a, **_kw: fixed_row)

    calls = {"fetch": 0, "classify": 0}

    def no_classify(*_a: Any, **_kw: Any) -> Any:
        calls["classify"] += 1
        return ("X", 0.1, 0.2)

    monkeypatch.setattr(sch.mgr.engine, "classify_regime", no_classify)

    async def count_fetch(*_a: Any, **_kw: Any) -> list[list[Any]]:
        calls["fetch"] += 1
        t = IST.localize(datetime(2026, 5, 11, 9, 20, 0)).isoformat()
        return [[t, 1, 2, 0.5, 1.5, 1e6]]

    from backend import upstox_client as uc

    monkeypatch.setattr(sch, "fetch_historical_candles", count_fetch)
    monkeypatch.setattr(uc, "fetch_historical_candles", count_fetch)

    from backend.constants import INDEX_KEY, VIX_KEY

    reset_mgr.regime = "MEAN_REVERTING"
    reset_mgr.active_stocks = [
        {"symbol": f"S{i:02d}", "instrument_key": f"ik{i}", "active": True} for i in range(50)
    ]
    tzix = pd.DatetimeIndex([pd.Timestamp("2026-05-11 09:20:00", tz=IST)])
    reset_mgr.rolling_cache[INDEX_KEY] = pd.DataFrame(
        {"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [1e6]},
        index=tzix,
    )
    reset_mgr.rolling_cache[VIX_KEY] = reset_mgr.rolling_cache[INDEX_KEY].copy()
    stock_hist = pd.DataFrame(
        {
            "open": np.linspace(1, 2, 25),
            "high": np.linspace(2, 3, 25),
            "low": np.linspace(0.5, 1, 25),
            "close": np.linspace(1.5, 2.5, 25),
            "volume": np.linspace(1e6, 2e6, 25),
        },
        index=pd.date_range("2026-05-10", periods=25, freq="5min", tz=IST),
    )
    for i in range(50):
        reset_mgr.rolling_cache[f"S{i:02d}"] = stock_hist.copy()

    persist_calls: list[str] = []

    async def track_persist(key: str, *a: Any, **kw: Any) -> None:
        persist_calls.append(key)

    monkeypatch.setattr(reset_mgr, "persist_index_vix_rows", track_persist)

    await sch.candle_scan_job()
    assert calls["classify"] == 0
    assert calls["fetch"] == 52

    from backend.constants import INDEX_KEY as IK, VIX_KEY as VK

    assert IK in persist_calls and VK in persist_calls
    assert all(k in (IK, VK) for k in persist_calls), persist_calls


@pytest.mark.asyncio
async def test_job1_closed_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.scheduler as sch

    called = {"n": 0}

    async def boom(*_a: Any, **_kw: Any) -> Any:
        called["n"] += 1
        return []

    monkeypatch.setattr(sch, "fetch_historical_candles", boom)
    monkeypatch.setattr(sch, "is_market_open", lambda: False)
    await sch.candle_scan_job()
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_tp_sl_uses_cache_only_no_fetch(monkeypatch: pytest.MonkeyPatch, reset_mgr: Any, fake_db: Any) -> None:
    import backend.scheduler as sch

    ist_hit = IST.localize(datetime(2026, 5, 11, 10, 0, 45))
    class DTime:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return ist_hit if tz is None else ist_hit.astimezone(tz)

        @classmethod
        def combine(cls, *_a: Any, **_kw: Any) -> datetime:
            return datetime.combine(datetime.min.date(), datetime.min.time())

    monkeypatch.setattr(sch, "datetime", DTime)
    monkeypatch.setattr("backend.market_time.datetime", DTime)
    monkeypatch.setattr(sch, "is_market_open", lambda: True)
    monkeypatch.setattr("backend.market_time.tp_sl_scan_valid", lambda *_a, **_kw: True)

    fetched = {"n": 0}

    async def nope(*_a: Any, **_kw: Any) -> Any:
        fetched["n"] += 1
        return []

    monkeypatch.setattr(sch, "fetch_historical_candles", nope)

    monkeypatch.setattr("backend.signal_tracker._today_str", lambda: "2026-05-11")
    from backend.signal_tracker import pending_tracker

    await fake_db["pending_signals"].insert_one(
        {
            "date": "2026-05-11",
            "symbol": "ONLY",
            "direction": "LONG",
            "entry": 100.0,
            "target_pct": 1.5,
            "stop_pct": 1.0,
            "tp_price": 101.5,
            "sl_price": 99.0,
            "regime": "MEAN_REVERTING",
            "status": "PENDING",
        },
    )
    doc_ps = await fake_db["pending_signals"].find_one({"date": "2026-05-11"})
    assert doc_ps is not None
    pending_tracker.in_memory = dict(doc_ps)

    reset_mgr.rolling_cache["ONLY"] = pd.DataFrame(
        {"open": [1], "high": [2], "low": [0.5], "close": [101.6], "volume": [1e6]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-05-11 10:00:00", tz=IST)]),
    )

    await sch.tp_sl_job()
    assert fetched["n"] == 0
    rec = await fake_db["trade_history"].find_one({"date": "2026-05-11"})
    assert rec["outcome"] == "TP_HIT"
    assert rec["status"] == "WIN"


@pytest.mark.asyncio
async def test_tp_sl_skips_outside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.market_time as mt
    import backend.scheduler as sch

    late = IST.localize(datetime(2026, 5, 11, 15, 11, 45))
    class DLate:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return late if tz is None else late.astimezone(tz)

    monkeypatch.setattr(sch, "datetime", DLate)
    monkeypatch.setattr("backend.market_time.datetime", DLate)
    monkeypatch.setattr(sch, "is_market_open", lambda: True)
    assert not mt.tp_sl_scan_valid(late)

    ran = {"n": 0}

    async def no(*_a: Any, **_kw: Any) -> Any:
        ran["n"] += 1
        return []

    monkeypatch.setattr(sch, "fetch_historical_candles", no)
    await sch.tp_sl_job()
    assert ran["n"] == 0


@pytest.mark.asyncio
async def test_eod_writes_no_trade_when_no_pending(monkeypatch: pytest.MonkeyPatch, reset_mgr: Any, fake_db: Any) -> None:
    import backend.scheduler as sch

    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr("backend.signal_tracker._today_str", lambda: "2026-05-10")
    monkeypatch.setattr("backend.scheduler.ist_date_str", lambda *_a, **_kw: "2026-05-10")

    reset_mgr.regime = "NO_TRADE"
    from backend.signal_tracker import pending_tracker

    pending_tracker.in_memory = None
    await sch.eod_settle_job()
    row = await fake_db["trade_history"].find_one({"date": "2026-05-10"})
    assert row is not None
    assert row.get("direction") == "NO_TRADE"
    assert row.get("symbol") == "-"

