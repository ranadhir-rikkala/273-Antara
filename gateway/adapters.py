import httpx
from prometheus_client import Counter, Gauge, Histogram

CONNECT_SECONDS = 2.0

INFLIGHT = Gauge("antara_gateway_inflight", "Model calls in flight", ["kind"])
FAILOVERS = Counter("antara_gateway_failovers_total", "Model calls sent to the standby", ["kind"])
REJECTED = Counter("antara_gateway_rejected_total", "Model calls refused by the gateway", ["kind", "reason"])
LATENCY = Histogram(
    "antara_gateway_upstream_seconds",
    "Upstream model latency",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)


class HttpxUpstream:
    def __init__(self, kind: str, base_urls: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._kind = kind
        self._bases = [url.strip().rstrip("/") for url in base_urls.split(",") if url.strip()]
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=CONNECT_SECONDS),
            limits=httpx.Limits(max_keepalive_connections=0),
            transport=transport,
        )

    async def post(self, path: str, body: bytes) -> tuple[int, bytes]:
        with LATENCY.labels(self._kind).time():
            for base in self._bases[:-1]:
                try:
                    response = await self._send(base, path, body)
                    return response.status_code, response.content
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    FAILOVERS.labels(self._kind).inc()
            response = await self._send(self._bases[-1], path, body)
        return response.status_code, response.content

    async def _send(self, base: str, path: str, body: bytes) -> httpx.Response:
        return await self._client.post(f"{base}{path}", content=body, headers={"content-type": "application/json"})

    async def aclose(self) -> None:
        await self._client.aclose()


class PrometheusGatewayMetrics:
    def rejected(self, kind: str, reason: str) -> None:
        REJECTED.labels(kind, reason).inc()

    def started(self, kind: str) -> None:
        INFLIGHT.labels(kind).inc()

    def finished(self, kind: str) -> None:
        INFLIGHT.labels(kind).dec()


class OllamaCatalog:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url
        self._transport = transport

    async def digests(self) -> dict[str, str]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=5.0, transport=self._transport) as client:
            response = await client.get("/api/tags")
        response.raise_for_status()
        return {model["name"]: model["digest"] for model in response.json().get("models", [])}
