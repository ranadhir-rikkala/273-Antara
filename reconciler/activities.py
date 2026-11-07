from temporalio import activity

from common.db import pool
from common.models import IngestRequest
from common.names import Activities
from common.temporal_client import shared
from reconciler import services
from reconciler.adapters import PostgresVersionScanner, TemporalWorkflowInspector


@activity.defn(name=Activities.FIND_INCONSISTENT)
async def find_inconsistent_versions() -> list[IngestRequest]:
    return await services.find_inconsistent(
        PostgresVersionScanner(await pool()), TemporalWorkflowInspector(await shared())
    )
