import asyncio
import contextlib
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.worker import Worker

from common.config import settings
from common.names import Workflows
from common.queues import RECONCILER
from common.temporal_client import connect
from reconciler.activities import find_inconsistent_versions
from reconciler.workflows import ReconcilerWorkflow

SCHEDULE_ID = "antara-reconciler"
INTERVAL = timedelta(minutes=2)


async def ensure_schedule(client: Client) -> None:
    with contextlib.suppress(ScheduleAlreadyRunningError):
        await client.create_schedule(
            SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(Workflows.RECONCILER, id="reconciler", task_queue=RECONCILER),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=INTERVAL)]),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )


async def main() -> None:
    client = await connect()
    await ensure_schedule(client)
    worker = Worker(
        client,
        task_queue=RECONCILER,
        workflows=[ReconcilerWorkflow],
        activities=[find_inconsistent_versions],
        max_concurrent_activities=settings.max_concurrent_activities,
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
