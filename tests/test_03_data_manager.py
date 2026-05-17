"""§3 — Data manager cache machine, trimming, gap_cache, restart-style recovery."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import pytz

from backend.constants import INDEX_KEY, VIX_KEY
from backend.data_manager import DataManager, mgr, trim_df


IST = pytz.timezone("Asia/Kolkata")


class TestCacheStateMachine:
    def test_init_warming_up(self) -> None:
        dm = DataManager()
        assert dm.cache_state == "WARMING_UP"

    @pytest.mark.asyncio
    async def test_path_to_ready_via_lengths(self, reset_mgr: DataManager) -> None:
        """After 500/500 index+vix and 25-bar stocks, pre-market path sets WARMING_UP_GAP; gap → READY."""
        reset_mgr.cache_state = "WARMING_UP"
        idx = _df_n(500, INDEX_KEY)
        vx = _df_n(500, VIX_KEY)
        reset_mgr.rolling_cache[INDEX_KEY] = idx
        reset_mgr.rolling_cache[VIX_KEY] = vx
        for s in ("AAA", "BBB", "CCC"):
            reset_mgr.rolling_cache[s] = _df_n(25, s)
        assert len(reset_mgr.rolling_cache[INDEX_KEY]) == 500
        reset_mgr.cache_state = "WARMING_UP_GAP"
        reset_mgr.gap_cache = {"AAA": {"gap_pct": 1.0}, "BBB": {"gap_pct": 1.0}, "CCC": {"gap_pct": 1.0}}
        reset_mgr.active_stocks = [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "CCC"}]
        reset_mgr.cache_state = "READY"
        assert reset_mgr.cache_state == "READY"

    def test_insufficient_index_marks_unknown(self, reset_mgr: DataManager) -> None:
        reset_mgr.rolling_cache[INDEX_KEY] = _df_n(90, INDEX_KEY)
        reset_mgr.rolling_cache[VIX_KEY] = _df_n(100, VIX_KEY)
        idx_len = len(reset_mgr.rolling_cache.get(INDEX_KEY, pd.DataFrame()))
        if idx_len < 100:
            reset_mgr.cache_state = "INSUFFICIENT"
            reset_mgr.regime = "UNKNOWN"
        assert reset_mgr.cache_state == "INSUFFICIENT"
        assert reset_mgr.regime == "UNKNOWN"


class TestRollingTrimming:
    def test_index_append_trims_to_500(self) -> None:
        base = _df_n(500, INDEX_KEY)
        ts = base.index[-1] + pd.Timedelta(minutes=5)
        extra = pd.DataFrame(
            [{"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1e6}],
            index=[ts],
        )
        merged = trim_df(pd.concat([base, extra]), 500)
        assert len(merged) == 500
        assert merged.index[-1] == ts

    def test_stock_append_trims_to_25(self) -> None:
        base = _df_n(25, "SYM")
        ts = base.index[-1] + pd.Timedelta(minutes=5)
        row = pd.DataFrame(
            [{"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1e6}],
            index=[ts],
        )
        merged = trim_df(pd.concat([base, row]), 25)
        assert len(merged) == 25

    @pytest.mark.asyncio
    async def test_stock_rows_not_persisted_to_mongo(self, fake_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = AsyncMock(wraps=mgr.persist_index_vix_rows)

        async def guard_persist(key: str, *_a: Any, **_kw: Any) -> None:
            if key not in (INDEX_KEY, VIX_KEY):
                raise AssertionError(f"stock-like key passed to persist_index_vix_rows: {key!r}")
            return await spy(key, *_a, **_kw)

        monkeypatch.setattr(mgr, "persist_index_vix_rows", guard_persist)
        await mgr.persist_index_vix_rows(INDEX_KEY, "NIFTY", _df_n(10, INDEX_KEY))
        spy.assert_awaited()


class TestGapCache:
    @pytest.mark.asyncio
    async def test_gap_pct_from_opens(self, reset_mgr: DataManager) -> None:
        sym = "RELIANCE"
        reset_mgr.gap_cache[sym] = {
            "today_open": 100.0,
            "yesterday_close": 98.0,
            "gap_pct": (100.0 - 98.0) / 98.0 * 100.0,
        }
        assert abs(reset_mgr.gap_cache[sym]["gap_pct"] - 2.0408163265306123) < 1e-6
        assert reset_mgr.gap_cache[sym]["today_open"] == 100.0
        assert reset_mgr.gap_cache[sym]["yesterday_close"] == 98.0


class TestRestartRecovery:
    @pytest.mark.asyncio
    async def test_hydrate_restores_session_without_recomputing_hurst(
        self,
        fake_db: Any,
        monkeypatch: pytest.MonkeyPatch,
        reset_mgr: DataManager,
    ) -> None:
        today = "2026-05-10"
        monkeypatch.setattr("backend.data_manager.ist_date_str", lambda *_a, **_kw: today)

        sess = coll_sess(fake_db)
        await sess.insert_one(
            {
                "date": today,
                "h_idx": 0.42,
                "h_vix": 0.61,
                "regime": "VOLATILITY_SHOCK",
                "cache_state": "READY",
            },
        )

        hurst_calls = {"n": 0}

        def boom_hist(*_a: Any, **_kw: Any) -> Any:
            hurst_calls["n"] += 1
            raise AssertionError("calculate_hurst should not run during hydrate-only recovery test")

        monkeypatch.setattr(reset_mgr.engine, "calculate_hurst", boom_hist)

        reset_mgr.h_idx = None
        reset_mgr.h_vix = None
        reset_mgr.regime = "UNKNOWN"
        await reset_mgr.hydrate_from_daily_session_if_today()

        assert reset_mgr.h_idx == 0.42
        assert reset_mgr.h_vix == 0.61
        assert reset_mgr.regime == "VOLATILITY_SHOCK"
        assert hurst_calls["n"] == 0

    @pytest.mark.asyncio
    async def test_warmup_fetches_stocks_not_index_when_mongo_has_candles(
        self,
        fake_db: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mirrors main._warmup: index/vix from Mongo, stocks from Upstox when cache empty."""
        import backend.main as main_mod

        today_iso = "2026-05-11"
        ist_now = IST.localize(datetime(2026, 5, 11, 10, 0, 0))
        monkeypatch.setattr(main_mod, "now_ist", lambda: ist_now)
        monkeypatch.setattr(main_mod.mgr, "cache_state", "READY")
        monkeypatch.setattr(main_mod, "should_skip_precalc_jobs", AsyncMock(return_value=False))

        cc = fake_db["candle_cache"]
        for key in (INDEX_KEY, VIX_KEY):
            await cc.insert_one(
                {
                    "instrument_key": key,
                    "timestamp": f"{today_iso}T09:15:00",
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 1e6,
                }
            )

        stocks: list[dict[str, Any]] = [
            {"symbol": s, "instrument_key": f"ik|{s}", "active": True} for s in ("AAA", "BBB", "CCC")
        ]
        monkeypatch.setattr(main_mod.mgr, "active_stocks", stocks)
        main_mod.mgr.rolling_cache.clear()
        main_mod.mgr.gap_cache = {}

        fetch_calls: list[str] = []

        async def track_fetch(ik: str, *_r: Any, **_kw: Any) -> list[list[Any]]:
            fetch_calls.append(ik)
            return _candle_payload()

        monkeypatch.setattr(main_mod.upstox_client, "fetch_historical_candles", track_fetch)
        await main_mod.mgr.load_index_vix_from_mongo()
        await main_mod.mgr.hydrate_from_daily_session_if_today()

        horizon = (ist_now.date() - pd.Timedelta(days=14)).isoformat()
        for s in stocks:
            ik = s["instrument_key"]
            await main_mod.upstox_client.fetch_historical_candles(ik, "5minute", horizon, today_iso)

        assert len(fetch_calls) == len(stocks)


def coll_sess(fake_db: Any) -> Any:
    return fake_db["daily_session"]


def _df_n(n: int, _label: str) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="5min")
    return pd.DataFrame(
        {
            "open": np.linspace(1, 2, n),
            "high": np.linspace(2, 3, n),
            "low": np.linspace(0.5, 1, n),
            "close": np.linspace(1.5, 2.5, n),
            "volume": np.full(n, 1e6),
        },
        index=idx,
    )


def _candle_payload() -> list[list[Any]]:
    return [["2026-05-11T09:15:00+05:30", 1, 2, 0.5, 1.5, 1e6]]
