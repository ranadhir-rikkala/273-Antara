import asyncio
from datetime import timedelta

from temporalio.worker import Worker

from agent.activities import (
    classify_request,
    escalate_approval,
    execute_tool,
    expire_approval,
    generate_answer,
    lookup_cached_answer,
    open_approval,
    report_incident,
    retrieve_documents,
    revoke_access,
    store_cached_answer,
)
from agent.workflows import AccessLeaseWorkflow, AgentRunWorkflow, IncidentReportWorkflow
from common.config import settings
from common.queues import AGENT, TOOLS
from common.temporal_client import connect
from common.worker_runtime import run_until_stopped


async def main() -> None:
    client = await connect()
    if settings.worker_queue == TOOLS:
        worker = Worker(
            client,
            task_queue=TOOLS,
            workflows=[IncidentReportWorkflow, AccessLeaseWorkflow],
            activities=[
                revoke_access,
                execute_tool,
                open_approval,
                escalate_approval,
                expire_approval,
                report_incident,
            ],
            max_concurrent_activities=settings.max_concurrent_activities,
            graceful_shutdown_timeout=timedelta(seconds=25),
        )
    else:
        worker = Worker(
            client,
            task_queue=AGENT,
            workflows=[AgentRunWorkflow],
            activities=[
                classify_request,
                lookup_cached_answer,
                retrieve_documents,
                generate_answer,
                store_cached_answer,
            ],
            max_concurrent_activities=settings.max_concurrent_activities,
            graceful_shutdown_timeout=timedelta(seconds=25),
        )
    await run_until_stopped(worker)


if __name__ == "__main__":
    asyncio.run(main())
