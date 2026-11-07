import os
from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

TABLES = (
    "ops_events, answer_cache, incident_reports, incidents, access_grants, approvals, tickets, tool_actions, "
    "audit_log, deletion_jobs, document_embeddings, document_chunks, document_acl, document_versions, documents"
)


def vector(*hot: int, size: int = 768) -> list[float]:
    values = [0.0] * size
    for index in hot:
        values[index] = 1.0
    return values


@pytest.fixture
async def db() -> AsyncIterator[AsyncConnectionPool]:
    pool = AsyncConnectionPool(os.environ["DATABASE_URL"], open=False, min_size=1, max_size=4)
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute("UPDATE documents SET active_version_id = NULL")
        await conn.execute(f"TRUNCATE {TABLES} CASCADE")
    yield pool
    await pool.close()
