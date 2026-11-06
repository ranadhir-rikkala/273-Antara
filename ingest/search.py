from common.db import pool, vector_literal
from common.llm import LLMProvider
from common.models import ChunkResult, UserContext

_SEARCH_SQL = """
SELECT c.document_id::text, c.version_id::text, c.chunk_id::text, c.text, c.page_number,
       1 - (e.embedding <=> %(query)s::vector) AS score
FROM document_chunks c
JOIN document_embeddings e ON e.chunk_id = c.chunk_id
JOIN documents d ON d.document_id = c.document_id AND d.active_version_id = c.version_id
WHERE d.deleted_at IS NULL
  AND EXISTS (
      SELECT 1 FROM document_acl a
      WHERE a.document_id = d.document_id AND a.group_name = ANY(%(groups)s)
  )
ORDER BY e.embedding <=> %(query)s::vector
LIMIT %(top_k)s
"""


async def search(query: str, user: UserContext, top_k: int = 5) -> list[ChunkResult]:
    if not user.groups:
        return []
    llm = LLMProvider()
    try:
        [embedding] = await llm.embed([query])
    finally:
        await llm.aclose()
    params = {"query": vector_literal(embedding), "groups": user.groups, "top_k": top_k}
    db = await pool()
    async with db.connection() as conn:
        rows = await (await conn.execute(_SEARCH_SQL, params)).fetchall()
    return [
        ChunkResult(
            document_id=row[0],
            version_id=row[1],
            chunk_id=row[2],
            text=row[3],
            page_number=row[4],
            score=float(row[5]),
        )
        for row in rows
    ]
