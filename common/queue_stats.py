from dataclasses import dataclass

from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client


@dataclass(frozen=True)
class QueueStats:
    backlog: int
    pollers: int


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
