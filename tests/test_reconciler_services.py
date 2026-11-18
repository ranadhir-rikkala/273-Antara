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


async def test_documents_that_failed_for_a_temporary_reason_are_retried() -> None:
    busy = IngestRequest("doc-3", "ver-3", "originals/doc-3/ver-3/c.txt", "text/plain")
    timed_out = IngestRequest("doc-4", "ver-4", "originals/doc-4/ver-4/d.txt", "text/plain")
    corrupt = IngestRequest("doc-5", "ver-5", "originals/doc-5/ver-5/e.txt", "text/plain")
    already = IngestRequest("doc-6", "ver-6", "originals/doc-6/ver-6/f.txt", "text/plain")
    scanner = FakeVersionScanner(
        [],
        failed=[
            (busy, "HTTPStatusError: Client error '429 Too Many Requests' for url 'http://gateway:8080/v1/embeddings'"),
            (timed_out, "activity ScheduleToClose timeout"),
            (corrupt, "corrupt-document: not a PDF"),
            (already, "activity ScheduleToClose timeout"),
        ],
    )
    found = await services.find_inconsistent(scanner, FakeWorkflowInspector({"ingest-ver-6"}), older_than_minutes=2)
    assert found == [busy, timed_out]
    assert scanner.failed_queries == [2]


def test_only_busy_model_timeouts_and_lost_connections_count_as_temporary() -> None:
    assert services.is_temporary("HTTPStatusError: Client error '429 Too Many Requests'")
    assert services.is_temporary("Server error '503 Service Unavailable'")
    assert services.is_temporary("activity StartToClose timeout")
    assert services.is_temporary("ConnectError: All connection attempts failed")
    assert not services.is_temporary("corrupt-document: not a PDF")
    assert not services.is_temporary("permission-denied")
    assert not services.is_temporary("")
