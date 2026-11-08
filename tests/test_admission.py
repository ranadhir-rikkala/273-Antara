import pytest

from api.admission import BacklogGate, InflightLimit, LoadGate, Overloaded, retry_after_seconds


class Metrics:
    def __init__(self) -> None:
        self.rejections: list[str] = []
        self.backlogs: dict[str, int] = {}

    def rejected(self, reason: str) -> None:
        self.rejections.append(reason)

    def backlog(self, queue: str, value: int) -> None:
        self.backlogs[queue] = value


class Reader:
    def __init__(self, value: int | None) -> None:
        self.value = value
        self.calls = 0

    async def backlog(self, queue: str) -> int | None:
        self.calls += 1
        return self.value


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_retry_after_grows_with_backlog_and_is_capped() -> None:
    assert retry_after_seconds(101, 100) == 1
    assert retry_after_seconds(150, 100) == 6
    assert retry_after_seconds(10_000, 100) == 30


async def test_backlog_gate_rejects_when_the_queue_is_over_its_limit() -> None:
    metrics = Metrics()
    gate = BacklogGate("ingest-cpu", limit=100, metrics=metrics)
    with pytest.raises(Overloaded) as error:
        await gate.admit(Reader(150))
    assert error.value.retry_after == 6
    assert "ingest-cpu has 150 waiting tasks" in error.value.detail
    assert metrics.rejections == ["backlog:ingest-cpu"]
    assert metrics.backlogs == {"ingest-cpu": 150}


async def test_backlog_gate_admits_under_its_limit() -> None:
    metrics = Metrics()
    await BacklogGate("agent", limit=100, metrics=metrics).admit(Reader(99))
    assert metrics.rejections == []


async def test_backlog_gate_rereads_only_after_the_refresh_window_and_keeps_the_last_value_when_unreadable() -> None:
    clock, reader = Clock(), Reader(150)
    gate = BacklogGate("agent", limit=100, metrics=Metrics(), refresh_seconds=2, clock=clock)
    assert await gate.current_backlog(reader) == 150
    reader.value = 10
    assert await gate.current_backlog(reader) == 150
    assert reader.calls == 1
    clock.now, reader.value = 3, None
    assert await gate.current_backlog(reader) == 150
    assert reader.calls == 2
    clock.now, reader.value = 6, 10
    assert await gate.current_backlog(reader) == 10


async def test_inflight_limit_rejects_when_every_slot_is_taken() -> None:
    metrics = Metrics()
    limit = InflightLimit(1, metrics)
    holder = limit.hold()
    await holder.__anext__()
    with pytest.raises(Overloaded) as error:
        await limit.hold().__anext__()
    assert error.value.retry_after == 5 and error.value.detail == "Antara is at capacity"
    assert metrics.rejections == ["inflight"]
    with pytest.raises(StopAsyncIteration):
        await holder.__anext__()


async def test_load_gate_rejects_when_too_much_work_is_in_flight() -> None:
    in_flight = {"documents": 75}

    async def read() -> int:
        return in_flight["documents"]

    metrics = Metrics()
    gate = LoadGate("documents-in-flight", limit=60, read=read, metrics=metrics, refresh_seconds=0)
    with pytest.raises(Overloaded) as error:
        await gate.admit()
    assert "75 documents" in error.value.detail
    in_flight["documents"] = 10
    await gate.admit()
    assert metrics.rejections == ["documents-in-flight"]


async def test_load_gate_counts_its_own_admissions_so_a_burst_cannot_slip_through() -> None:
    async def read() -> int:
        return 0

    metrics = Metrics()
    gate = LoadGate("documents-in-flight", limit=5, read=read, metrics=metrics, refresh_seconds=60)
    for _ in range(5):
        await gate.admit()
    with pytest.raises(Overloaded):
        await gate.admit()
    assert metrics.rejections == ["documents-in-flight"]
