"""Upstox Analytics REST client (historical candles + instruments file)."""

import asyncio
import gzip
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from backend.config import settings
from backend.rate_limiter import rate_limiter
from backend.data_manager import mgr as data_mgr_singleton

log = logging.getLogger(__name__)

INSTRUMENTS_USER_AGENT = "HARS/1.0 (Compatible; Upstox API Client)"

FEED_HARD_STOP = asyncio.Event()

_client: httpx.AsyncClient | None = None


def feed_is_halted() -> bool:
    return FEED_HARD_STOP.is_set()


def reset_feed_halt() -> None:
    FEED_HARD_STOP.clear()


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=2,
                keepalive_expiry=10,
            ),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _maybe_halt_sleep() -> None:
    """After 429, pause all callers."""
    await asyncio.sleep(0)


async def _before_request() -> None:
    if feed_is_halted():
        raise RuntimeError("Upstox feed halted (401/refresh token required)")
    await rate_limiter.acquire()


def _v3_unit_interval(interval: str) -> tuple[str, int]:
    """Map legacy interval strings to Upstox V3 path segments (unit, interval)."""
    if interval.endswith("minute"):
        try:
            minutes = int(interval.removesuffix("minute"))
        except ValueError as exc:
            raise ValueError(f"Unsupported interval: {interval}") from exc
        if not 1 <= minutes <= 300:
            raise ValueError(f"Minute interval out of range (1-300): {minutes}")
        return "minutes", minutes

    fixed: dict[str, tuple[str, int]] = {
        "day": ("days", 1),
        "week": ("weeks", 1),
        "month": ("months", 1),
    }
    if interval in fixed:
        return fixed[interval]
    raise ValueError(f"Unsupported interval: {interval}")


def _v3_historical_url(instrument_key: str, interval: str, from_date_iso: str, to_date_iso: str) -> str:
    ik = quote(instrument_key, safe="")
    unit, iv = _v3_unit_interval(interval)
    return (
        f"{settings.upstox_api_base}/v3/historical-candle/"
        f"{ik}/{unit}/{iv}/{to_date_iso}/{from_date_iso}"
    )


async def _parse_candle_response(resp: httpx.Response, instrument_key: str, url: str) -> list[list[Any]]:
    """Shared handler for V3 historical candle responses."""

    async def halting_429() -> None:
        log.critical("Upstox 429 — halting upstream calls for 60s per rulebook §14")
        FEED_HARD_STOP.set()
        await asyncio.sleep(60.0)
        FEED_HARD_STOP.clear()

    await asyncio.sleep(0.2)  # stagger per rulebook (after response)

    if resp.status_code == 401:
        log.critical("Upstox 401 unauthorised — halt until redeploy/token refresh.")
        try:
            data_mgr_singleton.data_feed_error = "Data Feed Error"
        except Exception:  # noqa: BLE001
            pass
        FEED_HARD_STOP.set()
        return []

    if resp.status_code == 429:
        await halting_429()
        return []

    if resp.status_code == 400:
        log.warning(
            "Upstox 400 for %s — %s (body=%s)",
            instrument_key,
            url,
            (resp.text or "")[:200],
        )
        return []

    resp.raise_for_status()
    try:
        data_mgr_singleton.data_feed_error = None
    except Exception:  # noqa: BLE001
        pass
    payload = resp.json()
    return (((payload or {}).get("data") or {}).get("candles")) or []


async def fetch_historical_candles(
    instrument_key: str,
    interval: str,
    from_date_iso: str,
    to_date_iso: str,
) -> list[list[Any]]:
    """Return raw candle arrays from API or [] on recoverable empties."""

    await _before_request()

    url = _v3_historical_url(instrument_key, interval, from_date_iso, to_date_iso)
    headers = {"Authorization": f"Bearer {settings.upstox_analytics_token}", "Accept": "application/json"}
    client = await get_client()
    resp = await client.get(url, headers=headers)
    return await _parse_candle_response(resp, instrument_key, url)


def build_nifty50_equity_key_map(
    rows: list[dict[str, Any]],
    symbols: list[str],
    aliases: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map dashboard symbols to Upstox instrument_key (NSE EQ only)."""
    aliases = aliases or {}
    lookup = {sym: aliases.get(sym, sym) for sym in symbols}
    want_trading = set(lookup.values())
    by_trading: dict[str, str] = {}

    for row in rows:
        if row.get("segment") != "NSE_EQ" or row.get("instrument_type") != "EQ":
            continue
        tsym = row.get("trading_symbol") or row.get("tradingsymbol")
        ik = row.get("instrument_key")
        if not tsym or not ik or tsym not in want_trading:
            continue
        by_trading[tsym] = ik

    out: dict[str, str] = {}
    for sym, trad_sym in lookup.items():
        key = by_trading.get(trad_sym)
        if key:
            out[sym] = key
    return out


async def download_instruments_json() -> list[dict[str, Any]]:
    """Download public BOD instruments JSON (gzip) for mapping symbols → instrument_key."""
    await _before_request()
    client = await get_client()
    url = settings.upstox_instruments_json_gz_url
    resp = await client.get(url, headers={"User-Agent": INSTRUMENTS_USER_AGENT})
    resp.raise_for_status()
    raw = gzip.decompress(resp.content)
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data:
        inner = data["data"]
        if isinstance(inner, list):
            return inner
    raise ValueError("Unexpected instruments JSON layout")

