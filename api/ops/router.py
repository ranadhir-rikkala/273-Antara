import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from temporalio.client import Client

from api.auth import current_user
from api.ops import services
from api.ops.adapters import (
    ClusterUnavailableError,
    GatewayModelControl,
    KubernetesCluster,
    PostgresOpsStore,
    PrometheusMetrics,
    SocketEgressProbe,
    TemporalReconcilerTrigger,
    TemporalWorkflows,
)
from api.ops.flood import run_flood
from common.config import settings
from common.db import pool
from common.models import UserContext
from common.queue_stats import TemporalQueueInspector

router = APIRouter(prefix="/ops")
background: set[asyncio.Task] = set()


class KillRequest(BaseModel):
    target: str


class OutageRequest(BaseModel):
    seconds: float


class GpuLaneRequest(BaseModel):
    down: bool


class FloodRequest(BaseModel):
    documents: int = 200
    questions: int = 50


def operator(user: UserContext = Depends(current_user)) -> UserContext:
    if not services.can_operate(user):
        raise HTTPException(status_code=403, detail="Only operators can use the operations console")
    return user


def chaos_operator(user: UserContext = Depends(operator)) -> UserContext:
    if not settings.chaos_enabled:
        raise HTTPException(status_code=403, detail="Chaos controls are disabled in this environment")
    return user


def temporal(request: Request) -> Client:
    client = getattr(request.app.state, "temporal", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Temporal is not connected")
    return client


async def store() -> PostgresOpsStore:
    return PostgresOpsStore(await pool())


def cluster() -> KubernetesCluster:
    return KubernetesCluster(settings.k8s_namespace)


async def _act(call: Any) -> Any:
    try:
        return await call
    except services.ChaosError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except services.NothingToBreakError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ClusterUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/state", dependencies=[Depends(operator)])
async def state(client: Client = Depends(temporal), ops: PostgresOpsStore = Depends(store)) -> dict[str, Any]:
    snapshot = await services.snapshot(
        TemporalQueueInspector(client),
        TemporalWorkflows(client),
        cluster(),
        GatewayModelControl(settings.gateway_url),
        PrometheusMetrics(settings.prometheus_url),
        ops,
    )
    return {**snapshot, "chaos_enabled": settings.chaos_enabled}


@router.post("/chaos/kill-worker", status_code=202)
async def kill_worker(
    body: KillRequest, user: UserContext = Depends(chaos_operator), ops: PostgresOpsStore = Depends(store)
) -> dict[str, str]:
    pod = await _act(services.kill_worker(body.target, cluster(), ops, user.user_id))
    return {"killed": pod}


@router.post("/chaos/model-outage", status_code=202)
async def model_outage(
    body: OutageRequest, user: UserContext = Depends(chaos_operator), ops: PostgresOpsStore = Depends(store)
) -> dict[str, float]:
    await _act(services.model_outage(body.seconds, GatewayModelControl(settings.gateway_url), ops, user.user_id))
    return {"failing_for_seconds": body.seconds}


@router.post("/chaos/agent-workers", status_code=202)
async def agent_workers(
    body: GpuLaneRequest, user: UserContext = Depends(chaos_operator), ops: PostgresOpsStore = Depends(store)
) -> dict[str, bool]:
    await _act(services.agent_workers(body.down, cluster(), ops, user.user_id))
    return {"agent_workers_stopped": body.down}


@router.post("/chaos/gpu-lane", status_code=202)
async def gpu_lane(
    body: GpuLaneRequest, user: UserContext = Depends(chaos_operator), ops: PostgresOpsStore = Depends(store)
) -> dict[str, bool]:
    await _act(services.gpu_lane(body.down, cluster(), ops, user.user_id))
    return {"gpu_lane_down": body.down}


@router.post("/chaos/corrupt-index", status_code=202)
async def corrupt_index(
    user: UserContext = Depends(chaos_operator), ops: PostgresOpsStore = Depends(store)
) -> dict[str, Any]:
    return await _act(services.corrupt_index(ops, user.user_id))


@router.post("/chaos/flood", status_code=202)
async def flood(
    body: FloodRequest, user: UserContext = Depends(chaos_operator), ops: PostgresOpsStore = Depends(store)
) -> dict[str, int]:
    documents, questions = await _act(_validated(body))
    task = asyncio.create_task(run_flood(settings.ops_target_url, documents, questions, ops, user.user_id))
    background.add(task)
    task.add_done_callback(background.discard)
    return {"documents": documents, "questions": questions}


async def _validated(body: FloodRequest) -> tuple[int, int]:
    return services.validate_flood(body.documents, body.questions)


@router.post("/reconcile", status_code=202)
async def reconcile(
    user: UserContext = Depends(operator),
    client: Client = Depends(temporal),
    ops: PostgresOpsStore = Depends(store),
) -> dict[str, str]:
    await services.run_reconciler(TemporalReconcilerTrigger(client), ops, user.user_id)
    return {"reconciler": "triggered"}


@router.post("/egress-check")
async def egress_check(
    user: UserContext = Depends(operator), ops: PostgresOpsStore = Depends(store)
) -> list[dict[str, Any]]:
    return await services.check_egress(SocketEgressProbe(), ops, user.user_id)
