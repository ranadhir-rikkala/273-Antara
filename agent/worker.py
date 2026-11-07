import asyncio

from temporalio.worker import Worker

from agent.activities import (
    classify_request,
    escalate_approval,
    execute_tool,
    generate_answer,
    lookup_cached_answer,
    open_approval,
    report_incident,
    retrieve_documents,
    store_cached_answer,
)
from agent.workflows import AgentRunWorkflow, IncidentReportWorkflow
from common.config import settings
from common.queues import AGENT, TOOLS
from common.temporal_client import connect


async def main() -> None:
    client = await connect()
    if settings.worker_queue == TOOLS:
        worker = Worker(
            client,
            task_queue=TOOLS,
            workflows=[IncidentReportWorkflow],
            activities=[execute_tool, open_approval, escalate_approval, report_incident],
            max_concurrent_activities=settings.max_concurrent_activities,
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
        )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
