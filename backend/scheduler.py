"""APScheduler (Asia/Kolkata) — Upstox fetch + signal jobs."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend import db
from backend.constants import BOOTSTRAP_NIFTY50, INDEX_KEY, TRADING_SYMBOL_ALIASES, VIX_KEY
from backend.data_manager import _parse_candles_to_df, mgr, trim_df
from backend.market_time import (
    candlescan_fire_valid,
    is_market_open,
    ist_date_str,
    prev_trading_date,
    should_skip_precalc_jobs,
    tp_sl_scan_valid,
)
from backend.scan_service import compute_scan_rows
from backend.signal_tracker import pending_tracker, trade_status
from backend import upstox_client
from backend.upstox_client import fetch_historical_candles

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

scheduler = AsyncIOScheduler(timezone=IST)

ENGINE_RUN_LOCK = asyncio.Lock()


def df_from_candles(candles: list[list[Any]]) -> pd.DataFrame | None:
    return _parse_candles_to_df(candles)


def merge_unique_by_time(existing: pd.DataFrame | None, new_df: pd.DataFrame | None) -> pd.DataFrame:
    if new_df is None or new_df.empty:
        return existing if existing is not None else pd.DataFrame()
    if existing is None or existing.empty:
        return new_df
    merged = pd.concat([existing, new_df])
    merged = merged[~merged.index.duplicated(keep="last")]
    merged.sort_index(inplace=True)
    return merged


def normalize_ts(ts: Any) -> datetime:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return IST.localize(t.to_pydatetime())
    return t.to_pydatetime().astimezone(IST)


def select_last_closed_5m(now_ist: datetime, candles: list[list[Any]]) -> list[Any] | None:
    df = df_from_candles(candles)
    if df is None or df.empty:
        return None

    cutoff = now_ist.astimezone(IST) - timedelta(seconds=15)

    last_qual = None

    for ts, row in df.iterrows():

        start = normalize_ts(ts)
        end = start + timedelta(minutes=5)
        if end <= cutoff:
            last_qual = (start, row)

    if not last_qual:

        return None

    start_sel, sel = last_qual
    return [
        start_sel.isoformat(),
        float(sel["open"]),
        float(sel["high"]),
        float(sel["low"]),
        float(sel["close"]),
        float(sel["volume"]),
    ]


async def instruments_refresh() -> None:
    """Refresh active Nifty equities using public instruments bundle."""

    try:
        payload = await upstox_client.download_instruments_json()
    except Exception as exc:  # noqa: BLE001
        log.warning("instruments download failed: %s", exc)
        return

    by_sym = upstox_client.build_nifty50_equity_key_map(
        payload,
        BOOTSTRAP_NIFTY50,
        TRADING_SYMBOL_ALIASES,
    )
    missing = [s for s in BOOTSTRAP_NIFTY50 if s not in by_sym]
    if missing:
        log.warning("instruments refresh: no Upstox key for %s", ", ".join(missing))
    log.info("instruments refresh: resolved %s/%s symbols", len(by_sym), len(BOOTSTRAP_NIFTY50))

    coll = db.get_db()["instruments"]
    today = datetime.now(IST).date().isoformat()

    for sym in BOOTSTRAP_NIFTY50:
        key = by_sym.get(sym)
        if not key:
            continue

        exists = await coll.find_one({"symbol": sym})

        doc = {"symbol": sym, "instrument_key": key, "added_on": today, "active": True}
        if exists:
            await coll.update_one({"_id": exists["_id"]}, {"$set": doc})
        else:
            await coll.insert_one(doc)

    await mgr.reload_active_instruments()


async def pre_market_job(
    *,
    recovery: bool = False,
    history_to_date: str | None = None,
) -> None:
    mgr.market_closed_label = False
    if await should_skip_precalc_jobs():
        mgr.market_closed_label = True

        return
    if upstox_client.feed_is_halted():
        return

    if recovery:
        ist_now = datetime.now(IST)
        log.warning(
            "PRE-MARKET JOB MISSED — running recovery at %s",
            ist_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
        mgr.late_start = True
        mgr.late_start_date = ist_date_str(ist_now)

    mgr.cache_state = "WARMING_UP"

    async with ENGINE_RUN_LOCK:
        await mgr.seed_bootstrap_instruments_if_empty()
        await instruments_refresh()
        await mgr.reload_active_instruments()

        stocks = [(s["symbol"], s["instrument_key"]) for s in mgr.active_stocks if s.get("active", True)]

        today_iso = datetime.now(IST).date().isoformat()
        fetch_to = history_to_date if history_to_date is not None else today_iso

        horizon_start = (datetime.now(IST).date() - timedelta(days=30)).isoformat()

        async def load_series(ik: str, mongo_label: str) -> pd.DataFrame:
            candles = await fetch_historical_candles(ik, "5minute", horizon_start, fetch_to)

            df = df_from_candles(candles)

            if df is None or df.empty:
                return pd.DataFrame()

            merged = merge_unique_by_time(None, df)

            return trim_df(merged, 500)

        nifty_df = await load_series(INDEX_KEY, "NIFTY")

        vix_df = await load_series(VIX_KEY, "VIX")

        mgr.rolling_cache[INDEX_KEY] = nifty_df

        mgr.rolling_cache[VIX_KEY] = vix_df

        await mgr.persist_index_vix_rows(INDEX_KEY, "NIFTY", nifty_df)

        await mgr.persist_index_vix_rows(VIX_KEY, "VIX", vix_df)

        for sym, ik in stocks:
            candles = await fetch_historical_candles(ik, "5minute", horizon_start, today_iso)
            sdf = df_from_candles(candles)
            if sdf is None or sdf.empty:
                mgr.rolling_cache[sym] = pd.DataFrame()

                continue
            mgr.rolling_cache[sym] = trim_df(merge_unique_by_time(None, sdf), 25)

        idx_len = len(mgr.rolling_cache.get(INDEX_KEY, pd.DataFrame()))

        vx_len = len(mgr.rolling_cache.get(VIX_KEY, pd.DataFrame()))

        if idx_len < 100 or vx_len < 100:
            mgr.cache_state = "INSUFFICIENT"

            mgr.regime = "UNKNOWN"

            mgr.h_idx = None

            mgr.h_vix = None

            log.critical("INSUFFICIENT history for Hurst (<100 bars) idx=%s vix=%s", idx_len, vx_len)

            await db.upsert_daily_session(
                {
                    "date": ist_date_str(),
                    "h_idx": None,
                    "h_vix": None,
                    "regime": "UNKNOWN",
                    "cache_state": mgr.cache_state,
                },
            )
            mgr.last_updated = datetime.now(IST)

            return

        idx_returns = nifty_df["close"].astype(float).pct_change().dropna().values
        vix_returns = vix_df["close"].astype(float).pct_change().dropna().values

        regime_label, h_i, h_v = mgr.engine.classify_regime(idx_returns, vix_returns)

        if np.isnan(h_i) or np.isnan(h_v):
            mgr.regime = "UNKNOWN"
            mgr.h_idx = None
            mgr.h_vix = None
        else:

            mgr.regime = regime_label

            mgr.h_idx = float(h_i)

            mgr.h_vix = float(h_v)

        mgr.cache_state = "WARMING_UP_GAP"

        await db.upsert_daily_session(
            {

                "date": ist_date_str(),

                "h_idx": mgr.h_idx,

                "h_vix": mgr.h_vix,

                "regime": mgr.regime,

                "cache_state": mgr.cache_state,

            },

        )

        mgr.last_updated = datetime.now(IST)

        if recovery:
            log.warning(
                "HURST computed late — values valid but session started after scheduled window"
            )


async def market_open_gap_job() -> None:

    if await should_skip_precalc_jobs():
        return
    if upstox_client.feed_is_halted():
        return

    today = datetime.now(IST).date().isoformat()
    holidays = await mgr.mongo_holiday_dates()

    prev_td = prev_trading_date(today, holidays)

    for s in mgr.active_stocks:

        if not s.get("active", True):

            continue
        sym = s["symbol"]
        ik = s["instrument_key"]

        candles_today = await fetch_historical_candles(ik, "1minute", today, today)
        candles_prev = await fetch_historical_candles(ik, "1minute", prev_td, prev_td)

        if not candles_today or not candles_prev:
            mgr.gap_cache.pop(sym, None)

            continue

        first = candles_today[0]

        last_row = candles_prev[-1]

        today_open = float(first[1])
        yesterday_close = float(last_row[4])

        if yesterday_close == 0:
            mgr.gap_cache.pop(sym, None)

            continue

        mgr.gap_cache[sym] = {
            "today_open": today_open,

            "yesterday_close": yesterday_close,

            "gap_pct": (today_open - yesterday_close) / yesterday_close * 100.0,

        }

    if mgr.cache_state != "INSUFFICIENT":

        mgr.cache_state = "READY"

    await db.upsert_daily_session(
        {
            "date": ist_date_str(),
            "h_idx": mgr.h_idx,
            "h_vix": mgr.h_vix,
            "regime": mgr.regime,
            "cache_state": mgr.cache_state,

        },

    )


async def candle_scan_job() -> None:

    if not is_market_open():

        return
    now = datetime.now(IST)

    if not candlescan_fire_valid(now):

        return
    if upstox_client.feed_is_halted():
        return

    async with ENGINE_RUN_LOCK:

        today_iso = datetime.now(IST).date().isoformat()

        horizon_start = (datetime.now(IST).date() - timedelta(days=14)).isoformat()

        idx_fail = False

        async def update_index_series(ik: str, mongo_label: str) -> None:

            nonlocal idx_fail
            candles = await fetch_historical_candles(ik, "5minute", horizon_start, today_iso)
            row = select_last_closed_5m(now, candles)
            if row is None:

                idx_fail = True

                log.warning("No closed 5m candle for %s", ik)

                return

            ts = pd.to_datetime(row[0])

            new_df = pd.DataFrame(
                [
                    {

                        "open": row[1],

                        "high": row[2],

                        "low": row[3],

                        "close": row[4],

                        "volume": row[5],

                    }

                ],
                index=[ts],
            )

            merged = trim_df(merge_unique_by_time(mgr.rolling_cache.get(ik), new_df), 500)

            mgr.rolling_cache[ik] = merged

            await mgr.persist_index_vix_rows(ik, mongo_label, merged)

        await update_index_series(INDEX_KEY, "NIFTY")

        await update_index_series(VIX_KEY, "VIX")

        if idx_fail:

            log.warning("Index/VIX incomplete — skipping engine cycle")

            return

        for s in mgr.active_stocks:

            if not s.get("active", True):
                continue
            candles = await fetch_historical_candles(s["instrument_key"], "5minute", horizon_start, today_iso)
            row = select_last_closed_5m(now, candles)
            if row is None:
                continue

            ts = pd.to_datetime(row[0])

            new_df = pd.DataFrame(
                [
                    {
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[5],
                    },
                ],

                index=[ts],
            )

            sym = s["symbol"]

            merged = trim_df(merge_unique_by_time(mgr.rolling_cache.get(sym), new_df), 25)

            mgr.rolling_cache[sym] = merged

        pool = mgr.build_stock_engine_pool()

        signal = mgr.engine.get_signals(mgr.regime, pool)

        await mgr.update_prices_snapshot()

        mgr.last_updated = datetime.now(IST)

        _ = compute_scan_rows(signal["symbol"] if signal else None)

        mgr.api_status_snapshot(signal=signal, persist_last_signal=True)

        if signal and mgr.regime not in ("NO_TRADE", "UNKNOWN"):
            df_sym = mgr.rolling_cache.get(signal["symbol"])
            if df_sym is None or df_sym.empty:
                log.error("Signal without price dataframe for %s", signal["symbol"])
            else:
                entry_px = float(df_sym["close"].iloc[-1])
                await pending_tracker.try_create_pending(
                    symbol=signal["symbol"],
                    entry_price=entry_px,

                    target_pct=float(signal["target"]),

                    stop_pct=float(signal["stop"]),

                    regime=mgr.regime,

                )


async def tp_sl_job() -> None:

    if not is_market_open():

        return
    now = datetime.now(IST)

    if not tp_sl_scan_valid(now):

        return

    ps = await pending_tracker.get_active()

    if not ps:

        return

    df = mgr.rolling_cache.get(ps["symbol"])

    if df is None or df.empty:
        return
    px = float(df["close"].iloc[-1])

    entry = float(ps["entry"])

    tgt = float(ps["target_pct"])

    stp = float(ps["stop_pct"])

    if px >= entry * (1 + tgt / 100):

        await pending_tracker.settle_trade(
            outcome="TP_HIT",

            exit_price=px,

            status_result=trade_status(entry, px),
        )

    elif px <= entry * (1 - stp / 100):

        await pending_tracker.settle_trade(
            outcome="SL_HIT",

            exit_price=px,

            status_result=trade_status(entry, px),
        )


async def eod_settle_job() -> None:

    if await should_skip_precalc_jobs():
        return
    ps = await pending_tracker.get_active()

    today = ist_date_str()

    if ps:
        df = mgr.rolling_cache.get(ps["symbol"])

        if df is None or df.empty:
            log.warning("EOD settle missing cache for symbol %s", ps["symbol"])
            return
        px = float(df["close"].iloc[-1])

        entry = float(ps["entry"])

        await pending_tracker.settle_trade(outcome="EOD", exit_price=px, status_result=trade_status(entry, px))

        return

    if mgr.regime in ("NO_TRADE", "UNKNOWN"):
        await pending_tracker.write_no_trade_day(today)


async def quarterly_job() -> None:
    """First trading weekday of quarter at 08:00 IST cron — guard repeats calendar logic."""

    now = datetime.now(IST)

    if now.weekday() >= 5:
        return
    holidays = await mgr.mongo_holiday_dates()

    if not _is_first_trading_day_of_quarter(now.date(), holidays):
        return

    await instruments_refresh()


def _is_first_trading_day_of_quarter(day, holidays: set[str]) -> bool:

    if day.month not in {1, 4, 7, 10}:

        return False

    cursor = day.replace(day=1)

    while cursor.weekday() >= 5 or cursor.isoformat() in holidays:
        cursor += timedelta(days=1)

    return day == cursor


async def keep_alive_job() -> None:

    port = os.getenv("PORT", "8000")

    url = f"http://127.0.0.1:{port}/api/health"

    try:

        async with httpx.AsyncClient(timeout=10.0) as client:

            await client.get(url)

    except Exception as exc:  # noqa: BLE001
        log.debug("keep-alive ping skipped/failed: %s", exc)


def register_jobs() -> None:

    scheduler.add_job(pre_market_job, "cron", day_of_week="mon-fri", hour=8, minute=45, id="job0_pm")

    scheduler.add_job(market_open_gap_job, "cron", day_of_week="mon-fri", hour=9, minute=18, id="job0b_gap")

    scheduler.add_job(
        candle_scan_job,
        "cron",
        day_of_week="mon-fri",
        minute="*/5",
        second=15,
        id="job1_scan",
    )

    scheduler.add_job(eod_settle_job, "cron", day_of_week="mon-fri", hour=15, minute=15, id="job3_eod")

    scheduler.add_job(
        tp_sl_job,
        "cron",
        day_of_week="mon-fri",
        minute="*/5",
        second=45,
        id="job4_tp_sl",
    )

    scheduler.add_job(
        quarterly_job,
        "cron",
        day_of_week="mon-fri",
        hour=8,
        minute=0,
        month="1,4,7,10",
        id="job5_q",
    )

    scheduler.add_job(keep_alive_job, "interval", minutes=10, id="job6_keepalive")


def start_scheduler() -> None:

    if scheduler.running:
        return
    register_jobs()

    scheduler.start()


def shutdown_scheduler() -> None:

    if scheduler.running:

        scheduler.shutdown(wait=False)
