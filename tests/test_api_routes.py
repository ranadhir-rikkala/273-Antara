import httpx
import pytest
from temporalio.service import RPCError, RPCStatusCode

from api import app as api_app
from common.models import AgentRunState
from tests.fakes import FakeCatalog, FakeObjectStore

ALICE = {"X-User-Id": "alice", "X-User-Groups": "employees"}
BOB = {"X-User-Id": "bob", "X-User-Groups": "contractors"}
DAVE = {"X-User-Id": "dave", "X-User-Groups": "security-leads,employees"}
DOCUMENTS = {
    "secret": {
        "document_id": "secret",
        "title": "runbook.md",
        "owner_id": "dave",
        "groups": ["security-leads"],
        "deleted": False,
    },
    "handbook": {
        "document_id": "handbook",
        "title": "handbook.md",
        "owner_id": "alice",
        "groups": ["employees"],
        "deleted": False,
    },
}


class Handle:
    def __init__(self, state: AgentRunState | None) -> None:
        self._state = state

    async def query(self, name: str, result_type: object = None, rpc_timeout: object = None) -> AgentRunState:
        if self._state is None:
            raise RPCError("missing", RPCStatusCode.NOT_FOUND, b"")
        return self._state


class Temporal:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.runs = {"agent-dave": AgentRunState(status="COMPLETED", answer="key [redacted]", requester_id="dave")}

    async def start_workflow(self, workflow: str, arg: object, id: str, task_queue: str) -> None:
        self.started.append(id)

    def get_workflow_handle(self, run_id: str) -> Handle:
        return Handle(self.runs.get(run_id))


@pytest.fixture
def temporal() -> Temporal:
    return Temporal()


@pytest.fixture
async def client(temporal: Temporal):
    api_app.app.dependency_overrides = {
        api_app.catalog: lambda: FakeCatalog(dict(DOCUMENTS)),
        api_app.temporal: lambda: temporal,
        api_app.objects: lambda: FakeObjectStore(),
        api_app.admission: lambda: None,
        api_app.admit_ingest: lambda: None,
    }
    transport = httpx.ASGITransport(app=api_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    api_app.app.dependency_overrides = {}


async def test_document_details_need_a_login_and_a_group_that_may_read_it(client: httpx.AsyncClient) -> None:
    assert (await client.get("/documents/secret")).status_code == 401
    assert (await client.get("/documents/secret", headers=BOB)).status_code == 404
    assert (await client.get("/documents/secret", headers=ALICE)).status_code == 404
    assert (await client.get("/documents/secret", headers=DAVE)).json()["title"] == "runbook.md"


async def test_only_owners_and_publishers_can_delete_documents(client: httpx.AsyncClient, temporal: Temporal) -> None:
    assert (await client.delete("/documents/secret")).status_code == 401
    assert (await client.delete("/documents/secret", headers=BOB)).status_code == 404
    assert (
        await client.delete("/documents/handbook", headers={"X-User-Id": "carol", "X-User-Groups": "employees"})
    ).status_code == 403
    assert temporal.started == []
    assert (await client.delete("/documents/handbook", headers=ALICE)).status_code == 202
    assert (await client.delete("/documents/secret", headers=DAVE)).status_code == 202
    assert temporal.started == ["delete-handbook", "delete-secret"]


async def test_a_run_can_be_read_only_by_the_person_who_asked(client: httpx.AsyncClient) -> None:
    assert (await client.get("/agent/runs/agent-dave")).status_code == 401
    assert (await client.get("/agent/runs/agent-dave", headers=BOB)).status_code == 404
    assert (await client.get("/agent/runs/agent-dave", headers=DAVE)).json()["answer"] == "key [redacted]"
    assert (await client.get("/agent/runs/agent-missing", headers=DAVE)).status_code == 404


async def test_uploads_refuse_other_groups_and_oversized_files(client: httpx.AsyncClient, temporal: Temporal) -> None:
    poison = await client.post(
        "/documents", headers=BOB, files={"file": ("x.md", b"x", "text/markdown")}, data={"groups": "security-leads"}
    )
    assert poison.status_code == 403
    big = await client.post(
        "/documents",
        headers=ALICE,
        files={"file": ("big.txt", b"a" * (5 * 1024 * 1024 + 1), "text/plain")},
        data={"groups": "employees"},
    )
    assert big.status_code == 413
    assert temporal.started == []
    ok = await client.post(
        "/documents", headers=ALICE, files={"file": ("ok.md", b"ok", "text/markdown")}, data={"groups": "employees"}
    )
    assert ok.status_code == 202 and temporal.started == [ok.json()["workflow_id"]]


async def test_trace_summaries_need_a_login_and_a_valid_trace_id(client: httpx.AsyncClient) -> None:
    assert (await client.get("/traces/" + "a" * 32)).status_code == 401
    assert (await client.get("/traces/not-a-trace", headers=ALICE)).status_code == 404
