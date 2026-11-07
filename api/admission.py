import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import HTTPException
from prometheus_client import Counter, Gauge
from temporalio.client import Client
from temporalio.service import RPCError

from common.queue_stats import queue_stats

MAX_RETRY_AFTER_SECONDS = 30

REJECTED = Counter("antara_api_rejected_total", "Requests rejected with HTTP 429", ["reason"])
QUEUE_BACKLOG = Gauge("antara_queue_backlog", "Last observed activity backlog per task queue", ["queue"])


def retry_after_seconds(backlog: int, limit: int) -> int:
    return max(1, min(MAX_RETRY_AFTER_SECONDS, (backlog - limit) // 10 + 1))


class BacklogGate:
    def __init__(self, queue: str, limit: int, refresh_seconds: float = 2.0) -> None:
        self.queue = queue
        self.limit = limit
        self.refresh_seconds = refresh_seconds
        self.backlog = 0
        self.rejected = 0
        self._checked_at = float("-inf")
        self._lock = asyncio.Lock()

    async def current_backlog(self, client: Client) -> int:
        if time.monotonic() - self._checked_at < self.refresh_seconds:
            return self.backlog
        async with self._lock:
            if time.monotonic() - self._checked_at >= self.refresh_seconds:
                with contextlib.suppress(RPCError):
                    self.backlog = (await queue_stats(client, self.queue)).backlog
                    QUEUE_BACKLOG.labels(self.queue).set(self.backlog)
                self._checked_at = time.monotonic()
        return self.backlog

    async def admit(self, client: Client) -> None:
        backlog = await self.current_backlog(client)
        if backlog > self.limit:
            self.rejected += 1
            REJECTED.labels(f"backlog:{self.queue}").inc()
            raise HTTPException(
                status_code=429,
                detail=f"Queue {self.queue} has {backlog} waiting tasks; try again shortly",
                headers={"Retry-After": str(retry_after_seconds(backlog, self.limit))},
            )


class LoadGate:
    def __init__(self, name: str, limit: int, read: Callable[[], Awaitable[int]], refresh_seconds: float = 2.0) -> None:
        self.name = name
        self.limit = limit
        self.rejected = 0
        self._read = read
        self._refresh_seconds = refresh_seconds
        self._value = 0
        self._admitted_since_read = 0
        self._checked_at = float("-inf")

    async def admit(self) -> None:
        if time.monotonic() - self._checked_at >= self._refresh_seconds:
            self._value = await self._read()
            self._admitted_since_read = 0
            self._checked_at = time.monotonic()
        load = self._value + self._admitted_since_read
        if load >= self.limit:
            self.rejected += 1
            REJECTED.labels(self.name).inc()
            raise HTTPException(
                status_code=429,
                detail=f"{load} documents are still being processed; try again shortly",
                headers={"Retry-After": str(retry_after_seconds(load + 1, self.limit))},
            )
        self._admitted_since_read += 1


class InflightLimit:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.rejected = 0
        self._slots = asyncio.Semaphore(limit)

    async def hold(self) -> AsyncIterator[None]:
        if self._slots.locked():
            self.rejected += 1
            REJECTED.labels("inflight").inc()
            raise HTTPException(status_code=429, detail="Antara is at capacity", headers={"Retry-After": "5"})
        async with self._slots:
            yield
