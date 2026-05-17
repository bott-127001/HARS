"""Market hours / calendar guards (Asia/Kolkata)."""
from datetime import datetime, timedelta

import pytz

IST = pytz.timezone("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def ist_date_str(dt: datetime | None = None) -> str:
    n = dt or now_ist()
    if n.tzinfo is None:
        n = IST.localize(n)
    else:
        n = n.astimezone(IST)
    return n.date().isoformat()


def is_market_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def is_tp_sl_job_window() -> bool:
    """INTRADAY_TP_SL_CHECK active 09:15 to 15:10 IST (rulebook Job 4)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    mo = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=10, second=0, microsecond=0)
    return mo <= now <= end


async def should_skip_precalc_jobs() -> bool:
    """Skip Jobs 0, 0b, 3 when weekend IST or MongoDB holiday for today's date."""
    from backend.db import get_db

    now = datetime.now(IST)
    if now.weekday() >= 5:
        return True
    today = ist_date_str(now)
    db = get_db()
    hol = await db["market_holidays"].find_one({"date": today})
    return hol is not None


def prev_trading_date(start_date_iso: str, holidays: set[str], max_steps: int = 20) -> str:
    """Step calendar back skipping weekends/holidays; start_date_iso is YYYY-MM-DD (current day)."""
    day = datetime.fromisoformat(start_date_iso).date()
    steps = 0
    while steps < max_steps:
        day -= timedelta(days=1)
        steps += 1
        if day.weekday() >= 5:
            continue
        ds = day.isoformat()
        if ds in holidays:
            continue
        return ds
    return day.isoformat()


def tp_sl_scan_valid(now_ist_dt: datetime) -> bool:
    """INTRADAIL TP/SL job timing — :45 ticks, weekdays, 09:15–15:10 IST."""

    if now_ist_dt.weekday() >= 5:
        return False
    hour = now_ist_dt.hour
    minute = now_ist_dt.minute
    second = now_ist_dt.second
    if second != 45 or minute % 5 != 0:
        return False
    mo = now_ist_dt.replace(hour=9, minute=15, second=0, microsecond=0)
    hi = now_ist_dt.replace(hour=15, minute=10, second=59, microsecond=0)
    t = now_ist_dt.replace(second=45, microsecond=0)
    return mo <= t <= hi


def candlescan_fire_valid(now_ist_dt: datetime) -> bool:
    """Cron validation: weekday, second==15, 5-min ladder 09:20..15:25 inclusive."""
    if now_ist_dt.weekday() >= 5:
        return False
    hour = now_ist_dt.hour
    minute = now_ist_dt.minute
    second = now_ist_dt.second
    if second != 15:
        return False
    if minute % 5 != 0:
        return False
    if hour < 9 or hour > 15:
        return False
    if hour == 9 and minute < 20:
        return False
    if hour == 15 and minute > 25:
        return False
    return True
