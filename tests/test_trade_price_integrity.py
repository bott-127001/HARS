"""Trade price integrity — TP/SL stale-bar guard and EOD 15:10 bar selection."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


def _stock_cache(bars: list[tuple[str, float]]) -> pd.DataFrame:
    """bars: list of (HH:MM, close) on 2026-05-11 IST."""
    idx = [IST.localize(datetime(2026, 5, 11, int(h), int(m))) for h, m in (t.split(":") for t, _ in bars)]
    closes = [c for _, c in bars]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1e6] * len(closes),
        },
        index=pd.DatetimeIndex(idx),
    )


@pytest.fixture
def tp_sl_env(monkeypatch: pytest.MonkeyPatch, reset_mgr: Any, fake_db: Any) -> dict[str, Any]:
    import backend.scheduler as sch

    ist_now = IST.localize(datetime(2026, 5, 11, 10, 0, 45))

    class DTime:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return ist_now if tz is None else ist_now.astimezone(tz)

    monkeypatch.setattr(sch, "datetime", DTime)
    monkeypatch.setattr("backend.market_time.datetime", DTime)
    monkeypatch.setattr(sch, "is_market_open", lambda: True)
    monkeypatch.setattr("backend.market_time.tp_sl_scan_valid", lambda *_a, **_kw: True)
    monkeypatch.setattr("backend.signal_tracker._today_str", lambda: "2026-05-11")
    monkeypatch.setattr(sch, "fetch_historical_candles", AsyncMock(return_value=[]))

    return {"sch": sch, "mgr": reset_mgr, "db": fake_db}


async def _seed_pending(fake_db: Any, entry: float = 100.0) -> None:
    from backend.signal_tracker import pending_tracker

    doc = {
        "date": "2026-05-11",
        "symbol": "ONLY",
        "direction": "LONG",
        "entry": entry,
        "target_pct": 1.5,
        "stop_pct": 1.0,
        "tp_price": entry * 1.015,
        "sl_price": entry * 0.99,
        "regime": "MEAN_REVERTING",
        "status": "PENDING",
    }
    await fake_db["pending_signals"].insert_one(doc)
    pending_tracker.in_memory = dict(doc)


@pytest.mark.asyncio
async def test_tp_sl_rejects_stale_bar(tp_sl_env: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    sch = tp_sl_env["sch"]
    mgr = tp_sl_env["mgr"]
    fake_db = tp_sl_env["db"]

    await _seed_pending(fake_db)
    mgr.rolling_cache["ONLY"] = pd.DataFrame(
        {"open": [1], "high": [2], "low": [0.5], "close": [200.0], "volume": [1e6]},
        index=pd.DatetimeIndex([IST.localize(datetime(2026, 5, 10, 15, 10))]),
    )

    with caplog.at_level("WARNING"):
        await sch.tp_sl_job()

    assert any("Stale bar" in r.message for r in caplog.records)
    rec = await fake_db["trade_history"].find_one({"date": "2026-05-11"})
    assert rec is None


@pytest.mark.asyncio
async def test_tp_sl_accepts_today_bar_above_tp(tp_sl_env: dict[str, Any]) -> None:
    sch = tp_sl_env["sch"]
    mgr = tp_sl_env["mgr"]
    fake_db = tp_sl_env["db"]

    await _seed_pending(fake_db, entry=100.0)
    mgr.rolling_cache["ONLY"] = _stock_cache([("10:00", 101.6)])

    await sch.tp_sl_job()

    rec = await fake_db["trade_history"].find_one({"date": "2026-05-11"})
    assert rec is not None
    assert rec["outcome"] == "TP_HIT"


@pytest.mark.asyncio
async def test_eod_uses_1510_bar_when_available(
    monkeypatch: pytest.MonkeyPatch,
    reset_mgr: Any,
    fake_db: Any,
) -> None:
    import backend.scheduler as sch

    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr("backend.signal_tracker._today_str", lambda: "2026-05-11")
    monkeypatch.setattr(sch, "ist_date_str", lambda *_a, **_kw: "2026-05-11")

    await _seed_pending(fake_db, entry=100.0)
    reset_mgr.rolling_cache["ONLY"] = _stock_cache(
        [("15:05", 100.5), ("15:10", 101.0), ("15:15", 101.5)],
    )

    await sch.eod_settle_job()

    rec = await fake_db["trade_history"].find_one({"date": "2026-05-11"})
    assert rec is not None
    assert rec["outcome"] == "EOD"
    assert rec["exit_price"] == 101.0


@pytest.mark.asyncio
async def test_eod_fallback_when_1510_missing(
    monkeypatch: pytest.MonkeyPatch,
    reset_mgr: Any,
    fake_db: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import backend.scheduler as sch

    monkeypatch.setattr(sch, "should_skip_precalc_jobs", AsyncMock(return_value=False))
    monkeypatch.setattr("backend.signal_tracker._today_str", lambda: "2026-05-11")
    monkeypatch.setattr(sch, "ist_date_str", lambda *_a, **_kw: "2026-05-11")

    await _seed_pending(fake_db, entry=100.0)
    reset_mgr.rolling_cache["ONLY"] = _stock_cache([("15:05", 100.8)])

    with caplog.at_level("WARNING"):
        await sch.eod_settle_job()

    assert any("15:10 bar not found" in r.message for r in caplog.records)
    rec = await fake_db["trade_history"].find_one({"date": "2026-05-11"})
    assert rec is not None
    assert rec["exit_price"] == 100.8


@pytest.mark.asyncio
async def test_stale_entry_bar_discards_signal(
    monkeypatch: pytest.MonkeyPatch,
    reset_mgr: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import backend.scheduler as sch
    from backend.constants import INDEX_KEY, VIX_KEY

    ist_now = IST.localize(datetime(2026, 5, 11, 9, 25, 15))

    class DTime:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return ist_now if tz is None else ist_now.astimezone(tz)

    monkeypatch.setattr(sch, "datetime", DTime)
    monkeypatch.setattr("backend.market_time.datetime", DTime)
    monkeypatch.setattr(sch, "is_market_open", lambda: True)
    monkeypatch.setattr("backend.market_time.candlescan_fire_valid", lambda *_a, **_kw: True)
    monkeypatch.setattr(sch, "ensure_gap_if_incomplete", AsyncMock())
    monkeypatch.setattr(sch, "fetch_historical_candles", AsyncMock(return_value=[]))
    fixed_row = [
        IST.localize(datetime(2026, 5, 11, 9, 20, 0)).isoformat(),
        1.0,
        2.0,
        0.5,
        1.5,
        1e6,
    ]
    monkeypatch.setattr(sch, "select_last_closed_5m", lambda *_a, **_kw: fixed_row)
    monkeypatch.setattr(reset_mgr, "persist_index_vix_rows", AsyncMock())

    signal = {"symbol": "ONLY", "target": 1.5, "stop": 1.0, "description": "test"}
    stale_only = pd.DataFrame(
        {"open": [1], "high": [2], "low": [0.5], "close": [99.0], "volume": [1e6]},
        index=pd.DatetimeIndex([IST.localize(datetime(2026, 5, 10, 15, 10))]),
    )

    def get_signals_stale(*_a: Any, **_kw: Any) -> dict[str, Any]:
        reset_mgr.rolling_cache["ONLY"] = stale_only.copy()
        return signal

    monkeypatch.setattr(sch.mgr.engine, "get_signals", get_signals_stale)

    create_mock = AsyncMock()
    monkeypatch.setattr(sch.pending_tracker, "try_create_pending", create_mock)

    today_ix = pd.DatetimeIndex([pd.Timestamp("2026-05-11 09:20:00", tz=IST)])
    reset_mgr.regime = "MEAN_REVERTING"
    reset_mgr.rolling_cache[INDEX_KEY] = pd.DataFrame(
        {"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [1e6]},
        index=today_ix,
    )
    reset_mgr.rolling_cache[VIX_KEY] = reset_mgr.rolling_cache[INDEX_KEY].copy()
    reset_mgr.rolling_cache["ONLY"] = stale_only.copy()
    reset_mgr.active_stocks = [{"symbol": "ONLY", "instrument_key": "ik|ONLY", "active": True}]

    with caplog.at_level("WARNING"):
        await sch.candle_scan_job()

    create_mock.assert_not_awaited()
    assert any("Entry bar" in r.message and "stale" in r.message for r in caplog.records)
