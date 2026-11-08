import httpx
from prometheus_client import Counter, Gauge, Histogram

INFLIGHT = Gauge("antara_gateway_inflight", "Model calls in flight", ["kind"])
REJECTED = Counter("antara_gateway_rejected_total", "Model calls refused by the gateway", ["kind", "reason"])
LATENCY = Histogram(
    "antara_gateway_upstream_seconds",
    "Upstream model latency",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)


class HttpxUpstream:
    def __init__(self, kind: str, base_url: str) -> None:
        self._kind = kind
        self._client = httpx.AsyncClient(base_url=base_url, timeout=300.0)

    async def post(self, path: str, body: bytes) -> tuple[int, bytes]:
        with LATENCY.labels(self._kind).time():
            response = await self._client.post(path, content=body, headers={"content-type": "application/json"})
        return response.status_code, response.content

    async def aclose(self) -> None:
        await self._client.aclose()


class PrometheusGatewayMetrics:
    def rejected(self, kind: str, reason: str) -> None:
        REJECTED.labels(kind, reason).inc()

    def started(self, kind: str) -> None:
        INFLIGHT.labels(kind).inc()

    def finished(self, kind: str) -> None:
        INFLIGHT.labels(kind).dec()
