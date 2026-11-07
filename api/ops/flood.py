import asyncio
from collections import Counter

import httpx

from api.ops.ports import OpsStore
from loadtest.burst import ask, bounded, upload

FLOOD_HEADERS = {"X-User-Id": "flood", "X-User-Groups": "employees"}
CONCURRENCY = 50


async def run_flood(target_url: str, docs: int, questions: int, store: OpsStore, actor: str) -> None:
    await store.record("chaos.flood-started", actor, {"documents": docs, "questions": questions})
    codes: Counter = Counter()
    latencies: list[float] = []
    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(base_url=target_url, headers=FLOOD_HEADERS, timeout=60) as client:
        jobs = [bounded(semaphore, upload(client, index, codes, latencies)) for index in range(docs)]
        jobs += [bounded(semaphore, ask(client, codes, latencies)) for _ in range(questions)]
        await asyncio.gather(*jobs, return_exceptions=True)
    await store.record("chaos.flood-finished", actor, {"responses": dict(codes)})
