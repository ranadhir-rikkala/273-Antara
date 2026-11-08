import pytest

from api import services
from common.models import UserContext
from tests.fakes import FakeCatalog, FakeInbox, FakeObjectStore

ALICE = UserContext("alice", ["employees"])
CAROL = UserContext("carol", ["managers", "employees"])
DAVE = UserContext("dave", ["security-leads"])


def test_groups_are_trimmed_and_blanks_dropped() -> None:
    assert services.parse_groups(" employees, ,contractors ") == ["employees", "contractors"]


async def test_upload_stores_the_file_and_registers_the_document() -> None:
    objects, catalog = FakeObjectStore(), FakeCatalog()
    request = await services.register_upload(ALICE, "vpn.md", "text/plain", b"VPN", ["employees"], objects, catalog)
    assert objects.objects[request.object_key] == b"VPN"
    assert request.object_key.endswith("/vpn.md")
    assert catalog.uploads[0]["groups"] == ["employees"] and catalog.uploads[0]["owner_id"] == "alice"
    unnamed = await services.register_upload(ALICE, None, None, b"x", ["employees"], objects, catalog)
    assert unnamed.content_type == services.DEFAULT_CONTENT_TYPE
    assert unnamed.object_key.endswith("/document")


async def test_inbox_visibility_depends_on_role() -> None:
    inbox = FakeInbox({"a1": ("run-1", "PENDING", False, "alice"), "a2": ("run-2", "PENDING", True, "alice")})
    assert len(await services.approvals_for(CAROL, inbox)) == 2
    assert [row["approval_id"] for row in await services.approvals_for(DAVE, inbox)] == ["a2"]
    with pytest.raises(services.ForbiddenError):
        await services.approvals_for(ALICE, inbox)


async def test_managers_decide_and_security_leads_only_after_escalation() -> None:
    inbox = FakeInbox({"a1": ("run-1", "PENDING", False, "alice"), "a2": ("run-2", "PENDING", True, "alice")})
    with pytest.raises(services.ForbiddenError):
        await services.decide("a1", DAVE, True, inbox)
    outcome = await services.decide("a2", DAVE, True, inbox)
    assert outcome.run_id == "run-2" and inbox.rows["a2"][1] == "APPROVED"
    denied = await services.decide("a1", CAROL, False, inbox)
    assert denied.approved is False and inbox.decided_by["a1"] == "carol"


async def test_missing_or_already_decided_approvals_are_rejected() -> None:
    inbox = FakeInbox({"a1": ("run-1", "APPROVED", False, "alice")})
    with pytest.raises(services.NotFoundError):
        await services.decide("nope", CAROL, True, inbox)
    with pytest.raises(services.ConflictError):
        await services.decide("a1", CAROL, True, inbox)


async def test_nobody_can_approve_their_own_request() -> None:
    inbox = FakeInbox({"a1": ("run-1", "PENDING", False, "carol")})
    with pytest.raises(services.ForbiddenError, match="own"):
        await services.decide("a1", CAROL, True, inbox)
    assert inbox.rows["a1"][1] == "PENDING"


def test_questions_are_trimmed_and_must_be_reasonable_length() -> None:
    assert services.clean_question("  How do I reset MFA?  ") == "How do I reset MFA?"
    for bad in ("", "   ", "x" * (services.MAX_QUESTION_LENGTH + 1)):
        with pytest.raises(services.InvalidQuestionError):
            services.clean_question(bad)


BOB = UserContext("bob", ["contractors"])
ERIN = UserContext("erin", ["operators"])
SECRET_DOC = {
    "document_id": "d1",
    "title": "runbook.md",
    "owner_id": "dave",
    "groups": ["security-leads"],
    "deleted": False,
}
HANDBOOK = {"document_id": "d2", "title": "handbook.md", "owner_id": "alice", "groups": ["employees"], "deleted": False}


async def test_users_share_documents_only_with_their_own_groups_unless_they_publish_policy() -> None:
    objects, catalog = FakeObjectStore(), FakeCatalog()
    with pytest.raises(services.ForbiddenError, match="only share with your own groups"):
        await services.register_upload(BOB, "x.md", "text/plain", b"x", ["security-leads"], objects, catalog)
    with pytest.raises(services.ForbiddenError):
        await services.register_upload(
            ALICE, "x.md", "text/plain", b"x", ["employees", "contractors"], objects, catalog
        )
    with pytest.raises(services.ForbiddenError, match="at least one group"):
        await services.register_upload(ALICE, "x.md", "text/plain", b"x", [], objects, catalog)
    await services.register_upload(DAVE, "p.md", "text/plain", b"p", ["employees", "contractors"], objects, catalog)
    await services.register_upload(ERIN, "p.md", "text/plain", b"p", ["employees"], objects, catalog)
    assert [upload["owner_id"] for upload in catalog.uploads] == ["dave", "erin"]


