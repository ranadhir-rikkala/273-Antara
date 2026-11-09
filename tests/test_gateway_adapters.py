import httpx
import pytest

from gateway.adapters import HttpxUpstream, OllamaCatalog


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


def upstream(seen: list[str], failure: type[Exception] | None) -> HttpxUpstream:
    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "primary" and failure is not None:
            raise failure("primary is gone", request=request)
        return httpx.Response(200, content=b"ok")

    return HttpxUpstream("chat", "http://primary:11434/v1, http://standby:11434/v1", httpx.MockTransport(reply))


async def test_upstream_sends_everything_to_the_primary_while_it_answers() -> None:
    seen: list[str] = []
    assert await upstream(seen, None).post("/chat/completions", b"{}") == (200, b"ok")
    assert seen == ["primary/v1/chat/completions"]


async def test_upstream_fails_over_to_the_standby_when_the_primary_cannot_be_reached() -> None:
    seen: list[str] = []
    assert await upstream(seen, httpx.ConnectError).post("/chat/completions", b"{}") == (200, b"ok")
    assert seen == ["primary/v1/chat/completions", "standby/v1/chat/completions"]


async def test_upstream_never_resends_a_request_the_primary_may_already_be_running() -> None:
    seen: list[str] = []
    with pytest.raises(httpx.ReadTimeout):
        await upstream(seen, httpx.ReadTimeout).post("/chat/completions", b"{}")
    assert seen == ["primary/v1/chat/completions"]
