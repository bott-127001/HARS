"""§1 — HARS engine (HARSStrategyEngine) calculation tests."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backend.hars_engine import HARSStrategyEngine


class TestCalculateHurst:
    def test_mean_reverting_series_h_below_threshold(self) -> None:
        rng = np.random.default_rng(1)
        n = 600
        noise = rng.standard_normal(n)
        ts = np.zeros(n)
        phi = -0.92
        for i in range(1, n):
            ts[i] = phi * ts[i - 1] + 0.3 * noise[i]
        h = HARSStrategyEngine.calculate_hurst(ts)
        assert not np.isnan(h)
        assert float(h) < 0.55

    def test_trending_series_h_above_threshold(self) -> None:
        rng = np.random.default_rng(42)
        steps = rng.standard_normal(512)
        ts = np.cumsum(steps)
        h = HARSStrategyEngine.calculate_hurst(ts)
        assert not np.isnan(h)
        assert float(h) > 0.55

    def test_length_99_returns_nan(self) -> None:
        ts = np.arange(99, dtype=float)
        assert np.isnan(HARSStrategyEngine.calculate_hurst(ts))

    def test_single_rs_scale_returns_nan(self) -> None:
        rng = np.random.default_rng(7)
        ts = rng.standard_normal(100)
        h = HARSStrategyEngine.calculate_hurst(ts)
        assert np.isnan(h)


class TestClassifyRegime:
    def test_mean_reverting_pair(self) -> None:
        eng = HARSStrategyEngine()
        with patch.object(HARSStrategyEngine, "calculate_hurst", side_effect=[0.40, 0.40]):
            label, hi, hv = eng.classify_regime(np.ones(200), np.ones(200))
        assert label == "MEAN_REVERTING"
        assert hi == 0.40 and hv == 0.40

    def test_volatility_shock_pair(self) -> None:
        eng = HARSStrategyEngine()
        with patch.object(HARSStrategyEngine, "calculate_hurst", side_effect=[0.40, 0.70]):
            label, _, _ = eng.classify_regime(np.ones(200), np.ones(200))
        assert label == "VOLATILITY_SHOCK"

    def test_no_trade_pair(self) -> None:
        eng = HARSStrategyEngine()
        with patch.object(HARSStrategyEngine, "calculate_hurst", side_effect=[0.70, 0.40]):
            label, _, _ = eng.classify_regime(np.ones(200), np.ones(200))
        assert label == "NO_TRADE"

    def test_unknown_when_nan(self) -> None:
        eng = HARSStrategyEngine()
        with patch.object(HARSStrategyEngine, "calculate_hurst", side_effect=[np.nan, 0.40]):
            label, _, _ = eng.classify_regime(np.ones(200), np.ones(200))
        assert label == "UNKNOWN"

    def test_boundary_equal_threshold_mean_reverting(self) -> None:
        eng = HARSStrategyEngine()
        with patch.object(HARSStrategyEngine, "calculate_hurst", side_effect=[0.55, 0.55]):
            label, _, _ = eng.classify_regime(np.ones(200), np.ones(200))
        assert label == "MEAN_REVERTING"

    def test_boundary_idx_above_no_trade(self) -> None:
        eng = HARSStrategyEngine()
        with patch.object(HARSStrategyEngine, "calculate_hurst", side_effect=[0.56, 0.40]):
            label, _, _ = eng.classify_regime(np.ones(200), np.ones(200))
        assert label == "NO_TRADE"


class TestGetSignals:
    def _pool_mean_revert(self) -> dict[str, pd.DataFrame]:
        idx = pd.date_range("2026-01-01", periods=30, freq="5min")
        a = pd.DataFrame({"A": 1.0, "B": 1.0, "C": 1.0}, index=idx)
        highs = pd.DataFrame({"A": 102.0, "B": 110.0, "C": 105.0}, index=idx)
        lows = pd.DataFrame({"A": 98.0, "B": 95.0, "C": 99.0}, index=idx)
        vols = pd.DataFrame({"A": 1e6, "B": 1e6, "C": 1e6}, index=idx)
        return {"prices": a, "highs": highs, "lows": lows, "volumes": vols}

    def test_mean_reverting_selects_highest_atr_stock(self) -> None:
        eng = HARSStrategyEngine()
        pool = self._pool_mean_revert()
        sig = eng.get_signals("MEAN_REVERTING", pool)
        assert sig is not None
        assert sig["symbol"] == "B"
        assert sig["target"] == 1.5
        assert sig["stop"] == 1.0
        assert sig["description"] == "High-ATR Mean Reversion"
        atr_b = (110.0 - 95.0) / 1.0
        assert atr_b >= (105.0 - 99.0) / 1.0

    def test_volatility_shock_filters_and_picks_highest_rvol(self) -> None:
        eng = HARSStrategyEngine()
        idx = pd.date_range("2026-01-01", periods=25, freq="5min")
        prices = pd.DataFrame(
            {
                "A": np.linspace(100, 100.5, 25),
                "B": np.linspace(50, 55, 25),
                "C": np.linspace(200, 200.2, 25),
            },
            index=idx,
        )
        va = np.full(25, 1e6)
        va[-1] = 3.5 * 1e6
        vb = np.full(25, 1e6)
        vb[-1] = 2.5 * 1e6
        vc = np.full(25, 1e6)
        vc[-1] = 1.5 * 1e6
        vols = pd.DataFrame({"A": va, "B": vb, "C": vc}, index=idx)
        highs = prices * 1.001
        lows = prices * 0.999
        pool = {"prices": prices, "highs": highs, "lows": lows, "volumes": vols}
        sig = eng.get_signals("VOLATILITY_SHOCK", pool)
        assert sig is not None
        assert sig["symbol"] == "A"
        assert sig["target"] == 2.0
        assert sig["stop"] == 1.0
        assert sig["description"] == "Accumulation Volatility Shock"

    def test_volatility_shock_no_candidates_returns_none(self) -> None:
        eng = HARSStrategyEngine()
        idx = pd.date_range("2026-01-01", periods=25, freq="5min")
        base_a = np.linspace(100, 100.3, 22)
        jump_a = np.array([100.6, 101.5, 103.0])
        a_col = np.concatenate([base_a, jump_a])
        base_b = np.linspace(50, 50.5, 22)
        jump_b = np.array([51.0, 52.5, 54.5])
        b_col = np.concatenate([base_b, jump_b])
        base_c = np.linspace(200, 201.0, 22)
        jump_c = np.array([202.0, 204.5, 207.5])
        c_col = np.concatenate([base_c, jump_c])
        prices = pd.DataFrame({"A": a_col, "B": b_col, "C": c_col}, index=idx)
        vols = pd.DataFrame(
            {
                "A": np.concatenate([np.full(24, 1e6), [4.0 * 1e6]]),
                "B": np.concatenate([np.full(24, 1e6), [4.0 * 1e6]]),
                "C": np.concatenate([np.full(24, 1e6), [4.0 * 1e6]]),
            },
            index=idx,
        )
        highs = prices * 1.001
        lows = prices * 0.999
        pool = {"prices": prices, "highs": highs, "lows": lows, "volumes": vols}
        assert eng.get_signals("VOLATILITY_SHOCK", pool) is None

    def test_no_trade_and_unknown_return_none(self) -> None:
        eng = HARSStrategyEngine()
        pool = self._pool_mean_revert()
        assert eng.get_signals("NO_TRADE", pool) is None
        assert eng.get_signals("UNKNOWN", pool) is None


class TestReturnsPipeline:
    def test_500_closes_yield_499_returns_clean(self) -> None:
        rng = np.random.default_rng(0)
        close = pd.Series(rng.random(500) * 10 + 100)
        ret = close.pct_change().dropna()
        assert len(ret) == 499
        assert not ret.isna().any()
        h = HARSStrategyEngine.calculate_hurst(ret.values)
        assert isinstance(h, (float, np.floating))
        assert not np.isnan(h)
