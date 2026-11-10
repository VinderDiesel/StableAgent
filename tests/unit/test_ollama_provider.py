"""Ollama Provider：`/api/chat` MockTransport 离线协议测。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from stableagent.core.state import Message, ToolCall
from stableagent.providers.ollama import OllamaProvider

COMPLETION = {
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "c1",
                "function": {"name": "echo", "arguments": {"text": "hi"}},
            }
        ],
    },
    "prompt_eval_count": 8,
    "eval_count": 3,
}


def _provider(handler: Any) -> OllamaProvider:
    return OllamaProvider(model="llama3.2", transport=httpx.MockTransport(handler))


async def test_acomplete_parses_tools_and_usage() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/chat")
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=COMPLETION)

    provider = _provider(handler)
    response = await provider.acomplete(
        [Message(role="user", content="hi")],
        tools=[{"name": "echo", "description": "echo", "parameters": {"type": "object"}}],
    )
    assert response.tool_calls == [ToolCall(id="c1", name="echo", arguments={"text": "hi"})]
    assert response.usage == {
        "prompt_tokens": 8,
        "completion_tokens": 3,
        "total_tokens": 11,
    }
    assert seen[0]["stream"] is False
    assert seen[0]["tools"][0]["function"]["name"] == "echo"


def test_sync_complete_works() -> None:
    provider = _provider(lambda r: httpx.Response(200, json=COMPLETION))
    assert provider.complete([Message(role="user", content="hi")]).tool_calls[0].name == "echo"


async def test_stream_done_chunk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            b'{"message":{"role":"assistant","content":"Hel"},"done":false}\n'
            b'{"message":{"role":"assistant","content":"lo"},"done":true}\n'
        )
        return httpx.Response(200, content=body)

    provider = _provider(handler)
    chunks = [c async for c in provider.stream([Message(role="user", content="q")])]
    assert [c.text for c in chunks if c.text] == ["Hel", "lo"]
    assert chunks[-1].done is True


def test_from_config_and_close() -> None:
    from stableagent.core.state import ProviderConfig

    provider = OllamaProvider.from_config(
        ProviderConfig(
            model="llama3.2",
            api_base="http://ollama.local",
            request_timeout_seconds=9.0,
        ),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=COMPLETION)),
    )
    assert str(provider._client.base_url).rstrip("/") == "http://ollama.local"
    assert provider._client.timeout == httpx.Timeout(9.0)
    provider.close()


def test_string_tool_arguments_in_response() -> None:
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "echo", "arguments": '{"text": "s"}'}},
            ],
        },
        "prompt_eval_count": 1,
        "eval_count": 1,
    }
    provider = _provider(lambda r: httpx.Response(200, json=payload))
    response = provider.complete([Message(role="user", content="hi")])
    assert response.tool_calls[0].arguments == {"text": "s"}


@pytest.mark.live
async def test_live_smoke_skipped_by_default() -> None:
    pytest.skip("live Ollama smoke opt-in only")
