from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from common.config import settings
from common.telemetry import setup_tracing
from gateway.adapters import HttpxUpstream, OllamaCatalog, PrometheusGatewayMetrics
from gateway.services import BUSY_RETRY_AFTER_SECONDS, Gateway, Lane, ModelBusy, ModelUnavailable


class ChaosRequest(BaseModel):
    fail_seconds: float


chat_upstream = HttpxUpstream("chat", settings.llm_upstream_url)
embed_upstream = HttpxUpstream("embed", settings.embed_upstream_url or settings.llm_upstream_url)
chat = Lane("chat", settings.gateway_max_inflight_chat, chat_upstream)
embed = Lane("embed", settings.gateway_max_inflight_embed, embed_upstream)
gateway = Gateway(
    settings.gateway_wait_seconds,
    PrometheusGatewayMetrics(),
    catalog=OllamaCatalog(settings.model_catalog_url) if settings.model_catalog_url else None,
    pinned=settings.pinned_model_digests,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_tracing()
    yield
    await chat_upstream.aclose()
    await embed_upstream.aclose()


app = FastAPI(title="Antara model gateway", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,metrics")


@app.exception_handler(ModelUnavailable)
async def model_unavailable(_: Request, error: ModelUnavailable) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=503)


@app.exception_handler(ModelBusy)
async def model_busy(_: Request, error: ModelBusy) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=429, headers={"Retry-After": str(BUSY_RETRY_AFTER_SECONDS)})


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {"ok": True, "chaos": gateway.failing(), "models": await gateway.verify()}


@app.post("/admin/chaos")
async def chaos(request: ChaosRequest) -> dict[str, float]:
    gateway.fail_for(request.fail_seconds)
    return {"failing_for_seconds": request.fail_seconds}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    status, content = await gateway.forward(chat, "/chat/completions", await request.body())
    return Response(content=content, status_code=status, media_type="application/json")


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
    status, content = await gateway.forward(embed, "/embeddings", await request.body())
    return Response(content=content, status_code=status, media_type="application/json")
