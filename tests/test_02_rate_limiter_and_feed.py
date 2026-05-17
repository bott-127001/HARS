"""§2 — Sliding-window rate limiter + 401/429 feed behaviour (implementation as wired in upstox_client)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.rate_limiter import UpstoxRateLimiter


@pytest.fixture
def isolated_limiter() -> UpstoxRateLimiter:
    return UpstoxRateLimiter()


class TestSlidingWindowCounters:
    @pytest.mark.asyncio
    async def test_eighth_request_in_same_wall_second_waits(self, isolated_limiter: UpstoxRateLimiter) -> None:
        """Rulebook mentions 10/s; implementation uses n1<=6 → 7 grants without wait, 8th waits."""

        t0 = 1_000_000.0
        clock = {"v": t0}

        def fake_time() -> float:
            return clock["v"]

        sleeps: list[float] = []

        async def fake_sleep(dt: float) -> None:
            sleeps.append(dt)
            clock["v"] += min(dt, 5.0) if dt > 0 else dt

        with patch("backend.rate_limiter.time.time", fake_time), patch("asyncio.sleep", fake_sleep):
            for _ in range(8):
                await isolated_limiter.acquire()
        assert sleeps, "expected throttle sleep on 8th acquire (same frozen clock → n1>6)"
        assert any(s > 0 for s in sleeps)

    @pytest.mark.asyncio
    async def test_minute_guard_exists_but_is_masked_by_second_guard(self, isolated_limiter: UpstoxRateLimiter) -> None:
        """PER_MINUTE-75 is checked, but satisfying n1<=6 caps ~7/s so n60 rarely binds first."""

        t0 = 2_000_000.0
        clock = {"v": t0}
        step = 0.12

        def fake_time() -> float:
            return clock["v"]

        async def fake_sleep(dt: float) -> None:
            clock["v"] += min(dt, 5.0) if dt > 0 else 0.0

        sleeps: list[float] = []

        async def counting_sleep(dt: float) -> None:
            sleeps.append(dt)
            await fake_sleep(dt)

        with patch("backend.rate_limiter.time.time", fake_time), patch("asyncio.sleep", counting_sleep):
            for _ in range(60):
                await isolated_limiter.acquire()
                clock["v"] += step
        assert sleeps, "expected sleep once per-second saturation exceeds the n1<=6 bucket"
        # With step=0.12s, ~8.3 req/s → second bucket trips before minute bucket in practice

    @pytest.mark.asyncio
    async def test_counters_reset_after_window(self, isolated_limiter: UpstoxRateLimiter) -> None:
        t0 = 5_000_000.0
        clock = {"v": t0}

        def fake_time() -> float:
            return clock["v"]

        async def fake_sleep(dt: float) -> None:
            clock["v"] += dt

        with patch("backend.rate_limiter.time.time", fake_time), patch("asyncio.sleep", fake_sleep):
            for _ in range(8):
                await isolated_limiter.acquire()
            clock["v"] += 2.0
            sleeps: list[float] = []

            async def track_sleep(dt: float) -> None:
                sleeps.append(dt)
                await fake_sleep(dt)

            with patch("asyncio.sleep", track_sleep):
                await isolated_limiter.acquire()
        assert not sleeps, "after 2s gap, per-second window should reset"


@pytest.mark.asyncio
async def test_429_sets_global_pause_and_logs_critical(caplog: pytest.LogCaptureFixture) -> None:
    """Global pause: FEED_HARD_STOP + 60s sleep in halting_429; concurrent calls fail fast."""

    import asyncio as aio

    import backend.upstox_client as uc

    caplog.set_level(logging.CRITICAL, logger="backend.upstox_client")
    uc.reset_feed_halt()

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.raise_for_status = MagicMock()

    sleep_block = aio.Event()
    entered = aio.Event()
    orig_sleep = aio.sleep

    async def selective_sleep(dt: float) -> None:
        if dt >= 59.0:
            entered.set()
            await sleep_block.wait()
        elif dt <= 0.01:
            await orig_sleep(0)
        else:
            await orig_sleep(0)

    async def fake_get(*_a: Any, **_kw: Any) -> MagicMock:
        return mock_resp

    with (
        patch.object(uc, "get_client", new=AsyncMock(return_value=MagicMock(get=fake_get))),
        patch("backend.upstox_client.asyncio.sleep", selective_sleep),
    ):
        first = aio.create_task(uc.fetch_historical_candles("NSE_EQ|X", "5minute", "2026-01-01", "2026-01-02"))
        await aio.wait_for(entered.wait(), timeout=3.0)

        second = aio.create_task(uc.fetch_historical_candles("NSE_EQ|Y", "5minute", "2026-01-01", "2026-01-02"))
        await aio.sleep(0.05)
        assert uc.feed_is_halted()
        with pytest.raises(RuntimeError, match="halted"):
            await second

        sleep_block.set()
        await first

    assert any("429" in r.message for r in caplog.records), caplog.records


@pytest.mark.asyncio
async def test_401_sets_data_feed_error_and_halts(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.upstox_client as uc
    from backend import data_manager

    uc.reset_feed_halt()
    data_manager.mgr.data_feed_error = None

    mock_resp = MagicMock()
    mock_resp.status_code = 401

    async def fake_get(*_a: Any, **_kw: Any) -> MagicMock:
        return mock_resp

    monkeypatch.setattr(uc, "get_client", AsyncMock(return_value=MagicMock(get=fake_get)))

    out = await uc.fetch_historical_candles("NSE_EQ|Z", "5minute", "2026-01-01", "2026-01-02")
    assert out == []
    assert data_manager.mgr.data_feed_error == "Data Feed Error"
    assert uc.feed_is_halted()

    with pytest.raises(RuntimeError, match="halted"):
        await uc.fetch_historical_candles("NSE_EQ|Z", "5minute", "2026-01-01", "2026-01-02")
