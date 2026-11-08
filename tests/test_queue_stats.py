from temporalio.service import RPCError, RPCStatusCode

from common import queue_stats
from common.models import QueueStats
from common.queue_stats import TemporalBacklogReader


async def test_backlog_reader_returns_the_backlog_and_none_when_temporal_is_unreachable(monkeypatch) -> None:
    answers: list[QueueStats | Exception] = [
        QueueStats(backlog=7, pollers=1),
        RPCError("down", RPCStatusCode.UNAVAILABLE, b""),
    ]

    async def fake_stats(client: object, queue: str) -> QueueStats:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(queue_stats, "queue_stats", fake_stats)
    reader = TemporalBacklogReader(client=None)
    assert await reader.backlog("agent") == 7
    assert await reader.backlog("agent") is None
