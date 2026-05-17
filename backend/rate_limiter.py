"""Proactive sliding-window limits for Upstox Analytics API (rulebook §0)."""

import asyncio
import time
from collections import deque

PER_SECOND = 10
PER_MINUTE = 500
PER_30MIN = 2000


class UpstoxRateLimiter:
    """Retain ~30 minutes of timestamps; refuse new calls until margins clear."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._requests: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._requests and self._requests[0] < now - 30 * 60:
            self._requests.popleft()

    async def acquire(self) -> None:
        while True:
            wait_s = 0.0
            async with self._lock:
                now = time.time()
                self._prune(now)
                times = list(self._requests)
                n30 = len(times)
                n60 = sum(1 for t in times if now - t < 60)
                n1 = sum(1 for t in times if now - t < 1)

                ok = n30 <= PER_30MIN - 50 and n60 <= PER_MINUTE - 75 and n1 <= 6
                if ok:
                    self._requests.append(now)
                    return

                if n30 >= PER_30MIN - 50:
                    wait_s = max(wait_s, self._requests[0] + 30 * 60 - now + 0.02)
                if n60 >= PER_MINUTE - 75:
                    idx = len(self._requests) - (PER_MINUTE - 74)
                    if idx >= 0:
                        wait_s = max(wait_s, self._requests[idx] + 60 - now + 0.02)
                if n1 >= 6:
                    recent = [t for t in self._requests if now - t < 1]
                    if recent:
                        wait_s = max(wait_s, min(recent) + 1.0 - now + 0.02)

                if wait_s <= 0:
                    wait_s = 0.05

            await asyncio.sleep(min(wait_s, 5.0))


rate_limiter = UpstoxRateLimiter()
