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


class Catalog:
    def __init__(self, digests: dict[str, str] | None = None, fail: bool = False) -> None:
        self.digests_now = digests or {}
        self.fail = fail
        self.calls = 0

    async def digests(self) -> dict[str, str]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("model server unreachable")
        return self.digests_now


PINNED = {"qwen2.5:1.5b": "aaa", "nomic-embed-text:latest": "bbb"}


async def test_models_must_match_their_pinned_digests_before_any_call_is_served() -> None:
    clock, catalog = Clock(), Catalog({"qwen2.5:1.5b": "aaa", "nomic-embed-text:latest": "bbb"})
    gateway = Gateway(wait_seconds=1, metrics=Metrics(), clock=clock, catalog=catalog, pinned=PINNED)
    assert await gateway.forward(Lane("chat", 1, Upstream()), "/chat", b"{}") == (200, b'{"ok": true}')
    assert gateway.verification() == {"verified": True, "unverified": []}


async def test_a_swapped_or_missing_model_is_refused_until_it_matches_again() -> None:
    clock, catalog = Clock(), Catalog({"qwen2.5:1.5b": "evil"})
    upstream = Upstream()
    gateway = Gateway(wait_seconds=1, metrics=Metrics(), clock=clock, catalog=catalog, pinned=PINNED)
    with pytest.raises(ModelUnavailable, match="nomic-embed-text:latest, qwen2.5:1.5b"):
        await gateway.forward(Lane("chat", 1, upstream), "/chat", b"{}")
    assert upstream.calls == [] and gateway.verification()["verified"] is False
    catalog.digests_now = {"qwen2.5:1.5b": "aaa", "nomic-embed-text:latest": "bbb"}
    clock.now = 30
    with pytest.raises(ModelUnavailable):
        await gateway.forward(Lane("chat", 1, upstream), "/chat", b"{}")
    clock.now = 61
    assert await gateway.forward(Lane("chat", 1, upstream), "/chat", b"{}") == (200, b'{"ok": true}')
    assert catalog.calls == 2


async def test_an_unreachable_model_catalog_counts_as_unverified() -> None:
    gateway = Gateway(wait_seconds=1, metrics=Metrics(), clock=Clock(), catalog=Catalog(fail=True), pinned=PINNED)
    with pytest.raises(ModelUnavailable, match="cannot verify"):
        await gateway.forward(Lane("embed", 1, Upstream()), "/embeddings", b"{}")


async def test_without_pinned_digests_nothing_is_checked() -> None:
    catalog = Catalog(fail=True)
    gateway = Gateway(wait_seconds=1, metrics=Metrics(), catalog=catalog)
    assert await gateway.forward(Lane("chat", 1, Upstream()), "/chat", b"{}") == (200, b'{"ok": true}')
    assert catalog.calls == 0 and gateway.verification() == {"verified": None, "unverified": []}


async def test_calls_arriving_together_all_wait_for_the_first_verification() -> None:
    class SlowCatalog(Catalog):
        async def digests(self) -> dict[str, str]:
            await asyncio.sleep(0.05)
            return await super().digests()

    catalog, upstream = SlowCatalog({"qwen2.5:1.5b": "evil"}), Upstream()
    gateway = Gateway(wait_seconds=1, metrics=Metrics(), clock=Clock(), catalog=catalog, pinned=PINNED)
    lane = Lane("chat", 4, upstream)
    outcomes = await asyncio.gather(*(gateway.forward(lane, "/chat", b"{}") for _ in range(3)), return_exceptions=True)
    assert all(isinstance(outcome, ModelUnavailable) for outcome in outcomes)
    assert upstream.calls == [] and catalog.calls == 1


async def test_a_health_check_verifies_models_without_needing_a_model_call() -> None:
    good = Gateway(wait_seconds=1, metrics=Metrics(), clock=Clock(), catalog=Catalog(dict(PINNED)), pinned=PINNED)
    assert await good.verify() == {"verified": True, "unverified": []}
    bad = Gateway(wait_seconds=1, metrics=Metrics(), clock=Clock(), catalog=Catalog({}), pinned=PINNED)
    assert await bad.verify() == {"verified": False, "unverified": ["nomic-embed-text:latest", "qwen2.5:1.5b"]}


async def test_a_failed_check_is_retried_within_seconds_while_a_good_one_lasts_a_minute() -> None:
    clock, catalog = Clock(), Catalog(dict(PINNED), fail=True)
    gateway = Gateway(wait_seconds=1, metrics=Metrics(), clock=clock, catalog=catalog, pinned=PINNED)
    assert (await gateway.verify())["verified"] is False
    catalog.fail = False
    clock.now = 6
    assert (await gateway.verify())["verified"] is True
    clock.now = 30
    await gateway.verify()
    assert catalog.calls == 2
