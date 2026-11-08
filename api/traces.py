from typing import Any

import httpx


class JaegerTraces:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def get(self, trace_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=5.0) as client:
            response = await client.get(f"/api/traces/{trace_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json().get("data") or []
        return data[0] if data else None
