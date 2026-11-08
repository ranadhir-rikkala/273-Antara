import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app
from pydantic import BaseModel, Field
from temporalio.client import Client

from api import services
from api.admission import BacklogGate, InflightLimit, LoadGate, Overloaded
from api.auth import current_user
from api.metrics import PrometheusAdmissionMetrics
from api.ops.router import router as ops_router
from api.postgres import PostgresApprovalInbox, PostgresDocumentCatalog
from api.runs import RunNotFoundError, RunUnavailableError, read_run_state
from common import storage
from common.config import settings
from common.db import pool
from common.models import AgentRunRequest, ApprovalDecision, ConversationTurn, UserContext
from common.names import Signals, Workflows
from common.queue_stats import TemporalBacklogReader
from common.queues import AGENT, INGEST_CPU
from common.storage import S3ObjectStore
from common.temporal_client import connect
from reconciler.services import ingest_workflow_id

STATIC_DIR = Path(__file__).parent / "static"


class AppState:
    temporal: Client | None = None


app_state = AppState()
admission_metrics = PrometheusAdmissionMetrics()
inflight = InflightLimit(settings.max_inflight_requests, admission_metrics)
ingest_gate = BacklogGate(INGEST_CPU, settings.max_ingest_backlog, admission_metrics)
agent_gate = BacklogGate(AGENT, settings.max_agent_backlog, admission_metrics)


async def _documents_in_flight() -> int:
    return await PostgresDocumentCatalog(await pool()).count_in_flight()


documents_gate = LoadGate(
    "documents-in-flight", settings.max_documents_in_flight, _documents_in_flight, admission_metrics
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_state.temporal = await connect()
    app.state.temporal = app_state.temporal
    await storage.ensure_bucket()
    yield


app = FastAPI(title="Antara API", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())
app.include_router(ops_router)
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,metrics")


@app.exception_handler(Overloaded)
async def overloaded(_: Request, error: Overloaded) -> JSONResponse:
    return JSONResponse({"detail": error.detail}, status_code=429, headers={"Retry-After": str(error.retry_after)})


class HistoryTurn(BaseModel):
    question: str = Field(max_length=4000)
    answer: str = Field(max_length=4000)


class AskRequest(BaseModel):
    question: str
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)


class ApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""


def temporal() -> Client:
    if app_state.temporal is None:
        raise HTTPException(status_code=503, detail="Temporal is not connected")
    return app_state.temporal


async def catalog() -> PostgresDocumentCatalog:
    return PostgresDocumentCatalog(await pool())


async def inbox() -> PostgresApprovalInbox:
    return PostgresApprovalInbox(await pool())


async def admission() -> AsyncIterator[None]:
    async for _ in inflight.hold():
        yield


async def admit_ingest(client: Client = Depends(temporal)) -> None:
    await ingest_gate.admit(TemporalBacklogReader(client))
    await documents_gate.admit()


async def admit_agent(client: Client = Depends(temporal)) -> None:
    await agent_gate.admit(TemporalBacklogReader(client))


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/ops", include_in_schema=False)
async def ops_console() -> FileResponse:
    return FileResponse(STATIC_DIR / "ops.html")


@app.get("/auth/config")
async def auth_config() -> dict[str, str]:
    return {"mode": settings.auth_mode, "issuer": settings.oidc_issuer, "client_id": settings.oidc_client_id}


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.post("/documents", status_code=202, dependencies=[Depends(admission), Depends(admit_ingest)])
async def upload_document(
    file: UploadFile,
    groups: str = Form("employees"),
    user: UserContext = Depends(current_user),
    client: Client = Depends(temporal),
    documents: PostgresDocumentCatalog = Depends(catalog),
) -> dict[str, str]:
    request = await services.register_upload(
        user,
        file.filename,
        file.content_type,
        await file.read(),
        services.parse_groups(groups),
        S3ObjectStore(),
        documents,
    )
    workflow_id = ingest_workflow_id(request.version_id)
    await client.start_workflow(Workflows.INGEST, request, id=workflow_id, task_queue=INGEST_CPU)
    return {"document_id": request.document_id, "version_id": request.version_id, "workflow_id": workflow_id}


@app.get("/documents")
async def list_documents(
    user: UserContext = Depends(current_user), documents: PostgresDocumentCatalog = Depends(catalog)
) -> list[dict[str, object]]:
    return await documents.list_for_groups(user.groups)


@app.get("/documents/{document_id}")
async def get_document(document_id: str, documents: PostgresDocumentCatalog = Depends(catalog)) -> dict[str, object]:
    document = await documents.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@app.delete("/documents/{document_id}", status_code=202)
async def delete_document(document_id: str, client: Client = Depends(temporal)) -> dict[str, str]:
    workflow_id = f"delete-{document_id}"
    await client.start_workflow(Workflows.DELETE_DOCUMENT, document_id, id=workflow_id, task_queue=INGEST_CPU)
    return {"document_id": document_id, "workflow_id": workflow_id}


@app.post("/agent/runs", status_code=202, dependencies=[Depends(admission), Depends(admit_agent)])
async def start_run(
    body: AskRequest, user: UserContext = Depends(current_user), client: Client = Depends(temporal)
) -> dict[str, str]:
    try:
        question = services.clean_question(body.question)
    except services.InvalidQuestionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    run_id = f"agent-{uuid.uuid4()}"
    await client.start_workflow(
        Workflows.AGENT_RUN,
        AgentRunRequest(
            question=question,
            user=user,
            history=[ConversationTurn(turn.question, turn.answer) for turn in body.history],
        ),
        id=run_id,
        task_queue=AGENT,
    )
    trace_id = trace.get_current_span().get_span_context().trace_id
    return {"run_id": run_id, "trace_id": format(trace_id, "032x") if trace_id else ""}


@app.get("/agent/runs/{run_id}")
async def get_run(run_id: str, client: Client = Depends(temporal)) -> dict[str, object]:
    try:
        state = await read_run_state(client.get_workflow_handle(run_id))
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RunUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error), headers={"Retry-After": "5"}) from error
    return {"run_id": run_id, **asdict(state)}


@app.get("/approvals")
async def list_approvals(
    user: UserContext = Depends(current_user), approvals: PostgresApprovalInbox = Depends(inbox)
) -> list[dict[str, object]]:
    try:
        return await services.approvals_for(user, approvals)
    except services.ForbiddenError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@app.post("/approvals/{approval_id}/decision", status_code=202)
async def decide(
    approval_id: str,
    body: ApprovalRequest,
    user: UserContext = Depends(current_user),
    client: Client = Depends(temporal),
    approvals: PostgresApprovalInbox = Depends(inbox),
) -> dict[str, str]:
    try:
        outcome = await services.decide(approval_id, user, body.approved, approvals)
    except services.NotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except services.ConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except services.ForbiddenError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    decision = ApprovalDecision(approved=body.approved, approver_id=user.user_id, comment=body.comment)
    await client.get_workflow_handle(outcome.run_id).signal(Signals.APPROVAL, decision)
    return {
        "approval_id": approval_id,
        "run_id": outcome.run_id,
        "decision": "approved" if outcome.approved else "denied",
    }
