import httpx

from gateway.adapters import OllamaCatalog


async def test_ollama_catalog_reads_each_model_digest() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen2.5:1.5b", "digest": "65ec"},
                    {"name": "nomic-embed-text:latest", "digest": "0a10"},
                ]
            },
        )

    catalog = OllamaCatalog("http://ollama:11434", transport=httpx.MockTransport(reply))
    assert await catalog.digests() == {"qwen2.5:1.5b": "65ec", "nomic-embed-text:latest": "0a10"}
