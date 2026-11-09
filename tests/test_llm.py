import json

import httpx

from common.config import settings
from common.llm import LLMProvider


def provider_recording(sent: list[dict]) -> LLMProvider:
    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = LLMProvider(base_url="http://model/v1", model="m")
    provider._client = httpx.AsyncClient(base_url="http://model/v1", transport=httpx.MockTransport(reply))
    return provider


async def test_every_chat_call_caps_the_answer_length_so_a_looping_model_stops() -> None:
    sent: list[dict] = []
    provider = provider_recording(sent)
    await provider.generate([{"role": "user", "content": "hi"}])
    await provider.generate_with_tools([{"role": "user", "content": "hi"}], [])
    await provider.aclose()
    assert [payload["max_tokens"] for payload in sent] == [settings.llm_max_tokens] * 2
    assert 0 < settings.llm_max_tokens <= 1024
