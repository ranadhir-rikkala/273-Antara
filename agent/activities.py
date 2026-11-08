from psycopg_pool import AsyncConnectionPool
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent import services
from agent.postgres import (
    PostgresAnswerCache,
    PostgresApprovalStore,
    PostgresAuditLog,
    PostgresIncidentUnitOfWork,
    PostgresToolUnitOfWork,
)
from common.config import settings
from common.db import pool
from common.llm import model_session
from common.models import (
    ApprovalOpenRequest,
    CachedAnswer,
    CacheLookupRequest,
    CacheStoreRequest,
    ChunkResult,
    GenerateRequest,
    IncidentReport,
    IncidentResult,
    ModelTurn,
    RetrieveRequest,
    ToolRequest,
    ToolResult,
)
from common.names import Activities, Errors
from ingest.retrieval import search_index


@activity.defn(name=Activities.CLASSIFY)
async def classify_request(question: str) -> str:
    async with model_session() as model:
        return await services.classify(question, model)


@activity.defn(name=Activities.RETRIEVE)
async def retrieve_documents(request: RetrieveRequest) -> list[ChunkResult]:
    db = await pool()
    async with model_session() as model:
        return await services.retrieve(request, search_index(db, model), PostgresAuditLog(db))


@activity.defn(name=Activities.GENERATE)
async def generate_answer(request: GenerateRequest) -> ModelTurn:
    async with model_session() as model:
        return await services.generate(request, model, settings.answer_min_score)


@activity.defn(name=Activities.EXECUTE_TOOL)
async def execute_tool(request: ToolRequest) -> ToolResult:
    try:
        return await services.execute_tool(request, tool_unit_of_work(await pool()))
    except services.UnknownToolError as error:
        raise ApplicationError(str(error), type=Errors.UNKNOWN_TOOL, non_retryable=True) from error
    except services.PermissionDeniedError as error:
        raise ApplicationError(str(error), type=Errors.PERMISSION_DENIED, non_retryable=True) from error


@activity.defn(name=Activities.REVOKE_ACCESS)
async def revoke_access(grant_id: str) -> bool:
    return await services.revoke_access(grant_id, tool_unit_of_work(await pool()))


def tool_unit_of_work(db: AsyncConnectionPool) -> PostgresToolUnitOfWork:
    return PostgresToolUnitOfWork(db, {"read": settings.access_read_seconds, "write": settings.access_write_seconds})


@activity.defn(name=Activities.OPEN_APPROVAL)
async def open_approval(request: ApprovalOpenRequest) -> str:
    db = await pool()
    return await services.open_approval(request, PostgresApprovalStore(db), PostgresAuditLog(db))


@activity.defn(name=Activities.ESCALATE_APPROVAL)
async def escalate_approval(approval_id: str) -> None:
    db = await pool()
    await services.escalate_approval(approval_id, PostgresApprovalStore(db), PostgresAuditLog(db))


@activity.defn(name=Activities.EXPIRE_APPROVAL)
async def expire_approval(approval_id: str) -> None:
    db = await pool()
    await services.expire_approval(approval_id, PostgresApprovalStore(db), PostgresAuditLog(db))


@activity.defn(name=Activities.REPORT_INCIDENT)
async def report_incident(report: IncidentReport) -> IncidentResult:
    async with model_session() as model:
        return await services.report_incident(
            report,
            model,
            PostgresIncidentUnitOfWork(await pool()),
            settings.incident_window_minutes,
            settings.incident_similarity,
        )


@activity.defn(name=Activities.CACHE_LOOKUP)
async def lookup_cached_answer(request: CacheLookupRequest) -> CachedAnswer | None:
    async with model_session() as model:
        return await services.lookup_cached_answer(
            request, model, PostgresAnswerCache(await pool()), settings.cache_ttl_hours, settings.cache_similarity
        )


@activity.defn(name=Activities.CACHE_STORE)
async def store_cached_answer(request: CacheStoreRequest) -> None:
    async with model_session() as model:
        await services.store_cached_answer(request, model, PostgresAnswerCache(await pool()))
