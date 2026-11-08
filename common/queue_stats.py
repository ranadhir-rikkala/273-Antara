from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client
from temporalio.service import RPCError

from common.models import QueueStats


async def queue_stats(
    client: Client, queue: str, kind: TaskQueueType.ValueType = TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY
) -> QueueStats:
    response = await client.workflow_service.describe_task_queue(
        DescribeTaskQueueRequest(
            namespace=client.namespace,
            task_queue=TaskQueue(name=queue),
            task_queue_type=kind,
            report_stats=True,
        )
    )
    return QueueStats(backlog=response.stats.approximate_backlog_count, pollers=len(response.pollers))


class TemporalQueueInspector:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def stats(self, queue: str) -> QueueStats:
        return await queue_stats(self._client, queue)


class TemporalBacklogReader:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def backlog(self, queue: str) -> int | None:
        try:
            return (await queue_stats(self._client, queue)).backlog
        except RPCError:
            return None
