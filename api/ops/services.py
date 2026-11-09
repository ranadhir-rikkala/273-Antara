import asyncio
from collections.abc import Awaitable
from typing import Any

from api.ops.ports import (
    Cluster,
    EgressProbe,
    MetricsReader,
    ModelControl,
    OpsStore,
    QueueReader,
    ReconcilerTrigger,
    WorkflowReader,
)
from common.models import UserContext
from common.queues import AGENT, INGEST_CPU, INGEST_GPU, RECONCILER, TOOLS

QUEUES = (INGEST_CPU, INGEST_GPU, AGENT, TOOLS, RECONCILER)
KILLABLE = ("agent", "tools", "ingest-cpu", "ingest-gpu", "reconciler", "api", "gateway")
GPU_LANE = "ingest-gpu"
AGENT_WORKERS = "agent"
REBALANCE = (
    "api",
    "agent",
    "tools",
    "ingest-cpu",
    "ingest-gpu",
    "reconciler",
    "gateway",
    "temporal",
    "temporal-ui",
    "otel-collector",
    "prometheus",
    "grafana",
)
STATELESS_OWNER = "ReplicaSet"
OPERATOR_GROUP = "operators"
MIN_OUTAGE_SECONDS, MAX_OUTAGE_SECONDS = 5, 300
MAX_FLOOD_DOCS, MAX_FLOOD_QUESTIONS = 300, 100
RECENT_WORKFLOWS, OPEN_INCIDENTS, RECENT_EVENTS = 40, 5, 25
REJECTED_QUERY = "sum(antara_api_rejected_total)"
MODEL_BUSY_QUERY = 'sum(antara_gateway_rejected_total{reason="busy"})'
THROUGHPUT_QUERY = 'sum by (instance) (rate({__name__=~"temporal_activity_execution_latency(_seconds)?_count"}[1m]))'


class ChaosError(Exception):
    pass


class NothingToBreakError(Exception):
    pass


def can_operate(user: UserContext) -> bool:
    return OPERATOR_GROUP in user.groups


async def _safe[T](call: Awaitable[T]) -> tuple[T | None, str | None]:
    try:
        return await call, None
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"


def summarize(
    pods: list[dict[str, Any]], queues: list[dict[str, Any]], workflows: list[dict[str, Any]], counts: dict[str, Any]
) -> dict[str, Any]:
    return {
        "pods_ready": sum(1 for pod in pods if pod["ready"] and not pod["terminating"]),
        "pods_total": len(pods),
        "backlog": sum(queue.get("backlog", 0) for queue in queues),
        "workflows_running": sum(1 for workflow in workflows if workflow["status"] == "RUNNING"),
        "approvals_pending": counts.get("approvals_pending", 0),
        "incidents_open": counts.get("incidents_open", 0),
    }


async def snapshot(
    queues: QueueReader,
    workflows: WorkflowReader,
    cluster: Cluster,
    model: ModelControl,
    metrics: MetricsReader,
    store: OpsStore,
) -> dict[str, Any]:
    queue_results = await asyncio.gather(*(_safe(queues.stats(name)) for name in QUEUES))
    queue_rows = [
        {"name": name, "backlog": stats.backlog, "pollers": stats.pollers}
        if stats is not None
        else {"name": name, "error": error}
        for name, (stats, error) in zip(QUEUES, queue_results, strict=True)
    ]
    (pods, pods_error), (recent, workflows_error), (health, model_error), (nodes, _) = await asyncio.gather(
        _safe(cluster.pods()), _safe(workflows.recent(RECENT_WORKFLOWS)), _safe(model.health()), _safe(cluster.nodes())
    )
    (rejected, _), (model_busy, _), (throughput, _) = await asyncio.gather(
        _safe(metrics.scalar(REJECTED_QUERY)),
        _safe(metrics.scalar(MODEL_BUSY_QUERY)),
        _safe(metrics.by_label(THROUGHPUT_QUERY, "instance")),
    )
    counts, incidents, events = await asyncio.gather(
        store.counts(), store.open_incidents(OPEN_INCIDENTS), store.events(RECENT_EVENTS)
    )
    pods, recent = pods or [], recent or []
    return {
        "summary": summarize(pods, queue_rows, recent, counts),
        "queues": queue_rows,
        "pods": pods,
        "nodes": nodes or [],
        "workflows": recent,
        "model": health or {"ok": False},
        "metrics": {
            "rejected_429": rejected,
            "model_busy_429": model_busy,
            "activities_per_second": throughput or {},
        },
        "counts": counts,
        "incidents": incidents,
        "events": events,
        "errors": {
            key: value
            for key, value in (("pods", pods_error), ("workflows", workflows_error), ("model", model_error))
            if value
        },
    }


