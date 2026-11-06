from collections.abc import Sequence

from psycopg_pool import AsyncConnectionPool

from common.config import settings

_pool: AsyncConnectionPool | None = None


async def pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(settings.database_url, open=False, min_size=1, max_size=10)
        await _pool.open()
    return _pool


def vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.7g}" for value in values) + "]"
