import asyncio
import time
from collections.abc import Callable

from gateway.ports import GatewayMetrics, Upstream

BUSY_RETRY_AFTER_SECONDS = 2


class ModelUnavailable(Exception):
    pass


class ModelBusy(Exception):
    pass


class Lane:
    def __init__(self, kind: str, max_inflight: int, upstream: Upstream) -> None:
        self.kind = kind
        self.upstream = upstream
        self.slots = asyncio.Semaphore(max_inflight)


class Gateway:
    def __init__(
        self, wait_seconds: float, metrics: GatewayMetrics, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._wait_seconds = wait_seconds
        self._metrics = metrics
        self._clock = clock
        self._failing_until = 0.0

    def fail_for(self, seconds: float) -> None:
        self._failing_until = self._clock() + seconds

    def failing(self) -> bool:
        return self._clock() < self._failing_until

    async def forward(self, lane: Lane, path: str, body: bytes) -> tuple[int, bytes]:
        if self.failing():
            self._metrics.rejected(lane.kind, "chaos")
            raise ModelUnavailable("Model unavailable (chaos flag)")
        try:
            await asyncio.wait_for(lane.slots.acquire(), timeout=self._wait_seconds)
        except TimeoutError:
            self._metrics.rejected(lane.kind, "busy")
            raise ModelBusy("Model busy") from None
        self._metrics.started(lane.kind)
        try:
            return await lane.upstream.post(path, body)
        finally:
            self._metrics.finished(lane.kind)
            lane.slots.release()
