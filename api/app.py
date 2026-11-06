import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app
from pydantic import BaseModel
from temporalio.client import Client

from api.admission import BacklogGate, InflightLimit
from common import storage
from common.config import settings
from common.db import pool
from common.models import AgentRunRequest, AgentRunState, ApprovalDecision, IngestRequest, UserContext, VersionStatus
from common.names import Queries, Signals, Workflows
from common.queues import AGENT, INGEST_CPU
from common.temporal_client import connect


class AppState:
    temporal: Client | None = None


app_state = AppState()
inflight = InflightLimit(settings.max_inflight_requests)
ingest_gate = BacklogGate(INGEST_CPU, settings.max_ingest_backlog)
agent_gate = BacklogGate(AGENT, settings.max_agent_backlog)


@asynccontextmanager
async def lifespan(_: FastAPI):
    app_state.temporal = await connect()
    await storage.ensure_bucket()
    yield


app = FastAPI(title="Antara API", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,metrics")


class AskRequest(BaseModel):
    question: str


class ApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""


def temporal() -> Client:
    if app_state.temporal is None:
        raise HTTPException(status_code=503, detail="Temporal is not connected")
    return app_state.temporal


def current_user(x_user_id: str = Header(...), x_user_groups: str = Header("")) -> UserContext:
    return UserContext(user_id=x_user_id, groups=[g.strip() for g in x_user_groups.split(",") if g.strip()])


async def admission() -> AsyncIterator[None]:
    async for _ in inflight.hold():
        yield


async def admit_ingest(client: Client = Depends(temporal)) -> None:
    await ingest_gate.admit(client)


async def admit_agent(client: Client = Depends(temporal)) -> None:
    await agent_gate.admit(client)


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.post("/documents", status_code=202, dependencies=[Depends(admission), Depends(admit_ingest)])
async def upload_document(
    file: UploadFile,
    groups: str = Form("employees"),
    user: UserContext = Depends(current_user),
    client: Client = Depends(temporal),
) -> dict[str, str]:
    data = await file.read()
    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    filename = file.filename or "document"
    content_type = file.content_type or "application/octet-stream"
    object_key = f"originals/{document_id}/{version_id}/{filename}"
    await storage.put_bytes(object_key, data, content_type)
    acl = [g.strip() for g in groups.split(",") if g.strip()]
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO documents (document_id, owner_id, title) VALUES (%s, %s, %s)",
            (document_id, user.user_id, filename),
        )
        await conn.execute(
            "INSERT INTO document_versions (version_id, document_id, version_number, status, object_key, content_type) "
            "VALUES (%s, %s, 1, %s, %s, %s)",
            (version_id, document_id, VersionStatus.UPLOADED, object_key, content_type),
        )
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO document_acl (document_id, group_name) VALUES (%s, %s)",
                [(document_id, group) for group in acl],
            )
    workflow_id = f"ingest-{version_id}"
    await client.start_workflow(
        Workflows.INGEST,
        IngestRequest(document_id=document_id, version_id=version_id, object_key=object_key, content_type=content_type),
        id=workflow_id,
        task_queue=INGEST_CPU,
    )
    return {"document_id": document_id, "version_id": version_id, "workflow_id": workflow_id}


@app.get("/documents")
async def list_documents(user: UserContext = Depends(current_user)) -> list[dict[str, object]]:
    db = await pool()
    async with db.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT d.document_id::text, d.title, v.status, v.version_number, d.created_at, "
                "array(SELECT group_name FROM document_acl a WHERE a.document_id = d.document_id ORDER BY 1) "
                "FROM documents d "
                "JOIN LATERAL (SELECT status, version_number FROM document_versions "
                "WHERE document_id = d.document_id ORDER BY version_number DESC LIMIT 1) v ON true "
                "WHERE d.deleted_at IS NULL AND EXISTS ("
                "SELECT 1 FROM document_acl a WHERE a.document_id = d.document_id AND a.group_name = ANY(%s)) "
                "ORDER BY d.created_at DESC LIMIT 200",
                (user.groups,),
            )
        ).fetchall()
    return [
        {
            "document_id": row[0],
            "title": row[1],
            "status": row[2],
            "version_number": row[3],
            "created_at": row[4].isoformat(),
            "groups": row[5],
        }
        for row in rows
    ]


