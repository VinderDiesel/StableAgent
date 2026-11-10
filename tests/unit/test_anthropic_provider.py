"""Anthropic Provider：httpx MockTransport 离线协议测。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from stableagent.core.state import Message, ToolCall
from stableagent.providers.anthropic import AnthropicProvider

COMPLETION = {
    "content": [
        {"type": "text", "text": "hi"},
        {
            "type": "tool_use",
            "id": "tool_1",
            "name": "echo",
            "input": {"text": "hi"},
        },
    ],
    "usage": {"input_tokens": 10, "output_tokens": 4},
}


def _provider(handler: Any) -> AnthropicProvider:
    return AnthropicProvider(api_key="test-key", transport=httpx.MockTransport(handler))


async def test_acomplete_parses_tool_use_and_usage() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/messages")
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=COMPLETION)

    provider = _provider(handler)
    response = await provider.acomplete(
        [Message(role="user", content="hi")],
        tools=[{"name": "echo", "description": "echo", "parameters": {"type": "object"}}],
    )
    assert response.content == "hi"
    assert response.tool_calls == [ToolCall(id="tool_1", name="echo", arguments={"text": "hi"})]
    assert response.usage["prompt_tokens"] == 10
    assert response.usage["completion_tokens"] == 4
    assert seen[0]["tools"][0]["name"] == "echo"
    assert "input_schema" in seen[0]["tools"][0]


def test_sync_complete_works() -> None:
    provider = _provider(lambda r: httpx.Response(200, json=COMPLETION))
    response = provider.complete([Message(role="user", content="hi")])
    assert response.tool_calls[0].name == "echo"


async def test_stream_emits_text_and_tool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            b'data: {"type":"content_block_start","content_block":'
            b'{"type":"tool_use","id":"t1","name":"echo"}}\n\n'
            b'data: {"type":"content_block_delta","delta":'
            b'{"type":"input_json_delta","partial_json":"{\\"text\\":\\"x\\"}"}}\n\n'
            b'data: {"type":"content_block_stop"}\n\n'
            b"data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    provider = _provider(handler)
    chunks = [c async for c in provider.stream([Message(role="user", content="q")])]
    tools = [c.tool_call for c in chunks if c.tool_call is not None]
    assert len(tools) == 1
    assert tools[0].arguments == {"text": "x"}
    assert chunks[-1].done is True


def test_from_config_and_close() -> None:
    from stableagent.core.state import ProviderConfig

    provider = AnthropicProvider.from_config(
        ProviderConfig(model="claude-3-5-sonnet-20241022", request_timeout_seconds=12.0),
        api_key="k",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=COMPLETION)),
    )
    assert provider.model.startswith("claude")
    assert provider._client.timeout == httpx.Timeout(12.0)
    provider.close()


def test_system_and_tool_history_wire() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=COMPLETION)

    provider = _provider(handler)
    provider.complete(
        [
            Message(role="system", content="be brief"),
            Message(
                role="assistant",
                content="ok",
                tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "a"})],
            ),
            Message(role="tool", content="a", tool_call_id="t1", name="echo"),
        ]
    )
    assert seen[0]["system"] == "be brief"
    assert seen[0]["messages"][0]["role"] == "assistant"
    assert seen[0]["messages"][1]["content"][0]["type"] == "tool_result"


@pytest.mark.live
async def test_live_smoke_skipped_by_default() -> None:
    pytest.skip("live Anthropic smoke opt-in only")
