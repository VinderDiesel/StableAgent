"""Ollama Provider：钉死 `/api/chat`，归一化到 Provider 三调用形态。"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from stableagent.core.state import Message, ProviderConfig, ToolCall
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


class OllamaProvider(Provider):
    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        kwargs: dict[str, Any] = {"base_url": base_url, "timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)
        self._aclient = httpx.AsyncClient(**kwargs)

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        await self._aclient.aclose()

    @classmethod
    def from_config(
        cls,
        config: ProviderConfig,
        *,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> OllamaProvider:
        return cls(
            model=config.model,
            base_url=config.api_base
            or os.environ.get("OLLAMA_HOST", DEFAULT_BASE_URL),
            timeout=config.request_timeout_seconds,
            transport=transport,
        )

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        stream: bool = False,
        **options: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "stream": stream,
            **options,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": s["name"],
                        "description": s.get("description", ""),
                        "parameters": s.get("parameters") or {"type": "object"},
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
            "/api/chat", json=self._payload(messages, tools, stream=False, **options)
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
            "/api/chat", json=self._payload(messages, tools, stream=False, **options)
        )
        response.raise_for_status()
        return _parse_response(response.json())

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> AsyncIterator[ProviderChunk]:
        payload = self._payload(messages, tools, stream=True, **options)
        assembler_args: dict[str, str] = {}
        async with self._aclient.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                message = data.get("message") or {}
                if message.get("content"):
                    yield ProviderChunk(text=message["content"])
                for tc in message.get("tool_calls") or []:
                    function = tc.get("function") or {}
                    name = function.get("name") or ""
                    args = function.get("arguments")
                    if isinstance(args, str):
                        assembler_args[name] = assembler_args.get(name, "") + args
                    elif isinstance(args, dict):
                        yield ProviderChunk(
                            tool_call=ToolCall(
                                id=tc.get("id") or f"ollama-{name}",
                                name=name,
                                arguments=args,
                            )
                        )
                if data.get("done"):
                    break
        for name, raw in assembler_args.items():
            yield ProviderChunk(
                tool_call=ToolCall(
                    id=f"ollama-{name}",
                    name=name,
                    arguments=json.loads(raw or "{}"),
                ),
                done=True,
            )
            return
        yield ProviderChunk(done=True)


def _to_wire(message: Message) -> dict[str, Any]:
    entry: dict[str, Any] = {"role": message.role, "content": message.content or ""}
    if message.role == "assistant" and message.tool_calls:
        entry["tool_calls"] = [
            {
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in message.tool_calls
        ]
    if message.role == "tool" and message.tool_call_id:
        entry["tool_call_id"] = message.tool_call_id
    return entry


def _parse_response(data: dict[str, Any]) -> ProviderResponse:
    message = data.get("message") or {}
    calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        function = tc.get("function") or {}
        args = function.get("arguments")
        arguments = json.loads(args or "{}") if isinstance(args, str) else dict(args or {})
        calls.append(
            ToolCall(
                id=tc.get("id") or f"ollama-{function.get('name') or 'tool'}",
                name=function.get("name") or "",
                arguments=arguments,
            )
        )
    usage = {
        "prompt_tokens": int(data.get("prompt_eval_count") or 0),
        "completion_tokens": int(data.get("eval_count") or 0),
        "total_tokens": int(data.get("prompt_eval_count") or 0)
        + int(data.get("eval_count") or 0),
    }
    return ProviderResponse(
        content=message.get("content"),
        tool_calls=calls,
        usage=usage,
    )