async def test_oversized_uploads_are_refused_before_anything_is_stored() -> None:
    objects, catalog = FakeObjectStore(), FakeCatalog()
    with pytest.raises(services.TooLargeError, match="5 MB"):
        await services.register_upload(
            ALICE, "big.txt", "text/plain", b"a" * (services.MAX_UPLOAD_BYTES + 1), ["employees"], objects, catalog
        )
    assert objects.objects == {} and catalog.uploads == []


async def test_document_details_are_hidden_from_anyone_whose_groups_cannot_read_it() -> None:
    catalog = FakeCatalog({"d1": SECRET_DOC})
    assert (await services.readable_document("d1", DAVE, catalog))["title"] == "runbook.md"
    for user in (ALICE, BOB):
        with pytest.raises(services.NotFoundError):
            await services.readable_document("d1", user, catalog)
    with pytest.raises(services.NotFoundError):
        await services.readable_document("missing", DAVE, catalog)


async def test_only_the_owner_operators_or_security_leads_may_delete_a_document() -> None:
    catalog = FakeCatalog({"d1": SECRET_DOC, "d2": HANDBOOK})
    await services.authorize_delete("d2", ALICE, catalog)
    await services.authorize_delete("d2", DAVE, catalog)
    await services.authorize_delete("d1", ERIN, catalog)
    with pytest.raises(services.ForbiddenError):
        await services.authorize_delete("d2", CAROL, catalog)
    with pytest.raises(services.NotFoundError):
        await services.authorize_delete("d1", BOB, catalog)
    with pytest.raises(services.NotFoundError):
        await services.authorize_delete("missing", ERIN, catalog)


def test_a_run_is_readable_only_by_the_person_who_asked() -> None:
    from common.models import AgentRunState

    mine = AgentRunState(status="COMPLETED", requester_id="alice")
    assert services.may_read_run(ALICE, mine) is True
    assert services.may_read_run(BOB, mine) is False
    assert services.may_read_run(ALICE, AgentRunState(status="COMPLETED")) is False


def test_a_trace_summary_lists_services_and_called_hosts_and_flags_any_outside_the_cluster() -> None:
    trace = {
        "processes": {"p1": {"serviceName": "api"}, "p2": {"serviceName": "agent"}, "p3": {"serviceName": "gateway"}},
        "spans": [
            {
                "processID": "p2",
                "startTime": 20,
                "tags": [
                    {"key": "span.kind", "value": "client"},
                    {"key": "http.url", "value": "http://gateway:8080/v1/chat"},
                ],
            },
            {
                "processID": "p1",
                "startTime": 10,
                "tags": [
                    {"key": "span.kind", "value": "server"},
                    {"key": "http.url", "value": "http://localhost:8000/agent/runs"},
                ],
            },
            {
                "processID": "p3",
                "startTime": 30,
                "tags": [
                    {"key": "span.kind", "value": "client"},
                    {"key": "http.url", "value": "http://ollama:11434/v1/chat"},
                ],
            },
            {
                "processID": "p3",
                "startTime": 40,
                "tags": [
                    {"key": "span.kind", "value": "client"},
                    {"key": "http.url", "value": "https://api.openai.com/v1/chat"},
                ],
            },
            {
                "processID": "p3",
                "startTime": 50,
                "tags": [
                    {"key": "span.kind", "value": "client"},
                    {"key": "http.url", "value": "http://10.42.0.9:9000/x"},
                ],
            },
        ],
    }
    assert services.summarize_trace(trace) == {
        "services": ["api", "agent", "gateway"],
        "hosts": ["gateway", "ollama", "api.openai.com", "10.42.0.9"],
        "outside": ["api.openai.com"],
        "spans": 5,
    }


class Traces:
    def __init__(self, traces: dict[str, dict]) -> None:
        self.traces = traces
        self.asked: list[str] = []

    async def get(self, trace_id: str) -> dict | None:
        self.asked.append(trace_id)
        return self.traces.get(trace_id)


async def test_trace_summaries_accept_only_real_trace_ids_and_report_missing_traces() -> None:
    trace_id = "0" * 31 + "a"
    traces = Traces({trace_id: {"processes": {"p": {"serviceName": "api"}}, "spans": []}})
    assert (await services.trace_summary(trace_id, traces))["services"] == []
    for bad in ("../../api/services", "A" * 32, "abc"):
        with pytest.raises(services.NotFoundError):
            await services.trace_summary(bad, traces)
    with pytest.raises(services.NotFoundError):
        await services.trace_summary("f" * 32, traces)
    assert traces.asked == [trace_id, "f" * 32]
