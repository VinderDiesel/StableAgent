"""OpenAI Provider（P1）：httpx 实现 sync / async / SSE 流式 function calling。

流式 tool_calls 的 arguments 跨 chunk 拼接由 ToolCallAssembler 负责（验收 A4）。
测试通过注入 httpx transport 离线运行，不依赖网络与凭据。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from stableagent.core.state import Message, ToolCall
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class ToolCallAssembler:
    """把 OpenAI 流式 tool_calls 增量按 index 重组为完整 ToolCall。"""

    def __init__(self) -> None:
        self._by_index: dict[int, dict[str, str]] = {}

    def feed(self, deltas: list[dict[str, Any]]) -> None:
        for delta in deltas:
            slot = self._by_index.setdefault(
                delta.get("index", 0), {"id": "", "name": "", "arguments": ""}
            )
            if delta.get("id"):
                slot["id"] = delta["id"]
            function = delta.get("function") or {}
            if function.get("name"):
                slot["name"] = function["name"]
            if function.get("arguments"):
                slot["arguments"] += function["arguments"]

    def calls(self) -> list[ToolCall]:
        result: list[ToolCall] = []
        for index in sorted(self._by_index):
            slot = self._by_index[index]
            raw = slot["arguments"] or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid tool_call arguments at index {index}: {raw!r}") from exc
            result.append(ToolCall(id=slot["id"], name=slot["name"], arguments=arguments))
        return result


class OpenAIProvider(Provider):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"}
        kwargs: dict[str, Any] = {"base_url": base_url, "headers": headers, "timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)
        self._aclient = httpx.AsyncClient(**kwargs)

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        await self._aclient.aclose()

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        **options: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            **options,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": s["name"],
                        "description": s["description"],
                        "parameters": s["parameters"],
                    },
                }
                for s in tools
            ]
        return payload

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        response = self._client.post(
            "/chat/completions", json=self._payload(messages, tools, **options)
        )
        response.raise_for_status()
        return _parse_response(response.json())

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        response = await self._aclient.post(
            "/chat/completions", json=self._payload(messages, tools, **options)
        )
        response.raise_for_status()
        return _parse_response(response.json())

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> AsyncIterator[ProviderChunk]:
        payload = self._payload(messages, tools, **options)
        payload["stream"] = True
        assembler = ToolCallAssembler()
        async with self._aclient.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :]
                if data.strip() == "[DONE]":
                    break
                choice = json.loads(data)["choices"][0]
                delta = choice.get("delta") or {}
                if delta.get("tool_calls"):
                    assembler.feed(delta["tool_calls"])
                if delta.get("content"):
                    yield ProviderChunk(text=delta["content"])
        calls = assembler.calls()
        if calls:
            for i, call in enumerate(calls):
                yield ProviderChunk(tool_call=call, done=i == len(calls) - 1)
        else:
            yield ProviderChunk(done=True)


def _to_wire(message: Message) -> dict[str, Any]:
    entry: dict[str, Any] = {"role": message.role, "content": message.content or None}
    if message.role == "assistant" and message.tool_calls:
        entry["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in message.tool_calls
        ]
    if message.role == "tool":
        entry["tool_call_id"] = message.tool_call_id
        if message.name:
            entry["name"] = message.name
    return entry


def _parse_response(data: dict[str, Any]) -> ProviderResponse:
    message = data["choices"][0]["message"]
    calls = [
        ToolCall(
            id=tc["id"],
            name=tc["function"]["name"],
            arguments=json.loads(tc["function"]["arguments"] or "{}"),
        )
        for tc in message.get("tool_calls") or []
    ]
    usage = {str(key): int(value or 0) for key, value in (data.get("usage") or {}).items()}
    return ProviderResponse(content=message.get("content"), tool_calls=calls, usage=usage)
