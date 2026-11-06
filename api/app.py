import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, Form, Header, HTTPException, UploadFile
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


@app.post("/agent/runs/{run_id}/approval", status_code=202)
async def decide(
    run_id: str,
    body: ApprovalRequest,
    user: UserContext = Depends(current_user),
    client: Client = Depends(temporal),
) -> dict[str, str]:
    if "managers" not in user.groups:
        raise HTTPException(status_code=403, detail="Only managers can approve access requests")
    decision = ApprovalDecision(approved=body.approved, approver_id=user.user_id, comment=body.comment)
    await client.get_workflow_handle(run_id).signal(Signals.APPROVAL, decision)
    return {"run_id": run_id, "decision": "approved" if body.approved else "denied"}
