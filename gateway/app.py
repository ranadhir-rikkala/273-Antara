import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from pydantic import BaseModel

from common.config import settings
from common.telemetry import setup_tracing

INFLIGHT = Gauge("antara_gateway_inflight", "Model calls in flight", ["kind"])
REJECTED = Counter("antara_gateway_rejected_total", "Model calls refused by the gateway", ["kind", "reason"])
LATENCY = Histogram(
    "antara_gateway_upstream_seconds",
    "Upstream model latency",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)


class ChaosRequest(BaseModel):
    fail_seconds: float


class GatewayState:
    def __init__(self) -> None:
        self.chat_slots = asyncio.Semaphore(settings.gateway_max_inflight_chat)
        self.embed_slots = asyncio.Semaphore(settings.gateway_max_inflight_embed)
        self.chaos_until = 0.0
        self.upstream = httpx.AsyncClient(base_url=settings.llm_upstream_url, timeout=300.0)
        self.embed_upstream = httpx.AsyncClient(
            base_url=settings.embed_upstream_url or settings.llm_upstream_url, timeout=300.0
        )


state = GatewayState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_tracing()
    yield
    await state.upstream.aclose()
    await state.embed_upstream.aclose()


app = FastAPI(title="Antara model gateway", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,metrics")


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {"ok": True, "chaos": time.monotonic() < state.chaos_until}


@app.post("/admin/chaos")
async def chaos(request: ChaosRequest) -> dict[str, float]:
    state.chaos_until = time.monotonic() + request.fail_seconds
    return {"failing_for_seconds": request.fail_seconds}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    return await _forward(request, "/chat/completions", state.chat_slots, "chat", state.upstream)


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
    return await _forward(request, "/embeddings", state.embed_slots, "embed", state.embed_upstream)


async def _forward(
    request: Request, path: str, slots: asyncio.Semaphore, kind: str, upstream_client: httpx.AsyncClient
) -> Response:
    if time.monotonic() < state.chaos_until:
        REJECTED.labels(kind, "chaos").inc()
        raise HTTPException(status_code=503, detail="Model unavailable (chaos flag)")
    try:
        await asyncio.wait_for(slots.acquire(), timeout=settings.gateway_wait_seconds)
    except TimeoutError:
        REJECTED.labels(kind, "busy").inc()
        raise HTTPException(status_code=429, detail="Model busy", headers={"Retry-After": "2"}) from None
    INFLIGHT.labels(kind).inc()
    try:
        with LATENCY.labels(kind).time():
            upstream = await upstream_client.post(
                path, content=await request.body(), headers={"content-type": "application/json"}
            )
    finally:
        INFLIGHT.labels(kind).dec()
        slots.release()
    return Response(content=upstream.content, status_code=upstream.status_code, media_type="application/json")
