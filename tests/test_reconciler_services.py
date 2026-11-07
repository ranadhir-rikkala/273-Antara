from common.models import IngestRequest, VersionStatus
from reconciler import services
from tests.fakes import FakeVersionScanner, FakeWorkflowInspector


async def test_only_stale_versions_without_a_running_ingest_are_redriven() -> None:
    stuck = IngestRequest("doc-1", "ver-1", "originals/doc-1/ver-1/a.txt", "text/plain")
    busy = IngestRequest("doc-2", "ver-2", "originals/doc-2/ver-2/b.txt", "text/plain")
    scanner = FakeVersionScanner([stuck, busy])
    found = await services.find_inconsistent(scanner, FakeWorkflowInspector({"ingest-ver-2"}))
    assert found == [stuck]
    assert scanner.queries == [([VersionStatus.UPLOADED, VersionStatus.PROCESSING], services.STALE_AFTER_MINUTES)]
