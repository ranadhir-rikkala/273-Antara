import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from common.config import settings


class ChaosRequest(BaseModel):
    fail_seconds: float


class GatewayState:
    def __init__(self) -> None:
        self.chat_slots = asyncio.Semaphore(settings.gateway_max_inflight_chat)
        self.embed_slots = asyncio.Semaphore(settings.gateway_max_inflight_embed)
        self.chaos_until = 0.0
        self.upstream = httpx.AsyncClient(base_url=settings.llm_upstream_url, timeout=300.0)


state = GatewayState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await state.upstream.aclose()


app = FastAPI(title="Antara model gateway", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {"ok": True, "chaos": time.monotonic() < state.chaos_until}


@app.post("/admin/chaos")
async def chaos(request: ChaosRequest) -> dict[str, float]:
    state.chaos_until = time.monotonic() + request.fail_seconds
    return {"failing_for_seconds": request.fail_seconds}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    return await _forward(request, "/chat/completions", state.chat_slots)


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
    return await _forward(request, "/embeddings", state.embed_slots)


async def _forward(request: Request, path: str, slots: asyncio.Semaphore) -> Response:
    if time.monotonic() < state.chaos_until:
        raise HTTPException(status_code=503, detail="Model unavailable (chaos flag)")
    try:
        await asyncio.wait_for(slots.acquire(), timeout=settings.gateway_wait_seconds)
    except TimeoutError:
        raise HTTPException(status_code=429, detail="Model busy", headers={"Retry-After": "2"}) from None
    try:
        upstream = await state.upstream.post(
            path, content=await request.body(), headers={"content-type": "application/json"}
        )
    finally:
        slots.release()
    return Response(content=upstream.content, status_code=upstream.status_code, media_type="application/json")
