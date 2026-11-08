import asyncio
import contextlib
import time
from collections.abc import Callable

from gateway.ports import GatewayMetrics, ModelCatalog, Upstream

BUSY_RETRY_AFTER_SECONDS = 2
VERIFY_EVERY_SECONDS = 60
RETRY_FAILED_CHECK_SECONDS = 5


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
        self,
        wait_seconds: float,
        metrics: GatewayMetrics,
        clock: Callable[[], float] = time.monotonic,
        catalog: ModelCatalog | None = None,
        pinned: dict[str, str] | None = None,
    ) -> None:
        self._wait_seconds = wait_seconds
        self._metrics = metrics
        self._clock = clock
        self._failing_until = 0.0
        self._catalog = catalog
        self._pinned = pinned or {}
        self._verified_at = float("-inf")
        self._unverified: list[str] = []
        self._verify_error = ""
        self._verify_lock = asyncio.Lock()

    def verification(self) -> dict[str, object]:
        if not self._pinned:
            return {"verified": None, "unverified": []}
        verified = self._verified_at > float("-inf") and not self._unverified and not self._verify_error
        return {"verified": verified, "unverified": list(self._unverified)}

    async def verify(self) -> dict[str, object]:
        with contextlib.suppress(ModelUnavailable):
            await self._ensure_verified()
        return self.verification()

    async def _ensure_verified(self) -> None:
        if not self._pinned or self._catalog is None:
            return
        async with self._verify_lock:
            wait = RETRY_FAILED_CHECK_SECONDS if self._verify_error else VERIFY_EVERY_SECONDS
            if self._clock() - self._verified_at >= wait:
                try:
                    actual = await self._catalog.digests()
                except Exception as error:
                    self._verify_error = f"cannot verify model digests: {error}"
                else:
                    self._verify_error = ""
                    self._unverified = sorted(
                        name for name, digest in self._pinned.items() if actual.get(name) != digest
                    )
                self._verified_at = self._clock()
        if self._verify_error:
            raise ModelUnavailable(self._verify_error)
        if self._unverified:
            raise ModelUnavailable(f"Model not verified: {', '.join(self._unverified)}")

    def fail_for(self, seconds: float) -> None:
        self._failing_until = self._clock() + seconds

    def failing(self) -> bool:
        return self._clock() < self._failing_until

    async def forward(self, lane: Lane, path: str, body: bytes) -> tuple[int, bytes]:
        if self.failing():
            self._metrics.rejected(lane.kind, "chaos")
            raise ModelUnavailable("Model unavailable (chaos flag)")
        try:
            await self._ensure_verified()
        except ModelUnavailable:
            self._metrics.rejected(lane.kind, "unverified")
            raise
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
