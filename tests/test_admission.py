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
