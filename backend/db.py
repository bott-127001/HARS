"""MongoDB helpers and index setup."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from backend.config import settings

log = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect_mongo_with_retries() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is not None:
        return _db
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is not set")
    delay = 1.0
    max_delay = 60.0
    attempt = 0
    last_err = None
    while delay <= max_delay:
        try:
            _client = AsyncIOMotorClient(settings.mongodb_uri)
            await _client.admin.command("ping")
            _db = _client[settings.mongodb_db_name]
            await ensure_indexes(_db)
            log.info("Connected to MongoDB")
            return _db
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            attempt += 1
            log.critical(
                "MongoDB connection failed attempt %s: %s; retry in %ss",
                attempt,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
    raise RuntimeError(f"MongoDB connection failed after retries: {last_err}")


async def close_mongo() -> None:
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db["candle_cache"].create_index(
        [("instrument_key", 1), ("timestamp", 1)],
        unique=True,
        name="uniq_instrument_timestamp",
    )
    await db["instruments"].create_index([("symbol", 1)])
    await db["instruments"].create_index([("active", 1)])
    await db["trade_history"].create_index([("date", -1)])
    await db["pending_signals"].create_index([("date", 1)])
    await db["market_holidays"].create_index([("date", 1)], unique=True, name="uniq_holiday_date")
    await db["daily_session"].create_index([("date", 1)], unique=True, name="uniq_session_date")


# --- Daily session ---
async def get_daily_session(date_str: str) -> dict[str, Any] | None:
    return await get_db()["daily_session"].find_one({"date": date_str})


async def upsert_daily_session(doc: dict[str, Any]) -> None:
    d = dict(doc)
    await get_db()["daily_session"].update_one(
        {"date": d["date"]},
        {"$set": d},
        upsert=True,
    )

