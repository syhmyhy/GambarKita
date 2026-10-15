"""Admission control for CPU/RAM-heavy image decoding.

Decoding cost scales with the number of pixels, not with upload size: a few
kilobytes of lossless WebP can declare a 24-megapixel canvas. Jobs therefore
reserve their pixel count from a shared budget (which bounds peak memory), and
the number of simultaneous jobs is capped too (which bounds CPU).

Admission is strictly FIFO with direct handoff. A waiting large photo can't be
starved by a stream of smaller ones arriving later: no newcomer takes capacity
while anyone is queued. Requests that can't be admitted within the queue
timeout get ``503 SERVICE_BUSY``.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.errors import ServiceBusyError


class ProcessingBudget:
    def __init__(self, *, max_pixels: int, max_jobs: int, queue_timeout: float) -> None:
        self._max_pixels = max_pixels
        self._max_jobs = max_jobs
        self._queue_timeout = queue_timeout
        self._pixels_in_use = 0
        self._jobs = 0
        self._waiters: deque[tuple[asyncio.Future[None], int]] = deque()

    @property
    def pixels_in_use(self) -> int:
        return self._pixels_in_use

    @property
    def active_jobs(self) -> int:
        return self._jobs

    @property
    def queued(self) -> int:
        return len(self._waiters)

    def weight_for(self, pixels: int) -> int:
        # A single job larger than the whole budget may still run, alone.
        return max(1, min(pixels, self._max_pixels))

    async def acquire(self, pixels: int) -> int:
        """Wait (FIFO) until the job fits; returns the weight to pass to ``release``."""
        weight = self.weight_for(pixels)
        if not self._waiters and self._fits(weight):
            self._take(weight)
            return weight

        granted: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (granted, weight)
        self._waiters.append(entry)
        try:
            await asyncio.wait_for(asyncio.shield(granted), timeout=self._queue_timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if granted.done() and not granted.cancelled():
                self.release(weight)  # granted in the same instant: hand it back
            else:
                granted.cancel()
                self._waiters.remove(entry)
                self._wake()  # the queue head may have changed
            if isinstance(exc, TimeoutError):
                raise ServiceBusyError() from None
            raise
        return weight

    def release(self, weight: int) -> None:
        self._pixels_in_use -= weight
        self._jobs -= 1
        self._wake()

    @asynccontextmanager
    async def reserve(self, pixels: int) -> AsyncIterator[None]:
        weight = await self.acquire(pixels)
        try:
            yield
        finally:
            self.release(weight)

    def _fits(self, weight: int) -> bool:
        return self._jobs < self._max_jobs and self._pixels_in_use + weight <= self._max_pixels

    def _take(self, weight: int) -> None:
        self._pixels_in_use += weight
        self._jobs += 1

    def _wake(self) -> None:
        # Capacity is charged *before* the waiter resumes (direct handoff), so
        # nothing can slip in between the wake-up and the waiter running.
        while self._waiters:
            granted, weight = self._waiters[0]
            if granted.done():
                self._waiters.popleft()
                continue
            if not self._fits(weight):
                return
            self._waiters.popleft()
            self._take(weight)
            granted.set_result(None)
