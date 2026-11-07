from psycopg_pool import AsyncConnectionPool

from common.models import ChunkResult, UserContext
from ingest import services
from ingest.ports import Embedder, SearchIndex
from ingest.postgres import PostgresSearchIndex


class IndexRetriever:
    def __init__(self, embedder: Embedder, index: SearchIndex) -> None:
        self._embedder = embedder
        self._index = index

    async def search(self, query: str, user: UserContext, top_k: int) -> list[ChunkResult]:
        return await services.search(query, user, top_k, self._embedder, self._index)


def search_index(pool: AsyncConnectionPool, embedder: Embedder) -> IndexRetriever:
    return IndexRetriever(embedder, PostgresSearchIndex(pool))
