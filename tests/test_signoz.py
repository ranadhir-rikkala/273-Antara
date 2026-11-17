import json

import httpx
import pytest

from api.signoz import SignozClient, SignozMetrics, SignozTraces

ORG = "01a1ebb0-6944-704a-bfdf-8d7c3d463567"


class FakeSignoz:
    def __init__(self, query_reply: dict, expire_first_token: bool = False) -> None:
        self.query_reply = query_reply
        self.expire_first_token = expire_first_token
        self.logins = 0
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if path == "/signoz/api/v2/sessions/context":
            assert request.url.params["email"] == "admin@antara.local"
            return httpx.Response(200, json={"status": "success", "data": {"exists": True, "orgs": [{"id": ORG}]}})
        if path == "/signoz/api/v2/sessions/email_password":
            assert json.loads(request.content) == {
                "email": "admin@antara.local",
                "password": "secret",
                "orgId": ORG,
            }
            self.logins += 1
            return httpx.Response(200, json={"status": "success", "data": {"accessToken": f"token-{self.logins}"}})
        if self.expire_first_token and request.headers["authorization"] == "Bearer token-1":
            return httpx.Response(401, json={"status": "error"})
        assert request.headers["authorization"] == f"Bearer token-{self.logins}"
        return httpx.Response(200, json=self.query_reply)


def client(fake: FakeSignoz) -> SignozClient:
    return SignozClient(
        "http://signoz:8080/signoz", "admin@antara.local", "secret", transport=httpx.MockTransport(fake)
    )


def vector(*rows: tuple[dict, str]) -> dict:
    return {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": m, "value": [1, v]} for m, v in rows]},
    }


async def test_metrics_log_in_once_and_answer_promql_like_prometheus() -> None:
    fake = FakeSignoz(vector(({}, "33")))
    metrics = SignozMetrics(client(fake))
    assert await metrics.scalar("sum(antara_api_rejected_total)") == 33.0
    assert await metrics.scalar("sum(antara_api_rejected_total)") == 33.0
    assert fake.logins == 1
    query = fake.seen[-1]
    assert query.url.path == "/signoz/api/v1/query"
    assert query.url.params["query"] == "sum(antara_api_rejected_total)"


async def test_metrics_group_by_a_label_and_report_nothing_when_there_is_no_data() -> None:
    fake = FakeSignoz(vector(({"k8s.pod.name": "agent-a"}, "0.5"), ({"k8s.pod.name": "agent-b"}, "1.5"), ({}, "2")))
    metrics = SignozMetrics(client(fake))
    assert await metrics.by_label("q", "k8s.pod.name") == {"agent-a": 0.5, "agent-b": 1.5, "?": 2.0}
    fake.query_reply = vector()
    assert await metrics.scalar("q") is None


async def test_an_expired_session_is_renewed_once() -> None:
    fake = FakeSignoz(vector(({}, "1")), expire_first_token=True)
    assert await SignozMetrics(client(fake)).scalar("q") == 1.0
    assert fake.logins == 2


async def test_a_lasting_auth_failure_is_raised_not_hidden() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions/context"):
            return httpx.Response(200, json={"data": {"orgs": [{"id": ORG}]}})
        if request.url.path.endswith("/sessions/email_password"):
            return httpx.Response(200, json={"data": {"accessToken": "t"}})
        return httpx.Response(401, json={"status": "error"})

    signoz = SignozClient("http://signoz:8080/signoz", "a", "b", transport=httpx.MockTransport(reply))
    with pytest.raises(httpx.HTTPStatusError):
        await SignozMetrics(signoz).scalar("q")


def span_rows(*rows: dict) -> dict:
    return {"status": "success", "data": {"data": {"results": [{"rows": [{"data": row} for row in rows]}]}}}


async def test_a_trace_is_read_as_plain_spans_in_time_order() -> None:
    fake = FakeSignoz(
        span_rows(
            {
                "service.name": "agent",
                "kind_string": "Client",
                "http.url": "http://gateway:8080/v1/embeddings",
                "timestamp": "2026-11-17T22:47:40.8681497Z",
            },
            {"service.name": "api", "kind_string": "Server", "timestamp": "2026-11-17T22:47:40.867324657Z"},
        )
    )
    traces = SignozTraces(client(fake), clock=lambda: 1_800_000_000.0, lookback_days=7)
    spans = await traces.get("38cf37f7caa563b8bcbbead8bd5c94df")
    assert spans == [
        {
            "service": "agent",
            "kind": "client",
            "url": "http://gateway:8080/v1/embeddings",
            "start": 1794955660868149700,
        },
        {"service": "api", "kind": "server", "url": "", "start": 1794955660867324657},
    ]
    request = fake.seen[-1]
    assert request.url.path == "/signoz/api/v5/query_range"
    body = json.loads(request.content)
    assert body["end"] == 1_800_000_000_000
    assert body["start"] == 1_800_000_000_000 - 7 * 86_400_000
    assert body["requestType"] == "raw"
    spec = body["compositeQuery"]["queries"][0]["spec"]
    assert spec["signal"] == "traces"
    assert spec["filter"] == {"expression": "trace_id = '38cf37f7caa563b8bcbbead8bd5c94df'"}


async def test_a_trace_with_no_spans_is_missing() -> None:
    fake = FakeSignoz({"status": "success", "data": {"data": {"results": [{"rows": None}]}}})
    assert await SignozTraces(client(fake)).get("f" * 32) is None


async def test_without_a_password_requests_go_out_without_logging_in() -> None:
    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=vector(({}, "4")))

    signoz = SignozClient("http://signoz:8080/signoz", "", "", transport=httpx.MockTransport(reply))
    assert await SignozMetrics(signoz).scalar("q") == 4.0
    assert [request.url.path for request in seen] == ["/signoz/api/v1/query"]
    assert "authorization" not in seen[0].headers
