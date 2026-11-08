import asyncio
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
    ScheduleUpdate,
)
from temporalio.worker import Worker

from common.config import settings
from common.names import Workflows
from common.queues import RECONCILER
from common.temporal_client import connect
from common.worker_runtime import run_until_stopped
from reconciler.activities import find_inconsistent_versions
from reconciler.workflows import ReconcilerWorkflow

SCHEDULE_ID = "antara-reconciler"


def reconciler_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(Workflows.RECONCILER, id="reconciler", task_queue=RECONCILER),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=settings.reconcile_every_seconds))]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_schedule(client: Client) -> None:
    try:
        await client.create_schedule(SCHEDULE_ID, reconciler_schedule())
    except ScheduleAlreadyRunningError:
        await client.get_schedule_handle(SCHEDULE_ID).update(lambda _: ScheduleUpdate(schedule=reconciler_schedule()))


async def main() -> None:
    client = await connect()
    await ensure_schedule(client)
    worker = Worker(
        client,
        task_queue=RECONCILER,
        workflows=[ReconcilerWorkflow],
        activities=[find_inconsistent_versions],
        max_concurrent_activities=settings.max_concurrent_activities,
        graceful_shutdown_timeout=timedelta(seconds=25),
    )
    await run_until_stopped(worker)


if __name__ == "__main__":
    asyncio.run(main())