@app.get("/documents/{document_id}")
async def get_document(document_id: str) -> dict[str, object]:
    db = await pool()
    async with db.connection() as conn:
        document = await (
            await conn.execute(
                "SELECT title, active_version_id::text, deleted_at IS NOT NULL FROM documents WHERE document_id = %s",
                (document_id,),
            )
        ).fetchone()
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        versions = await (
            await conn.execute(
                "SELECT version_id::text, version_number, status, error FROM document_versions "
                "WHERE document_id = %s ORDER BY version_number",
                (document_id,),
            )
        ).fetchall()
    return {
        "document_id": document_id,
        "title": document[0],
        "active_version_id": document[1],
        "deleted": document[2],
        "versions": [{"version_id": v[0], "version_number": v[1], "status": v[2], "error": v[3]} for v in versions],
    }


@app.delete("/documents/{document_id}", status_code=202)
async def delete_document(document_id: str, client: Client = Depends(temporal)) -> dict[str, str]:
    workflow_id = f"delete-{document_id}"
    await client.start_workflow(Workflows.DELETE_DOCUMENT, document_id, id=workflow_id, task_queue=INGEST_CPU)
    return {"document_id": document_id, "workflow_id": workflow_id}


@app.post("/agent/runs", status_code=202, dependencies=[Depends(admission), Depends(admit_agent)])
async def start_run(
    body: AskRequest, user: UserContext = Depends(current_user), client: Client = Depends(temporal)
) -> dict[str, str]:
    run_id = f"agent-{uuid.uuid4()}"
    await client.start_workflow(
        Workflows.AGENT_RUN, AgentRunRequest(question=body.question, user=user), id=run_id, task_queue=AGENT
    )
    return {"run_id": run_id}


@app.get("/agent/runs/{run_id}")
async def get_run(run_id: str, client: Client = Depends(temporal)) -> dict[str, object]:
    handle = client.get_workflow_handle(run_id)
    state = await handle.query(Queries.STATE, result_type=AgentRunState)
    return {"run_id": run_id, **asdict(state)}


APPROVER_GROUP = "managers"
ESCALATION_GROUP = "security-leads"


@app.get("/approvals")
async def list_approvals(user: UserContext = Depends(current_user)) -> list[dict[str, object]]:
    if APPROVER_GROUP not in user.groups and ESCALATION_GROUP not in user.groups:
        raise HTTPException(status_code=403, detail="Only approvers can see the approvals inbox")
    escalated_only = APPROVER_GROUP not in user.groups
    db = await pool()
    async with db.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT approval_id::text, run_id, requester_id, system, role, reason, escalated, created_at "
                "FROM approvals WHERE status = 'PENDING' AND (escalated OR NOT %s) ORDER BY created_at",
                (escalated_only,),
            )
        ).fetchall()
    return [
        {
            "approval_id": row[0],
            "run_id": row[1],
            "requester_id": row[2],
            "system": row[3],
            "role": row[4],
            "reason": row[5],
            "escalated": row[6],
            "created_at": row[7].isoformat(),
        }
        for row in rows
    ]


@app.post("/approvals/{approval_id}/decision", status_code=202)
async def decide(
    approval_id: str,
    body: ApprovalRequest,
    user: UserContext = Depends(current_user),
    client: Client = Depends(temporal),
) -> dict[str, str]:
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        row = await (
            await conn.execute(
                "SELECT run_id, status, escalated FROM approvals WHERE approval_id = %s FOR UPDATE", (approval_id,)
            )
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        run_id, status, escalated = row
        if status != "PENDING":
            raise HTTPException(status_code=409, detail=f"Approval is already {status}")
        if APPROVER_GROUP not in user.groups and not (escalated and ESCALATION_GROUP in user.groups):
            raise HTTPException(status_code=403, detail="You cannot decide this approval")
        await conn.execute(
            "UPDATE approvals SET status = %s, decided_by = %s, decided_at = now() WHERE approval_id = %s",
            ("APPROVED" if body.approved else "DENIED", user.user_id, approval_id),
        )
    decision = ApprovalDecision(approved=body.approved, approver_id=user.user_id, comment=body.comment)
    await client.get_workflow_handle(run_id).signal(Signals.APPROVAL, decision)
    return {"approval_id": approval_id, "run_id": run_id, "decision": "approved" if body.approved else "denied"}