def pick_pod(pods: list[dict[str, Any]], target: str) -> str:
    for pod in pods:
        if pod["app"] == target and pod["ready"] and not pod["terminating"]:
            return pod["name"]
    raise NothingToBreakError(f"No running {target} pod to kill")


async def kill_worker(target: str, cluster: Cluster, store: OpsStore, actor: str) -> str:
    if target not in KILLABLE:
        raise ChaosError(f"Choose one of {', '.join(KILLABLE)}")
    name = pick_pod(await cluster.pods(), target)
    await cluster.delete_pod(name)
    await store.record("chaos.kill-worker", actor, {"target": target, "pod": name})
    return name


async def model_outage(seconds: float, model: ModelControl, store: OpsStore, actor: str) -> None:
    if not MIN_OUTAGE_SECONDS <= seconds <= MAX_OUTAGE_SECONDS:
        raise ChaosError(f"Outage must last {MIN_OUTAGE_SECONDS}-{MAX_OUTAGE_SECONDS} seconds")
    await model.fail_for(seconds)
    await store.record("chaos.model-outage", actor, {"seconds": seconds})


async def gpu_lane(down: bool, cluster: Cluster, store: OpsStore, actor: str) -> None:
    await cluster.pause_scaling(GPU_LANE, 0 if down else None)
    await store.record("chaos.gpu-lane-down" if down else "chaos.gpu-lane-restored", actor, {"lane": GPU_LANE})


async def agent_workers(down: bool, cluster: Cluster, store: OpsStore, actor: str) -> None:
    await cluster.pause_scaling(AGENT_WORKERS, 0 if down else None)
    kind = "chaos.agent-workers-stopped" if down else "chaos.agent-workers-restored"
    await store.record(kind, actor, {"workers": AGENT_WORKERS})


async def worker_node(down: bool, cluster: Cluster, store: OpsStore, actor: str) -> dict[str, Any]:
    workers = [node["name"] for node in await cluster.nodes() if not node["control_plane"]]
    if not workers:
        raise NothingToBreakError("This cluster has no worker node")
    node = workers[0]
    await cluster.cordon(node, down)
    if not down:
        for deployment in REBALANCE:
            await cluster.restart(deployment)
        await store.record("chaos.node-restored", actor, {"node": node})
        return {"node": node, "moved": []}
    pods = await cluster.pods()
    moved = [pod["name"] for pod in pods if pod.get("node") == node and pod.get("owner") == STATELESS_OWNER]
    for name in moved:
        await cluster.delete_pod(name)
    await store.record("chaos.node-drained", actor, {"node": node, "moved": len(moved)})
    return {"node": node, "moved": moved}


async def corrupt_index(store: OpsStore, actor: str) -> dict[str, Any]:
    document = await store.corrupt_latest_document()
    if document is None:
        raise NothingToBreakError("No READY document to corrupt")
    await store.record("chaos.corrupt-index", actor, document)
    return document


async def run_reconciler(trigger: ReconcilerTrigger, store: OpsStore, actor: str) -> None:
    await trigger.run_now()
    await store.record("ops.reconciler-triggered", actor, {})


def validate_flood(docs: int, questions: int) -> tuple[int, int]:
    if not (0 <= docs <= MAX_FLOOD_DOCS and 0 <= questions <= MAX_FLOOD_QUESTIONS) or docs + questions == 0:
        raise ChaosError(f"Flood with 1-{MAX_FLOOD_DOCS} documents and 0-{MAX_FLOOD_QUESTIONS} questions")
    return docs, questions


OUTSIDE_SERVICES = (
    ("api.openai.com", 443),
    ("api.anthropic.com", 443),
    ("huggingface.co", 443),
    ("1.1.1.1", 443),
)


async def check_egress(probe: EgressProbe, store: OpsStore, actor: str) -> list[dict[str, Any]]:
    outcomes = await asyncio.gather(*(probe.reach(host, port) for host, port in OUTSIDE_SERVICES))
    results = [
        {"host": host, "port": port, "reachable": reachable, "detail": detail}
        for (host, port), (reachable, detail) in zip(OUTSIDE_SERVICES, outcomes, strict=True)
    ]
    reachable = [result["host"] for result in results if result["reachable"]]
    await store.record(
        "ops.egress-check",
        actor,
        {"blocked": len(results) - len(reachable), "total": len(results), "reachable": reachable},
    )
    return results
