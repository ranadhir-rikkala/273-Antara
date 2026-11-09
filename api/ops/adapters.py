import asyncio
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from temporalio.client import Client

SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
KUBERNETES_API = "https://kubernetes.default.svc"
PAUSE_ANNOTATION = "autoscaling.keda.sh/paused-replicas"
RECONCILER_SCHEDULE = "antara-reconciler"
CONTROL_PLANE_LABEL = "node-role.kubernetes.io/control-plane"
RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"
SERVICE_LABEL = "antara/service"
ROLE_LABEL = "antara/role"


class ClusterUnavailableError(Exception):
    pass


def _pod_row(item: dict[str, Any]) -> dict[str, Any]:
    metadata, status = item["metadata"], item.get("status", {})
    containers = status.get("containerStatuses", [])
    labels = metadata.get("labels", {})
    return {
        "name": metadata["name"],
        "app": labels.get(SERVICE_LABEL) or labels.get("app", "?"),
        "role": labels.get(ROLE_LABEL),
        "phase": status.get("phase", "Unknown"),
        "ready": bool(containers) and all(container.get("ready") for container in containers),
        "restarts": sum(container.get("restartCount", 0) for container in containers),
        "terminating": "deletionTimestamp" in metadata,
        "started": metadata.get("creationTimestamp"),
        "node": item.get("spec", {}).get("nodeName"),
        "owner": next((owner["kind"] for owner in metadata.get("ownerReferences", [])), None),
    }


def _node_row(item: dict[str, Any]) -> dict[str, Any]:
    conditions = item.get("status", {}).get("conditions", [])
    return {
        "name": item["metadata"]["name"],
        "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in conditions),
        "schedulable": not item.get("spec", {}).get("unschedulable", False),
        "control_plane": CONTROL_PLANE_LABEL in item["metadata"].get("labels", {}),
    }


