import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from api.ports import AdmissionMetrics, BacklogReader

MAX_RETRY_AFTER_SECONDS = 30
INFLIGHT_RETRY_AFTER_SECONDS = 5


class Overloaded(Exception):
    def __init__(self, detail: str, retry_after: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after = retry_after


def retry_after_seconds(backlog: int, limit: int) -> int:
    return max(1, min(MAX_RETRY_AFTER_SECONDS, (backlog - limit) // 10 + 1))


class BacklogGate:
    def __init__(
        self,
        queue: str,
        limit: int,
        metrics: AdmissionMetrics,
        refresh_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.queue = queue
        self.limit = limit
        self._metrics = metrics
        self._refresh_seconds = refresh_seconds
        self._clock = clock
        self._backlog = 0
        self._checked_at = float("-inf")
        self._lock = asyncio.Lock()

    async def current_backlog(self, reader: BacklogReader) -> int:
        if self._clock() - self._checked_at < self._refresh_seconds:
            return self._backlog
        async with self._lock:
            if self._clock() - self._checked_at >= self._refresh_seconds:
                backlog = await reader.backlog(self.queue)
                if backlog is not None:
                    self._backlog = backlog
                    self._metrics.backlog(self.queue, backlog)
                self._checked_at = self._clock()
        return self._backlog

    async def admit(self, reader: BacklogReader) -> None:
        backlog = await self.current_backlog(reader)
        if backlog > self.limit:
            self._metrics.rejected(f"backlog:{self.queue}")
            raise Overloaded(
                f"Queue {self.queue} has {backlog} waiting tasks; try again shortly",
                retry_after_seconds(backlog, self.limit),
            )


class LoadGate:
    def __init__(
        self,
        name: str,
        limit: int,
        read: Callable[[], Awaitable[int]],
        metrics: AdmissionMetrics,
        refresh_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.limit = limit
        self._read = read
        self._metrics = metrics
        self._refresh_seconds = refresh_seconds
        self._clock = clock
        self._value = 0
        self._admitted_since_read = 0
        self._checked_at = float("-inf")

    async def admit(self) -> None:
        if self._clock() - self._checked_at >= self._refresh_seconds:
            self._value = await self._read()
            self._admitted_since_read = 0
            self._checked_at = self._clock()
        load = self._value + self._admitted_since_read
        if load >= self.limit:
            self._metrics.rejected(self.name)
            raise Overloaded(
                f"{load} documents are still being processed; try again shortly",
                retry_after_seconds(load + 1, self.limit),
            )
        self._admitted_since_read += 1


class InflightLimit:
    def __init__(self, limit: int, metrics: AdmissionMetrics) -> None:
        self.limit = limit
        self._metrics = metrics
        self._slots = asyncio.Semaphore(limit)

    async def hold(self) -> AsyncIterator[None]:
        if self._slots.locked():
            self._metrics.rejected("inflight")
            raise Overloaded("Antara is at capacity", INFLIGHT_RETRY_AFTER_SECONDS)
        async with self._slots:
            yield
