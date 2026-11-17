import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

DAY_MS = 86_400_000


class SignozClient:
    def __init__(self, base_url: str, email: str, password: str, transport: httpx.AsyncBaseTransport | None = None):
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._transport = transport
        self._token: str | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=10.0, transport=self._transport)

    async def _login(self) -> str:
        async with self._client() as client:
            context = await client.get("/api/v2/sessions/context", params={"email": self._email})
            org_id = context.raise_for_status().json()["data"]["orgs"][0]["id"]
            session = await client.post(
                "/api/v2/sessions/email_password",
                json={"email": self._email, "password": self._password, "orgId": org_id},
            )
        return session.raise_for_status().json()["data"]["accessToken"]

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(2):
            if self._token is None and self._password:
                self._token = await self._login()
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
            async with self._client() as client:
                response = await client.request(method, path, headers=headers, **kwargs)
            if response.status_code == 401 and attempt == 0 and self._password:
                self._token = None
                continue
            return response.raise_for_status().json()


class SignozMetrics:
    def __init__(self, signoz: SignozClient) -> None:
        self._signoz = signoz

    async def _query(self, query: str) -> list[dict[str, Any]]:
        reply = await self._signoz.request("GET", "/api/v1/query", params={"query": query})
        return reply["data"]["result"]

    async def scalar(self, query: str) -> float | None:
        result = await self._query(query)
        return float(result[0]["value"][1]) if result else None

    async def by_label(self, query: str, label: str) -> dict[str, float]:
        return {row["metric"].get(label, "?"): float(row["value"][1]) for row in await self._query(query)}


def _nanoseconds(stamp: str) -> int:
    whole, _, fraction = stamp.rstrip("Z").partition(".")
    seconds = int(datetime.fromisoformat(f"{whole}+00:00").timestamp())
    return seconds * 1_000_000_000 + int(fraction.ljust(9, "0")[:9])


class SignozTraces:
    def __init__(self, signoz: SignozClient, clock: Callable[[], float] = time.time, lookback_days: int = 7) -> None:
        self._signoz = signoz
        self._clock = clock
        self._lookback_days = lookback_days

    async def get(self, trace_id: str) -> list[dict[str, Any]] | None:
        end = int(self._clock() * 1000)
        body = {
            "schemaVersion": "v1",
            "start": end - self._lookback_days * DAY_MS,
            "end": end,
            "requestType": "raw",
            "compositeQuery": {
                "queries": [
                    {
                        "type": "builder_query",
                        "spec": {
                            "name": "A",
                            "signal": "traces",
                            "filter": {"expression": f"trace_id = '{trace_id}'"},
                            "selectFields": [
                                {"name": "service.name", "fieldContext": "resource"},
                                {"name": "kind_string"},
                                {"name": "http.url", "fieldContext": "attribute"},
                                {"name": "timestamp"},
                            ],
                            "order": [{"key": {"name": "timestamp"}, "direction": "asc"}],
                            "limit": 1000,
                        },
                    }
                ]
            },
        }
        reply = await self._signoz.request("POST", "/api/v5/query_range", json=body)
        rows = reply["data"]["data"]["results"][0].get("rows") or []
        if not rows:
            return None
        return [
            {
                "service": row["data"].get("service.name", "?"),
                "kind": str(row["data"].get("kind_string", "")).lower(),
                "url": row["data"].get("http.url") or "",
                "start": _nanoseconds(row["data"]["timestamp"]),
            }
            for row in rows
        ]
