"""E3：OpenAI Provider 集成测试——httpx.MockTransport 离线、确定性，无凭据。"""

import json
from typing import Any

import httpx
import pytest

from stableagent.core.state import Message, ToolCall
from stableagent.providers.openai import OpenAIProvider

COMPLETION = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
}


def _provider(handler: Any) -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key", transport=httpx.MockTransport(handler))


def _assert_payload(raw: bytes) -> dict[str, Any]:
    payload = json.loads(raw)
    assert payload["model"] == "gpt-4o"
    return payload


async def test_acomplete_parses_tool_calls_and_usage() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_assert_payload(request.content))
        return httpx.Response(200, json=COMPLETION)

    provider = _provider(handler)
    response = await provider.acomplete(
        [Message(role="user", content="hi")],
        tools=[{"name": "echo", "description": "echo", "parameters": {"type": "object"}}],
    )
    assert response.tool_calls == [ToolCall(id="call_1", name="echo", arguments={"text": "hi"})]
    assert response.usage["prompt_tokens"] == 12
    wire_tool = seen[0]["tools"][0]
    assert wire_tool == {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "echo",
            "parameters": {"type": "object"},
        },
    }


def test_sync_complete_works_with_same_transport() -> None:
    provider = _provider(lambda request: httpx.Response(200, json=COMPLETION))
    response = provider.complete([Message(role="user", content="hi")])
    assert response.content is None
    assert response.tool_calls[0].name == "echo"


def test_history_wire_format_includes_tool_and_assistant_calls() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_assert_payload(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        )

    provider = _provider(handler)
    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})],
        ),
        Message(role="tool", content="x", tool_call_id="c1", name="echo"),
    ]
    provider.complete(messages)
    assistant_wire, tool_wire = seen[0]["messages"]
    assert assistant_wire["tool_calls"][0]["function"]["arguments"] == '{"text": "x"}'
    assert assistant_wire["content"] is None
    assert tool_wire["tool_call_id"] == "c1"
    assert tool_wire["name"] == "echo"


def _sse(*objs: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(obj)}\n\n".encode() for obj in objs]
    return b"".join(lines) + b"data: [DONE]\n\n"


async def test_stream_assembles_split_function_calling_chunks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert _assert_payload(request.content)["stream"] is True
        body = _sse(
            {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_9",
                                    "function": {"name": "echo", "arguments": '{"te'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": 'xt": "deep"}'}}]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    provider = _provider(handler)
    chunks = [c async for c in provider.stream([Message(role="user", content="q")])]
    texts = [c.text for c in chunks if c.text]
    assert texts == ["Hel", "lo"]
    final_chunks = [c for c in chunks if c.tool_call is not None]
    assert len(final_chunks) == 1
    call = final_chunks[0].tool_call
    assert call is not None and call.id == "call_9"
    assert call.arguments == {"text": "deep"}  # A4：arguments 跨 chunk 完整重组
    assert chunks[-1].done is True


async def test_http_error_propagates_for_loop_normalization() -> None:
    provider = _provider(lambda request: httpx.Response(429, json={"error": "rate"}))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.acomplete([Message(role="user", content="q")])


async def test_usage_with_nested_detail_objects_is_tolerated() -> None:
    """网关常在 usage 里附带 *_details 嵌套对象，只累计数值字段。"""
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 5,
            "total_tokens": 16,
            "prompt_tokens_details": {"cached_tokens": 4},
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }
    provider = _provider(lambda request: httpx.Response(200, json=payload))
    response = await provider.acomplete([Message(role="user", content="q")])
    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}
