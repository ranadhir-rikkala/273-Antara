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
    inbox = FakeInbox({"a1": ("run-1", "PENDING", False), "a2": ("run-2", "PENDING", True)})
    assert len(await services.approvals_for(CAROL, inbox)) == 2
    assert [row["approval_id"] for row in await services.approvals_for(DAVE, inbox)] == ["a2"]
    with pytest.raises(services.ForbiddenError):
        await services.approvals_for(ALICE, inbox)


async def test_managers_decide_and_security_leads_only_after_escalation() -> None:
    inbox = FakeInbox({"a1": ("run-1", "PENDING", False), "a2": ("run-2", "PENDING", True)})
    with pytest.raises(services.ForbiddenError):
        await services.decide("a1", DAVE, True, inbox)
    outcome = await services.decide("a2", DAVE, True, inbox)
    assert outcome.run_id == "run-2" and inbox.rows["a2"][1] == "APPROVED"
    denied = await services.decide("a1", CAROL, False, inbox)
    assert denied.approved is False and inbox.decided_by["a1"] == "carol"


async def test_missing_or_already_decided_approvals_are_rejected() -> None:
    inbox = FakeInbox({"a1": ("run-1", "APPROVED", False)})
    with pytest.raises(services.NotFoundError):
        await services.decide("nope", CAROL, True, inbox)
    with pytest.raises(services.ConflictError):
        await services.decide("a1", CAROL, True, inbox)