class KubernetesCluster:
    def __init__(self, namespace: str) -> None:
        self._namespace = namespace

    def _client(self) -> httpx.AsyncClient:
        token_file = SERVICE_ACCOUNT / "token"
        if not token_file.exists():
            raise ClusterUnavailableError("Not running inside Kubernetes")
        return httpx.AsyncClient(
            base_url=KUBERNETES_API,
            verify=str(SERVICE_ACCOUNT / "ca.crt"),
            headers={"Authorization": f"Bearer {token_file.read_text().strip()}"},
            timeout=10,
        )

    async def pods(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            response = await client.get(f"/api/v1/namespaces/{self._namespace}/pods")
        items = response.raise_for_status().json()["items"]
        return sorted((_pod_row(item) for item in items), key=lambda pod: (pod["app"], pod["name"]))

    async def delete_pod(self, name: str) -> None:
        async with self._client() as client:
            response = await client.delete(f"/api/v1/namespaces/{self._namespace}/pods/{name}")
        response.raise_for_status()

    async def pause_scaling(self, scaled_object: str, replicas: int | None) -> None:
        value = None if replicas is None else str(replicas)
        async with self._client() as client:
            response = await client.patch(
                f"/apis/keda.sh/v1alpha1/namespaces/{self._namespace}/scaledobjects/{scaled_object}",
                json={"metadata": {"annotations": {PAUSE_ANNOTATION: value}}},
                headers={"Content-Type": "application/merge-patch+json"},
            )
        response.raise_for_status()

    async def nodes(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            response = await client.get("/api/v1/nodes")
        items = response.raise_for_status().json()["items"]
        return sorted((_node_row(item) for item in items), key=lambda node: (not node["control_plane"], node["name"]))

    async def cordon(self, node: str, unschedulable: bool) -> None:
        async with self._client() as client:
            response = await client.patch(
                f"/api/v1/nodes/{node}",
                json={"spec": {"unschedulable": unschedulable}},
                headers={"Content-Type": "application/merge-patch+json"},
            )
        response.raise_for_status()

    async def restart(self, deployment: str) -> None:
        stamp = datetime.now(UTC).isoformat()
        async with self._client() as client:
            response = await client.patch(
                f"/apis/apps/v1/namespaces/{self._namespace}/deployments/{deployment}",
                json={"spec": {"template": {"metadata": {"annotations": {RESTARTED_AT: stamp}}}}},
                headers={"Content-Type": "application/merge-patch+json"},
            )
        response.raise_for_status()


class TemporalWorkflows:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def recent(self, limit: int) -> list[dict[str, Any]]:
        rows = []
        async for execution in self._client.list_workflows(page_size=limit):
            rows.append(
                {
                    "id": execution.id,
                    "type": execution.workflow_type,
                    "status": execution.status.name if execution.status else "UNKNOWN",
                    "started": execution.start_time.isoformat() if execution.start_time else None,
                    "closed": execution.close_time.isoformat() if execution.close_time else None,
                }
            )
            if len(rows) >= limit:
                break
        return rows


class TemporalReconcilerTrigger:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def run_now(self) -> None:
        await self._client.get_schedule_handle(RECONCILER_SCHEDULE).trigger()


class GatewayModelControl:
    def __init__(self, pods_url: str) -> None:
        url = httpx.URL(pods_url)
        self._host, self._port = url.host, url.port or 80

    async def _copies(self) -> list[str]:
        infos = await asyncio.get_running_loop().getaddrinfo(self._host, self._port, type=socket.SOCK_STREAM)
        addresses = sorted({info[4][0] for info in infos})
        if not addresses:
            raise ClusterUnavailableError(f"No gateway copies behind {self._host}")
        return [f"http://{address}:{self._port}" for address in addresses]

    async def health(self) -> dict[str, Any]:
        copies = await self._copies()
        async with httpx.AsyncClient(timeout=5) as client:
            replies = await asyncio.gather(*(client.get(f"{copy}/healthz") for copy in copies))
        bodies = [reply.raise_for_status().json() for reply in replies]
        return {
            **bodies[0],
            "ok": all(body.get("ok") for body in bodies),
            "chaos": any(body.get("chaos") for body in bodies),
            "copies": len(bodies),
        }

    async def fail_for(self, seconds: float) -> None:
        copies = await self._copies()
        async with httpx.AsyncClient(timeout=5) as client:
            replies = await asyncio.gather(
                *(client.post(f"{copy}/admin/chaos", json={"fail_seconds": seconds}) for copy in copies)
            )
        for reply in replies:
            reply.raise_for_status()


class PrometheusMetrics:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def _query(self, query: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=5) as client:
            response = await client.get("/api/v1/query", params={"query": query})
        return response.raise_for_status().json()["data"]["result"]

    async def scalar(self, query: str) -> float | None:
        result = await self._query(query)
        return float(result[0]["value"][1]) if result else None

    async def by_label(self, query: str, label: str) -> dict[str, float]:
        return {row["metric"].get(label, "?"): float(row["value"][1]) for row in await self._query(query)}


class PostgresOpsStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def counts(self) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT "
                    "(SELECT count(*) FROM approvals WHERE status = 'PENDING'), "
                    "(SELECT count(*) FROM approvals WHERE status = 'PENDING' AND escalated), "
                    "(SELECT count(*) FROM incidents WHERE status = 'OPEN'), "
                    "(SELECT count(*) FROM tickets), "
                    "(SELECT count(*) FROM access_grants WHERE revoked_at IS NULL), "
                    "(SELECT count(*) FROM access_grants WHERE revoked_at IS NULL AND expires_at IS NOT NULL)"
                )
            ).fetchone()
            documents = await (
                await conn.execute(
                    "SELECT v.status, count(*) FROM documents d JOIN document_versions v "
                    "ON v.document_id = d.document_id WHERE d.deleted_at IS NULL GROUP BY v.status"
                )
            ).fetchall()
            harness = await (
                await conn.execute(
                    "SELECT CASE WHEN action LIKE 'harness.%%' THEN substr(action, 9) "
                    "WHEN action = 'agent.retrieve' THEN 'searches' "
                    "WHEN action LIKE 'tool.%%' THEN 'tool-actions' "
                    "ELSE 'approvals' END, count(*) FROM audit_log "
                    "WHERE action LIKE 'harness.%%' OR action LIKE 'tool.%%' "
                    "OR action IN ('agent.retrieve', 'approval.opened') "
                    "GROUP BY 1"
                )
            ).fetchall()
        keys = (
            "approvals_pending",
            "approvals_escalated",
            "incidents_open",
            "tickets",
            "access_grants",
            "leases_active",
        )
        return {**dict(zip(keys, row, strict=True)), "documents": dict(documents), "harness": dict(harness)}

    async def open_incidents(self, limit: int) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT incident_id::text, title, system, reporter_count, updated_at FROM incidents "
                    "WHERE status = 'OPEN' ORDER BY updated_at DESC LIMIT %s",
                    (limit,),
                )
            ).fetchall()
        return [
            {"incident_id": r[0], "title": r[1], "system": r[2], "reporters": r[3], "updated": r[4].isoformat()}
            for r in rows
        ]

    async def events(self, limit: int) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT kind, actor_id, detail, created_at FROM ops_events ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            ).fetchall()
        return [{"kind": r[0], "actor": r[1], "detail": r[2], "at": r[3].isoformat()} for r in rows]

    async def record(self, kind: str, actor_id: str, detail: dict[str, Any]) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO ops_events (kind, actor_id, detail) VALUES (%s, %s, %s)", (kind, actor_id, Jsonb(detail))
            )

    async def corrupt_latest_document(self) -> dict[str, Any] | None:
        async with self._pool.connection() as conn, conn.transaction():
            row = await (
                await conn.execute(
                    "SELECT d.document_id::text, v.version_id::text, d.title FROM documents d "
                    "JOIN document_versions v ON v.version_id = d.active_version_id "
                    "WHERE d.deleted_at IS NULL AND v.status = 'READY' ORDER BY v.updated_at DESC LIMIT 1 FOR UPDATE"
                )
            ).fetchone()
            if row is None:
                return None
            document_id, version_id, title = row
            broken = (
                await conn.execute(
                    "DELETE FROM document_embeddings WHERE chunk_id IN "
                    "(SELECT chunk_id FROM document_chunks WHERE version_id = %s)",
                    (version_id,),
                )
            ).rowcount
            await conn.execute("UPDATE documents SET active_version_id = NULL WHERE document_id = %s", (document_id,))
            await conn.execute(
                "UPDATE document_versions SET status = 'PROCESSING', updated_at = now() - interval '1 hour' "
                "WHERE version_id = %s",
                (version_id,),
            )
        return {"document_id": document_id, "version_id": version_id, "title": title, "embeddings_deleted": broken}


class SocketEgressProbe:
    def __init__(self, timeout: float = 3.0) -> None:
        self._timeout = timeout

    async def reach(self, host: str, port: int) -> tuple[bool, str]:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), self._timeout)
        except TimeoutError:
            return False, f"no answer within {self._timeout:g} s"
        except OSError as error:
            return False, self.explain(error)
        writer.close()
        return True, "connected"

    @staticmethod
    def explain(error: OSError) -> str:
        text = str(error)
        if isinstance(error, ConnectionRefusedError) or "Errno 111" in text:
            return "connection refused"
        if error.errno == 101 or "Errno 101" in text:
            return "network unreachable"
        return error.strerror or type(error).__name__
