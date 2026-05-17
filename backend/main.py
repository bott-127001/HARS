"""FastAPI application - REST API and static frontend hosting."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import db as db_layer
from backend import upstox_client
from backend.auth import create_access_token, require_auth
from backend.config import settings
from backend.data_manager import _parse_candles_to_df, mgr, trim_df
from backend.market_time import now_ist, should_skip_precalc_jobs
from backend.scan_service import compute_scan_rows
from backend.scheduler import (
    instruments_refresh,
    market_open_gap_job,
    shutdown_scheduler,
    start_scheduler,
)
from backend.signal_tracker import pending_tracker

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def _warmup_bootstrap_cache() -> None:
    await mgr.seed_bootstrap_instruments_if_empty()
    try:
        await instruments_refresh()
    except Exception as exc:  # noqa: BLE001
        log.warning("instruments refresh warmup failed: %s", exc)

    await mgr.reload_active_instruments()
    await mgr.load_index_vix_from_mongo()
    await mgr.hydrate_from_daily_session_if_today()
    await pending_tracker.reload_from_db()

    ist_now = now_ist()
    today_iso = ist_now.date().isoformat()
    horizon = (ist_now.date() - timedelta(days=14)).isoformat()

    for s in mgr.active_stocks:
        if not s.get("active", True):
            continue

        sym = s["symbol"]
        ik = s["instrument_key"]

        df = mgr.rolling_cache.get(sym)
        if df is None or df.empty:
            try:
                candles = await upstox_client.fetch_historical_candles(
                    ik,
                    "5minute",
                    horizon,
                    today_iso,
                )
                sdf = _parse_candles_to_df(candles)
                if sdf is not None and not sdf.empty:
                    mgr.rolling_cache[sym] = trim_df(sdf, 25)
            except Exception as exc:  # noqa: BLE001
                log.warning("stock warm fetch failed %s: %s", sym, exc)

    if mgr.cache_state != "INSUFFICIENT" and mgr.active_stocks:
        synd = [x["symbol"] for x in mgr.active_stocks if x.get("active", True)]
        gap_ready = bool(mgr.gap_cache) and all(sym in mgr.gap_cache for sym in synd)
        cutoff918 = ist_now.replace(hour=9, minute=18, second=0, microsecond=0)

        if (
            synd
            and (not gap_ready)
            and (not await should_skip_precalc_jobs())
            and ist_now >= cutoff918
        ):
            try:
                await market_open_gap_job()
            except Exception as exc:  # noqa: BLE001
                log.warning("gap hydrate on startup failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_layer.connect_mongo_with_retries()
    asyncio.create_task(_warmup_bootstrap_cache())
    start_scheduler()
    yield
    shutdown_scheduler()
    await upstox_client.close_client()
    await db_layer.close_mongo()


app = FastAPI(title="HARS Signal Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/login")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    if not secrets.compare_digest(form.username, settings.dashboard_username):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not secrets.compare_digest(form.password, settings.dashboard_password):
        raise HTTPException(status_code=401, detail="Unauthorized")

    return {"token": create_access_token()}


@app.get("/api/health")
async def health():
    return {"status": "ok", "cache_ready": mgr.cache_ready_public()}


@app.get("/api/status")
async def api_status(_: None = Depends(require_auth)):
    last_sig = mgr.last_signal if isinstance(mgr.last_signal, dict) else None

    return mgr.api_status_snapshot(signal=last_sig, persist_last_signal=False)


@app.get("/api/scan")
async def api_scan(_: None = Depends(require_auth)):
    sym = mgr.last_signal.get("symbol") if isinstance(mgr.last_signal, dict) else None

    return compute_scan_rows(sym)


@app.get("/api/history")
async def api_history(_: None = Depends(require_auth)):
    coll = db_layer.get_db()["trade_history"]
    rows: list[dict[str, Any]] = []

    async for doc in coll.find({}).sort("date", -1):
        doc = dict(doc)
        doc.pop("_id", None)
        rows.append(doc)

    return rows


@app.post("/api/admin/refresh-instruments")
async def admin_refresh_instruments(_: None = Depends(require_auth)):
    await instruments_refresh()
    return {"ok": True}


class HolidayPayload(BaseModel):

    dates: list[str]


@app.post("/api/admin/refresh-holidays")
async def admin_refresh_holidays(payload: HolidayPayload, _: None = Depends(require_auth)):
    coll = db_layer.get_db()["market_holidays"]

    for d in payload.dates:

        await coll.update_one({"date": d}, {"$set": {"date": d}}, upsert=True)

    return {"ok": True, "count": len(payload.dates)}


dist_dir = Path(__file__).resolve().parents[1] / "frontend" / "dist"

if dist_dir.exists():

    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")
