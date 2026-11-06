from common.queue_stats import QueueStats
from common.queues import INGEST_CPU, INGEST_GPU
from ingest.dispatch import choose_embed_queue


def test_healthy_gpu_gets_the_work() -> None:
    queue, _ = choose_embed_queue(QueueStats(backlog=10, pollers=1), max_gpu_backlog=64)
    assert queue == INGEST_GPU


def test_overloaded_gpu_offloads_to_cpu() -> None:
    queue, reason = choose_embed_queue(QueueStats(backlog=200, pollers=1), max_gpu_backlog=64)
    assert queue == INGEST_CPU
    assert "200" in reason


def test_missing_gpu_workers_fall_back_to_cpu() -> None:
    queue, reason = choose_embed_queue(QueueStats(backlog=0, pollers=0), max_gpu_backlog=64)
    assert queue == INGEST_CPU
    assert "no GPU workers" in reason
