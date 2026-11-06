from common.queue_stats import QueueStats
from common.queues import INGEST_CPU, INGEST_GPU


def choose_embed_queue(gpu: QueueStats, max_gpu_backlog: int) -> tuple[str, str]:
    if gpu.pollers == 0:
        return INGEST_CPU, "no GPU workers are polling"
    if gpu.backlog > max_gpu_backlog:
        return INGEST_CPU, f"GPU backlog {gpu.backlog} is over {max_gpu_backlog}"
    return INGEST_GPU, f"GPU healthy with backlog {gpu.backlog}"
