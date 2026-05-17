"""§6 Scan service + §7 signal_tracker."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pytest

from backend.data_manager import mgr
from backend.scan_service import compute_scan_rows
from backend.signal_tracker import trade_status


def test_trade_status_triplets() -> None:
    assert trade_status(1000, 1015) == "WIN"
    assert trade_status(1000, 990) == "LOSS"
    assert trade_status(1000, 1000) == "BREAKEVEN"


def test_tp_sl_formula_static() -> None:
    entry = 1000.0
    tgt = 1.5
    stp = 1.0
    tp = entry * (1 + tgt / 100.0)
    sl = entry * (1 - stp / 100.0)
    assert math.isclose(tp, 1015.0)
    assert math.isclose(sl, 990.0)


@pytest.mark.asyncio
async def test_pending_writes_long_direction(fake_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.signal_tracker import PendingSignalTracker

    monkeypatch.setattr("backend.signal_tracker._today_str", lambda: "2026-05-10")
    tr = PendingSignalTracker()
    await tr.try_create_pending(
        symbol="TEST",
        entry_price=100.0,
        target_pct=1.5,
        stop_pct=1.0,
        regime="MEAN_REVERTING",
    )
    doc = await fake_db["pending_signals"].find_one({"date": "2026-05-10"})
    assert doc["direction"] == "LONG"


class TestScanCompliance:
    def setup_method(self) -> None:
        mgr.regime = "MEAN_REVERTING"
        mgr.cache_state = "READY"
        mgr.gap_cache.clear()
        mgr.rolling_cache.clear()
        idx = pd.date_range("2026-01-01", periods=25, freq="5min")

        syms = [f"S{i:02d}" for i in range(1, 12)]
        mgr.active_stocks = [{"symbol": s} for s in syms]

        for i, sym in enumerate(syms):
            if sym == "S11":
                mgr.rolling_cache[sym] = pd.DataFrame()
                mgr.gap_cache[sym] = {"gap_pct": -0.5}
                continue
            hi = 100.0 + float(i)
            lo = 95.0 + float(i)
            cl = (hi + lo) / 2.0
            last_vol = (3.0 if sym == "S01" else 1.0) * 1e6
            mgr.rolling_cache[sym] = pd.DataFrame(
                {
                    "open": [cl] * 25,
                    "high": [hi + 5.0] * 25,
                    "low": [lo - 5.0] * 25,
                    "close": [cl] * 25,
                    "volume": [1e6] * 24 + [last_vol],
                },
                index=idx,
            )
            mgr.gap_cache[sym] = {"gap_pct": 1.5 if sym == "S01" else 0.1}

    def test_high_score_vs_zero(self) -> None:
        rows = compute_scan_rows(None)
        by_sym = {r["symbol"]: r for r in rows}
        assert by_sym["S01"]["compliance_score"] == 4
        assert by_sym["S11"]["compliance_score"] == 0

    def test_sort_and_ties(self) -> None:
        mgr.active_stocks = [{"symbol": "X1"}, {"symbol": "X2"}]

        idx = pd.date_range("2026-01-01", periods=25, freq="5min")
        base_hi = 102.0
        base_lo = 98.0

        def mk(sym_close: float, vol_spike: float, close_series: list[float] | None = None) -> pd.DataFrame:
            closes = close_series or ([sym_close - 1.0] * 22 + [sym_close - 0.1, sym_close - 0.05, sym_close])
            return pd.DataFrame(
                {
                    "open": closes,
                    "high": [base_hi] * 25,
                    "low": [base_lo] * 25,
                    "close": closes,
                    "volume": [1e6] * 24 + [vol_spike],
                },
                index=idx,
            )

        c1 = [100.0] * 22 + [100.1, 100.15, 100.2]
        c2 = [200.0] * 22 + [200.1, 200.15, 200.2]
        mgr.rolling_cache["X1"] = mk(100.2, 4.0 * 1e6, c1)
        mgr.rolling_cache["X2"] = mk(200.2, 3.0 * 1e6, c2)
        mgr.gap_cache["X1"] = {"gap_pct": 0.1}
        mgr.gap_cache["X2"] = {"gap_pct": 0.1}

        rows = compute_scan_rows(None)
        assert [r["symbol"] for r in rows] == ["X1", "X2"]
        assert rows[0]["compliance_score"] == rows[1]["compliance_score"]
        assert rows[0]["rvol"] > rows[1]["rvol"]


def test_gap_pending_shows_emdash() -> None:
    mgr.cache_state = "READY"
    mgr.active_stocks = [{"symbol": "ONLY"}]
    mgr.rolling_cache["ONLY"] = pd.DataFrame(
        {
            "open": [1],
            "high": [2],
            "low": [0.5],
            "close": [1.5],
            "volume": [1e6],
        },
        index=pd.date_range("2026-01-01", periods=1, freq="5min"),
    )
    mgr.gap_cache.clear()
    rows = compute_scan_rows(None)
    gp = rows[0]["gap_pct"]
    assert gp is None
    from backend.scan_service import _num

    assert _num(gp, True) is None


def test_atr_percent_formula() -> None:
    highs = pd.Series([102, 103, 104])
    lows = pd.Series([98, 97, 96])
    latest = 100.0
    atr_pct = (float(highs.max()) - float(lows.min())) / latest * 100.0
    assert math.isclose(atr_pct, 8.0)


def test_momentum_15m_formula() -> None:
    prices = pd.Series([100.0, 101.0, 102.0, 103.0])
    mom = (prices.iloc[-1] / prices.iloc[-3] - 1.0) * 100.0
    assert abs(mom - 1.9801980198019802) < 1e-6
