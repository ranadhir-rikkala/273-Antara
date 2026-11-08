import asyncio

import pytest

from gateway.services import Gateway, Lane, ModelBusy, ModelUnavailable


class Metrics:
    def __init__(self) -> None:
        self.events: list[tuple[str, ...]] = []

    def rejected(self, kind: str, reason: str) -> None:
        self.events.append(("rejected", kind, reason))

    def started(self, kind: str) -> None:
        self.events.append(("started", kind))

    def finished(self, kind: str) -> None:
        self.events.append(("finished", kind))


class Upstream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.release = asyncio.Event()
        self.release.set()

    async def post(self, path: str, body: bytes) -> tuple[int, bytes]:
        self.calls.append((path, body))
        await self.release.wait()
        return 200, b'{"ok": true}'


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_forward_returns_the_upstream_reply_and_frees_the_slot() -> None:
    metrics, upstream = Metrics(), Upstream()
    gateway, lane = Gateway(wait_seconds=1, metrics=metrics), Lane("chat", 1, upstream)
    assert await gateway.forward(lane, "/chat/completions", b"{}") == (200, b'{"ok": true}')
    assert await gateway.forward(lane, "/chat/completions", b"{}") == (200, b'{"ok": true}')
    assert upstream.calls == [("/chat/completions", b"{}")] * 2
    assert metrics.events == [("started", "chat"), ("finished", "chat")] * 2


async def test_model_is_busy_when_no_slot_frees_up_in_time() -> None:
    metrics, upstream = Metrics(), Upstream()
    gateway, lane = Gateway(wait_seconds=0.01, metrics=metrics), Lane("embed", 1, upstream)
    upstream.release.clear()
    first = asyncio.create_task(gateway.forward(lane, "/embeddings", b"{}"))
    await asyncio.sleep(0)
    with pytest.raises(ModelBusy):
        await gateway.forward(lane, "/embeddings", b"{}")
    upstream.release.set()
    assert await first == (200, b'{"ok": true}')
    assert ("rejected", "embed", "busy") in metrics.events
    assert len(upstream.calls) == 1


async def test_chaos_flag_fails_every_call_until_it_expires() -> None:
    clock, metrics, upstream = Clock(), Metrics(), Upstream()
    gateway, lane = Gateway(wait_seconds=1, metrics=metrics, clock=clock), Lane("chat", 1, upstream)
    assert not gateway.failing()
    gateway.fail_for(30)
    assert gateway.failing()
    with pytest.raises(ModelUnavailable):
        await gateway.forward(lane, "/chat/completions", b"{}")
    assert metrics.events == [("rejected", "chat", "chaos")] and upstream.calls == []
    clock.now = 31
    assert not gateway.failing()
    assert await gateway.forward(lane, "/chat/completions", b"{}") == (200, b'{"ok": true}')
