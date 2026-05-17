"""Persisted PendingSignal lifecycle + TP/SL/EOD bookkeeping."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytz

from backend import db

IST = pytz.timezone("Asia/Kolkata")


def _today_str() -> str:
    return datetime.now(IST).date().isoformat()


def trade_status(entry: float, exit_price: float) -> str:
    if exit_price > entry:
        return "WIN"
    if exit_price < entry:
        return "LOSS"
    return "BREAKEVEN"


@dataclass
class PendingSignalTracker:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    in_memory: dict[str, Any] | None = None  # keyed by today's date iso

    async def reload_from_db(self) -> None:
        today = _today_str()
        coll = db.get_db()["pending_signals"]
        doc = await coll.find_one({"date": today, "status": "PENDING"})
        async with self.lock:
            self.in_memory = dict(doc) if doc else None

    async def get_active(self) -> dict[str, Any] | None:
        async with self.lock:
            if self.in_memory and self.in_memory.get("status") == "PENDING":
                return dict(self.in_memory)
            return None

    async def try_create_pending(
        self,
        *,
        symbol: str,
        entry_price: float,
        target_pct: float,
        stop_pct: float,
        regime: str,
    ) -> bool:
        today = _today_str()
        coll = db.get_db()["pending_signals"]
        async with self.lock:
            exists = await coll.find_one({"date": today, "status": "PENDING"})
            if exists:
                return False
            if regime in ("NO_TRADE", "UNKNOWN"):
                return False
            tp = entry_price * (1 + float(target_pct) / 100.0)
            sl = entry_price * (1 - float(stop_pct) / 100.0)

            doc = {
                "date": today,
                "symbol": symbol,
                "direction": "LONG",
                "entry": float(entry_price),
                "target_pct": float(target_pct),
                "stop_pct": float(stop_pct),
                "tp_price": tp,
                "sl_price": sl,
                "regime": regime,
                "status": "PENDING",
                "created_at": datetime.now(timezone.utc),
            }

            await coll.update_one({"date": today}, {"$set": doc}, upsert=True)
            self.in_memory = doc
            return True

    async def settle_trade(
        self,
        *,
        outcome: str,
        exit_price: float,
        status_result: str,
    ) -> None:
        ps = await self.get_active()
        if not ps:
            return

        coll_hist = db.get_db()["trade_history"]
        record = {
            "date": ps["date"],
            "symbol": ps["symbol"],
            "direction": "LONG",
            "entry": ps["entry"],
            "tp": ps["tp_price"],
            "sl": ps["sl_price"],
            "exit_price": float(exit_price),
            "regime": ps.get("regime"),
            "outcome": outcome,
            "status": status_result,
        }
        await coll_hist.insert_one(record)

        coll_pend = db.get_db()["pending_signals"]
        await coll_pend.update_one(
            {"date": ps["date"]},
            {"$set": {"status": outcome}},
        )

        async with self.lock:
            self.in_memory = None

    async def write_no_trade_day(self, date_str: str) -> None:
        coll_hist = db.get_db()["trade_history"]
        existing = await coll_hist.find_one({"date": date_str})
        if existing:
            return
        await coll_hist.insert_one(
            {
                "date": date_str,
                "symbol": "-",
                "direction": "NO_TRADE",
                "entry": None,
                "tp": None,
                "sl": None,
                "exit_price": None,
                "regime": "NO_TRADE",
                "outcome": "NO_TRADE",
                "status": "NO_TRADE",
            },
        )


pending_tracker = PendingSignalTracker()
