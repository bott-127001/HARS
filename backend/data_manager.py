"""Rolling caches, instruments, mongo candle persistence, dashboard state."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytz

from backend import db
from backend.constants import BOOTSTRAP_NIFTY50, INDEX_KEY, VIX_KEY
from backend.market_time import ist_date_str, now_ist
from backend.hars_engine import HARSStrategyEngine

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def _parse_candles_to_df(candles: list[list[Any]]) -> pd.DataFrame | None:
    if not candles:
        return None
    rows = []
    for c in candles:
        if len(c) < 6:
            continue
        ts = pd.to_datetime(c[0])
        rows.append({"timestamp": ts, "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]), "oi": float(c[6]) if len(c) > 6 else np.nan})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.set_index(pd.DatetimeIndex(df["timestamp"]), inplace=True)
    df.sort_index(inplace=True)
    return df.drop(columns=["timestamp"], errors="ignore")


def trim_df(df: pd.DataFrame, max_rows: int) -> pd.DataFrame:
    """Keep the last ``max_rows`` rows via ``.tail()`` (bounded memory)."""
    if df is None or df.empty:
        return pd.DataFrame()
    return df.tail(max_rows)


def append_merge_trim(
    existing: pd.DataFrame | None,
    new_df: pd.DataFrame | None,
    max_rows: int,
) -> pd.DataFrame:
    """Merge by time, trim immediately, and release the pre-trim concat buffer."""
    if new_df is None or new_df.empty:
        if existing is None or existing.empty:
            return pd.DataFrame()
        return existing.tail(max_rows)
    if existing is None or existing.empty:
        df = new_df
        out = df.tail(max_rows)
        del df
        return out
    df = pd.concat([existing, new_df])
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    out = df.tail(max_rows)
    del df
    return out


@dataclass
class DataManager:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    rolling_cache: dict[str, pd.DataFrame] = field(default_factory=dict)
    gap_cache: dict[str, dict[str, float]] = field(default_factory=dict)
    active_stocks: list[dict[str, Any]] = field(default_factory=list)
    regime: str = "UNKNOWN"
    h_idx: float | None = None
    h_vix: float | None = None
    nifty_price: float | None = None
    vix_price: float | None = None
    last_signal: dict[str, Any] | None = None
    last_updated: datetime | None = None
    cache_state: str = "WARMING_UP"  # WARMING_UP | WARMING_UP_GAP | READY | INSUFFICIENT
    market_closed_label: bool = False
    data_feed_error: str | None = None
    late_start: bool = False
    late_start_date: str | None = None  # Hurst or gap recovery flagged today → /api/status late_start
    engine: HARSStrategyEngine = field(default_factory=HARSStrategyEngine)

    async def mongo_holiday_dates(self) -> set[str]:
        """All holiday yyyy-mm-dd strings."""
        out: set[str] = set()
        async for doc in db.get_db()["market_holidays"].find({}):
            out.add(doc["date"])
        return out

    def cache_ready_public(self) -> bool:
        return self.cache_state == "READY"

    def get_stock_cache(self, symbol: str) -> pd.DataFrame | None:
        """Single entry point for stock cache lookup (keyed by symbol string)."""
        return self.rolling_cache.get(symbol)

    async def reload_active_instruments(self) -> None:
        coll = db.get_db()["instruments"]
        curs = coll.find({"active": True}).sort("symbol", 1)
        self.active_stocks = [doc async for doc in curs]
        # Keep ~50 equities; tolerate fewer during bootstrap
        if not self.active_stocks:
            log.warning("No active instruments — seed bootstrap required.")

    async def seed_bootstrap_instruments_if_empty(self) -> None:
        coll = db.get_db()["instruments"]
        count = await coll.count_documents({})
        if count > 0:
            return
        today = datetime.now(IST).date().isoformat()
        docs = []
        for sym in BOOTSTRAP_NIFTY50:
            docs.append(
                {
                    "symbol": sym,
                    # Placeholder ISIN-style key; refreshed by instruments job
                    "instrument_key": f"NSE_EQ|BOOTSTRAP|{sym}",
                    "added_on": today,
                    "active": True,
                }
            )
        if docs:
            await coll.insert_many(docs)

    async def load_index_vix_from_mongo(self) -> None:
        coll = db.get_db()["candle_cache"]
        for key in (INDEX_KEY, VIX_KEY):
            cur = coll.find({"instrument_key": key}).sort("timestamp", 1).limit(500)
            rows: list[dict[str, Any]] = []
            ts_list: list[Any] = []
            async for doc in cur:
                ts_list.append(pd.to_datetime(doc["timestamp"]))
                rows.append(
                    {
                        "open": float(doc["open"]),
                        "high": float(doc["high"]),
                        "low": float(doc["low"]),
                        "close": float(doc["close"]),
                        "volume": float(doc["volume"]),
                    },
                )
            if rows:
                df = pd.DataFrame(rows, dtype="float32")
                df.index = pd.DatetimeIndex(ts_list)
                df.sort_index(inplace=True)
                del rows, ts_list
                self.rolling_cache[key] = df.tail(500)
                del df
            elif key not in self.rolling_cache:
                self.rolling_cache[key] = pd.DataFrame()

    async def persist_index_vix_rows(self, key: str, symbol_label: str, df: pd.DataFrame) -> None:
        """Upsert trimmed history for INDEX/VIX (latest 500)."""
        if df is None or df.empty:
            return
        trimmed = trim_df(df, 500)
        coll = db.get_db()["candle_cache"]
        for ts, row in trimmed.iterrows():
            doc = {
                "instrument_key": key,
                "symbol": symbol_label,
                "timestamp": pd.Timestamp(ts).isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            await coll.update_one(
                {"instrument_key": key, "timestamp": doc["timestamp"]},
                {"$set": doc},
                upsert=True,
            )
        await self.trim_mongo_cache_to_500(key)

    async def trim_mongo_cache_to_500(self, instrument_key: str) -> None:
        coll = db.get_db()["candle_cache"]
        cur = coll.find({"instrument_key": instrument_key}).sort("timestamp", -1)
        extra: list[Any] = []
        n = 0
        async for doc in cur:
            n += 1
            if n > 500:
                extra.append(doc["_id"])
        if extra:
            await coll.delete_many({"_id": {"$in": extra}})

    def build_stock_engine_pool(self) -> dict[str, pd.DataFrame]:
        """Assemble column-oriented frames keyed by ACTIVE stock symbols."""

        cols = sorted([x["symbol"] for x in self.active_stocks if x.get("active", True)])

        closes: dict[str, pd.Series] = {}
        highs: dict[str, pd.Series] = {}
        lows: dict[str, pd.Series] = {}
        vols: dict[str, pd.Series] = {}

        for sym in cols:
            df = self.rolling_cache.get(sym)
            if df is None or df.empty:
                closes[sym] = pd.Series(dtype=float)
                highs[sym] = pd.Series(dtype=float)
                lows[sym] = pd.Series(dtype=float)
                vols[sym] = pd.Series(dtype=float)
                continue

            closes[sym] = df["close"]
            highs[sym] = df["high"]
            lows[sym] = df["low"]
            vols[sym] = df["volume"]

        prices = pd.DataFrame(closes)
        hi = pd.DataFrame(highs)
        lo = pd.DataFrame(lows)
        volumes = pd.DataFrame(vols)
        prices.sort_index(inplace=True)
        hi.sort_index(inplace=True)
        lo.sort_index(inplace=True)
        volumes.sort_index(inplace=True)
        return {"prices": prices, "highs": hi, "lows": lo, "volumes": volumes}

    async def hydrate_from_daily_session_if_today(self) -> None:
        today = ist_date_str()
        sess = await db.get_daily_session(today)
        if not sess:
            return
        self.regime = sess.get("regime", self.regime)
        self.h_idx = sess.get("h_idx")
        self.h_vix = sess.get("h_vix")
        cs = sess.get("cache_state")
        if cs:
            self.cache_state = cs

    async def after_restart_gap_or_stocks_empty(self, before_918: bool) -> bool:
        """Return True if gap job should fire immediately."""

        today = ist_date_str()
        if self.gap_cache and all(sym in self.gap_cache for sym in [x["symbol"] for x in self.active_stocks]):
            return False

        holidays = await self.mongo_holiday_dates()
        n = datetime.now(IST).date()
        if n.weekday() >= 5 or today in holidays:
            return False

        if before_918:
            return False
        # After gap time and missing entries
        now = datetime.now(IST).replace(second=0, microsecond=0)
        cutoff = now.replace(hour=9, minute=18)
        return now >= cutoff

    async def update_prices_snapshot(self) -> None:
        idx_df = self.rolling_cache.get(INDEX_KEY)
        vix_df = self.rolling_cache.get(VIX_KEY)
        try:
            if idx_df is not None and not idx_df.empty:
                self.nifty_price = float(idx_df["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            self.nifty_price = None
        try:
            if vix_df is not None and not vix_df.empty:
                self.vix_price = float(vix_df["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            self.vix_price = None

    def api_status_snapshot(
        self,
        *,
        signal: dict[str, Any] | None = None,
        persist_last_signal: bool = True,
    ) -> dict[str, Any]:
        """Build `/api/status` JSON shape; pending values remain null for frontend em-dash."""

        cache_ready = self.cache_ready_public()
        nifty = None if not cache_ready else self.nifty_price
        vx = None if not cache_ready else self.vix_price
        h_ix = None if not cache_ready else self.h_idx
        h_vx = None if not cache_ready else self.h_vix
        regime = self.regime if cache_ready else "UNKNOWN"

        out = {
            "nifty_price": nifty,
            "vix_price": vx,
            "h_idx": h_ix,
            "h_vix": h_vx,
            "regime": regime,
            "signal": signal,
            "cache_ready": cache_ready,
            "last_updated": (self.last_updated.isoformat() if self.last_updated else None),
            "data_feed_error": self.data_feed_error,
            "cache_state": self.cache_state,
            "market_closed": self.market_closed_label,
            "late_start": bool(self.late_start and self.late_start_date == ist_date_str()),
        }
        if persist_last_signal:
            self.last_signal = signal
        return out


mgr = DataManager()
