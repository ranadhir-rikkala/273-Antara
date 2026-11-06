from dataclasses import asdict

from psycopg.types.json import Jsonb
from temporalio import activity

from common.config import settings
from common.db import pool, vector_literal
from common.llm import LLMProvider
from common.models import CachedAnswer, CacheLookupRequest, CacheStoreRequest, ChunkResult
from common.names import Activities

_LOOKUP_SQL = """
SELECT c.cache_id, c.answer, c.citations
FROM answer_cache c
WHERE c.groups_key = %(groups_key)s
  AND c.created_at > now() - make_interval(hours => %(ttl)s)
  AND 1 - (c.embedding <=> %(embedding)s::vector) >= %(threshold)s
  AND NOT EXISTS (
      SELECT 1 FROM unnest(c.version_ids) AS cited(version_id)
      WHERE NOT EXISTS (
          SELECT 1 FROM documents d
          WHERE d.active_version_id = cited.version_id AND d.deleted_at IS NULL
      )
  )
ORDER BY c.embedding <=> %(embedding)s::vector
LIMIT 1
"""


def groups_key(groups: list[str]) -> str:
    return ",".join(sorted(set(groups)))


async def _embed(text: str) -> str:
    llm = LLMProvider()
    try:
        [embedding] = await llm.embed([text])
    finally:
        await llm.aclose()
    return vector_literal(embedding)


@activity.defn(name=Activities.CACHE_LOOKUP)
async def lookup_cached_answer(request: CacheLookupRequest) -> CachedAnswer | None:
    params = {
        "groups_key": groups_key(request.user.groups),
        "ttl": settings.cache_ttl_hours,
        "embedding": await _embed(request.question),
        "threshold": settings.cache_similarity,
    }
    db = await pool()
    async with db.connection() as conn:
        row = await (await conn.execute(_LOOKUP_SQL, params)).fetchone()
        if row is None:
            return None
        await conn.execute("UPDATE answer_cache SET hits = hits + 1 WHERE cache_id = %s", (row[0],))
    return CachedAnswer(answer=row[1], citations=[ChunkResult(**citation) for citation in row[2]])


@activity.defn(name=Activities.CACHE_STORE)
async def store_cached_answer(request: CacheStoreRequest) -> None:
    version_ids = sorted({citation.version_id for citation in request.citations})
    db = await pool()
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO answer_cache (question, embedding, groups_key, answer, citations, version_ids) "
            "VALUES (%s, %s::vector, %s, %s, %s, %s::uuid[])",
            (
                request.question,
                await _embed(request.question),
                groups_key(request.user.groups),
                request.answer,
                Jsonb([asdict(citation) for citation in request.citations]),
                version_ids,
            ),
        )
