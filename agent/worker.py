import asyncio

from temporalio.worker import Worker

from agent.activities import execute_tool, generate_answer, retrieve_documents
from agent.workflows import AgentRunWorkflow
from common.config import settings
from common.queues import AGENT, TOOLS
from common.temporal_client import connect


async def main() -> None:
    client = await connect()
    if settings.worker_queue == TOOLS:
        worker = Worker(
            client,
            task_queue=TOOLS,
            activities=[execute_tool],
            max_concurrent_activities=settings.max_concurrent_activities,
        )
    else:
        worker = Worker(
            client,
            task_queue=AGENT,
            workflows=[AgentRunWorkflow],
            activities=[retrieve_documents, generate_answer],
            max_concurrent_activities=settings.max_concurrent_activities,
        )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
