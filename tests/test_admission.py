import pytest
from fastapi import HTTPException

from api.admission import BacklogGate, retry_after_seconds


def test_retry_after_grows_with_backlog_and_is_capped() -> None:
    assert retry_after_seconds(101, 100) == 1
    assert retry_after_seconds(150, 100) == 6
    assert retry_after_seconds(10_000, 100) == 30


async def test_gate_rejects_with_429_when_backlog_is_over_limit() -> None:
    gate = BacklogGate("ingest-cpu", limit=100, refresh_seconds=60)
    gate.backlog = 150
    gate._checked_at = float("inf")
    with pytest.raises(HTTPException) as error:
        await gate.admit(client=None)
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "6"
    assert gate.rejected == 1


async def test_gate_admits_under_limit() -> None:
    gate = BacklogGate("agent", limit=100, refresh_seconds=60)
    gate.backlog = 99
    gate._checked_at = float("inf")
    await gate.admit(client=None)
    assert gate.rejected == 0


class StatsClient:
    def __init__(self, backlog: int, fail: bool = False) -> None:
        self.backlog = backlog
        self.fail = fail
        self.calls = 0


async def test_gate_refreshes_backlog_and_keeps_last_value_when_temporal_is_unreachable(monkeypatch) -> None:
    from temporalio.service import RPCError, RPCStatusCode

    from api import admission
    from common.queue_stats import QueueStats

    async def fake_stats(client: StatsClient, queue: str) -> QueueStats:
        client.calls += 1
        if client.fail:
            raise RPCError("down", RPCStatusCode.UNAVAILABLE, b"")
        return QueueStats(backlog=client.backlog, pollers=1)

    monkeypatch.setattr(admission, "queue_stats", fake_stats)
    gate = BacklogGate("agent", limit=100, refresh_seconds=0)
    client = StatsClient(backlog=150)
    with pytest.raises(HTTPException):
        await gate.admit(client)
    client.fail = True
    assert await gate.current_backlog(client) == 150
    cached = BacklogGate("agent", limit=100, refresh_seconds=60)
    await cached.current_backlog(client)
    await cached.current_backlog(client)
    assert client.calls == 3


async def test_inflight_limit_rejects_when_every_slot_is_taken() -> None:
    from api.admission import InflightLimit

    limit = InflightLimit(1)
    holder = limit.hold()
    await holder.__anext__()
    with pytest.raises(HTTPException) as error:
        await limit.hold().__anext__()
    assert error.value.status_code == 429 and limit.rejected == 1
    with pytest.raises(StopAsyncIteration):
        await holder.__anext__()


async def test_load_gate_rejects_when_too_much_work_is_in_flight() -> None:
    from api.admission import LoadGate

    in_flight = {"documents": 75}

    async def read() -> int:
        return in_flight["documents"]

    gate = LoadGate("documents-in-flight", limit=60, read=read, refresh_seconds=0)
    with pytest.raises(HTTPException) as error:
        await gate.admit()
    assert error.value.status_code == 429 and "75 documents" in error.value.detail
    in_flight["documents"] = 10
    await gate.admit()
    assert gate.rejected == 1


async def test_load_gate_counts_its_own_admissions_so_a_burst_cannot_slip_through() -> None:
    from api.admission import LoadGate

    async def read() -> int:
        return 0

    gate = LoadGate("documents-in-flight", limit=5, read=read, refresh_seconds=60)
    for _ in range(5):
        await gate.admit()
    with pytest.raises(HTTPException):
        await gate.admit()
    assert gate.rejected == 1
