from typing import Any

import httpx

from common.config import settings


class LLMProvider:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url or settings.llm_base_url, timeout=timeout)
        self._embed_client = httpx.AsyncClient(
            base_url=base_url or settings.embedding_base_url or settings.llm_base_url, timeout=timeout
        )
        self._model = model or settings.llm_model
        self._embedding_model = embedding_model or settings.embedding_model

    async def generate(self, messages: list[dict[str, Any]]) -> str:
        data = await self._post("/chat/completions", {"model": self._model, "messages": messages})
        return data["choices"][0]["message"].get("content") or ""

    async def generate_with_tools(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"model": self._model, "messages": messages, "tools": tools, "tool_choice": "auto"}
        data = await self._post("/chat/completions", payload)
        return data["choices"][0]["message"]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._post("/embeddings", {"model": self._embedding_model, "input": texts}, self._embed_client)
        ordered = sorted(data["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._embed_client.aclose()

    async def _post(
        self, path: str, payload: dict[str, Any], client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        response = await (client or self._client).post(path, json=payload)
        response.raise_for_status()
        return response.json()
