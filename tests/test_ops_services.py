from typing import Any

import pytest

from api.ops import services
from common.models import QueueStats, UserContext


class Queues:
    async def stats(self, queue: str) -> QueueStats:
        if queue == "reconciler":
            raise ConnectionError("temporal busy")
        return QueueStats(backlog=4 if queue == "agent" else 0, pollers=1)


class Workflows:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def recent(self, limit: int) -> list[dict[str, Any]]:
        if self.fail:
            raise RuntimeError("visibility down")
        return [{"id": "agent-1", "status": "RUNNING"}, {"id": "ingest-1", "status": "COMPLETED"}]


class Cluster:
    def __init__(self, pods: list[dict[str, Any]] | None = None) -> None:
        self._pods = (
            pods
            if pods is not None
            else [
                {"name": "agent-a", "app": "agent", "ready": True, "terminating": False},
                {"name": "agent-b", "app": "agent", "ready": False, "terminating": False},
                {"name": "tools-a", "app": "tools", "ready": True, "terminating": True},
            ]
        )
        self.deleted: list[str] = []
        self.paused: list[tuple[str, int | None]] = []

    async def pods(self) -> list[dict[str, Any]]:
        return self._pods

    async def delete_pod(self, name: str) -> None:
        self.deleted.append(name)

    async def pause_scaling(self, scaled_object: str, replicas: int | None) -> None:
        self.paused.append((scaled_object, replicas))


class Model:
    def __init__(self) -> None:
        self.failures: list[float] = []

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "chaos": False}

    async def fail_for(self, seconds: float) -> None:
        self.failures.append(seconds)


class Metrics:
    async def scalar(self, query: str) -> float | None:
        return 12.0

    async def by_label(self, query: str, label: str) -> dict[str, float]:
        return {"agent-a": 0.5}


class Store:
    def __init__(self, corruptible: dict[str, Any] | None = None) -> None:
        self.recorded: list[tuple[str, str, dict[str, Any]]] = []
        self.corruptible = corruptible

    async def counts(self) -> dict[str, Any]:
        return {"approvals_pending": 2, "incidents_open": 1}

    async def open_incidents(self, limit: int) -> list[dict[str, Any]]:
        return [{"system": "vpn", "reporters": 6}]

    async def events(self, limit: int) -> list[dict[str, Any]]:
        return [{"kind": kind} for kind, _, _ in self.recorded]

    async def record(self, kind: str, actor_id: str, detail: dict[str, Any]) -> None:
        self.recorded.append((kind, actor_id, detail))

    async def corrupt_latest_document(self) -> dict[str, Any] | None:
        return self.corruptible


class Trigger:
    def __init__(self) -> None:
        self.runs = 0

    async def run_now(self) -> None:
        self.runs += 1


def test_only_operators_may_operate() -> None:
    assert services.can_operate(UserContext("erin", ["operators"]))
    assert not services.can_operate(UserContext("carol", ["managers"]))


async def test_snapshot_reports_each_section_and_survives_partial_failures() -> None:
    state = await services.snapshot(Queues(), Workflows(fail=True), Cluster(), Model(), Metrics(), Store())
    assert state["summary"] == {
        "pods_ready": 1,
        "pods_total": 3,
        "backlog": 4,
        "workflows_running": 0,
        "approvals_pending": 2,
        "incidents_open": 1,
    }
    assert state["queues"][-1]["name"] == "reconciler" and "temporal busy" in state["queues"][-1]["error"]
    assert "visibility down" in state["errors"]["workflows"]
    assert state["metrics"] == {"rejected_429": 12.0, "model_busy_429": 12.0, "activities_per_second": {"agent-a": 0.5}}
    healthy = await services.snapshot(Queues(), Workflows(), Cluster(), Model(), Metrics(), Store())
    assert healthy["summary"]["workflows_running"] == 1 and healthy["errors"] == {}


async def test_killing_a_worker_picks_a_ready_pod_and_records_it() -> None:
    cluster, store = Cluster(), Store()
    assert await services.kill_worker("agent", cluster, store, "erin") == "agent-a"
    assert cluster.deleted == ["agent-a"]
    assert store.recorded == [("chaos.kill-worker", "erin", {"target": "agent", "pod": "agent-a"})]


async def test_killing_needs_a_known_target_with_a_live_pod() -> None:
    with pytest.raises(services.ChaosError):
        await services.kill_worker("app-db", Cluster(), Store(), "erin")
    with pytest.raises(services.NothingToBreakError):
        await services.kill_worker("tools", Cluster(), Store(), "erin")


async def test_model_outages_are_bounded_and_recorded() -> None:
    model, store = Model(), Store()
    await services.model_outage(30, model, store, "erin")
    assert model.failures == [30] and store.recorded[0][0] == "chaos.model-outage"
    for bad in (1, 1000):
        with pytest.raises(services.ChaosError):
            await services.model_outage(bad, model, store, "erin")


async def test_gpu_lane_is_paused_and_restored_through_keda() -> None:
    cluster, store = Cluster(), Store()
    await services.gpu_lane(True, cluster, store, "erin")
    await services.gpu_lane(False, cluster, store, "erin")
    assert cluster.paused == [("ingest-gpu", 0), ("ingest-gpu", None)]
    assert [kind for kind, _, _ in store.recorded] == ["chaos.gpu-lane-down", "chaos.gpu-lane-restored"]


async def test_corrupting_the_index_needs_a_ready_document() -> None:
    store = Store(corruptible={"title": "vpn-access.md", "version_id": "v1"})
    assert (await services.corrupt_index(store, "erin"))["title"] == "vpn-access.md"
    assert store.recorded[0] == ("chaos.corrupt-index", "erin", {"title": "vpn-access.md", "version_id": "v1"})
    with pytest.raises(services.NothingToBreakError):
        await services.corrupt_index(Store(), "erin")


async def test_reconciler_can_be_run_on_demand() -> None:
    trigger, store = Trigger(), Store()
    await services.run_reconciler(trigger, store, "erin")
    assert trigger.runs == 1 and store.recorded[0][0] == "ops.reconciler-triggered"


def test_floods_are_bounded() -> None:
    assert services.validate_flood(200, 50) == (200, 50)
    for docs, questions in ((0, 0), (1000, 0), (10, 500), (-1, 5)):
        with pytest.raises(services.ChaosError):
            services.validate_flood(docs, questions)


class Probe:
    def __init__(self, open_hosts: set[str]) -> None:
        self.open_hosts = open_hosts
        self.tried: list[tuple[str, int]] = []

    async def reach(self, host: str, port: int) -> tuple[bool, str]:
        self.tried.append((host, port))
        return (True, "connected") if host in self.open_hosts else (False, "connection refused")


async def test_egress_check_tries_every_outside_ai_service_and_records_the_result() -> None:
    store, probe = Store(), Probe(set())
    results = await services.check_egress(probe, store, "erin")
    assert [result["host"] for result in results] == [host for host, _ in services.OUTSIDE_SERVICES]
    assert all(result["reachable"] is False for result in results)
    assert store.recorded == [
        (
            "ops.egress-check",
            "erin",
            {"blocked": len(services.OUTSIDE_SERVICES), "total": len(services.OUTSIDE_SERVICES), "reachable": []},
        )
    ]


async def test_egress_check_names_any_outside_service_that_was_reachable() -> None:
    store = Store()
    await services.check_egress(Probe({"huggingface.co"}), store, "erin")
    detail = store.recorded[0][2]
    assert detail["reachable"] == ["huggingface.co"] and detail["blocked"] == detail["total"] - 1
