from datetime import timedelta

from temporalio import workflow
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from common.models import IngestRequest, ReconcileReport
    from common.names import Activities, Workflows
    from common.queues import INGEST_CPU, RECONCILER
    from common.retry import DEFAULT_RETRY
    from reconciler.services import ingest_workflow_id


@workflow.defn(name=Workflows.RECONCILER)
class ReconcilerWorkflow:
    @workflow.run
    async def run(self) -> ReconcileReport:
        stuck = await workflow.execute_activity(
            Activities.FIND_INCONSISTENT,
            result_type=list[IngestRequest],
            task_queue=RECONCILER,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        report = ReconcileReport()
        for request in stuck:
            await workflow.start_child_workflow(
                Workflows.INGEST,
                request,
                id=ingest_workflow_id(request.version_id),
                task_queue=INGEST_CPU,
                parent_close_policy=ParentClosePolicy.ABANDON,
            )
            report.redriven.append(request.version_id)
        return report
