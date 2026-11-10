"""Anthropic Provider：Messages API，归一化到 Provider 三调用形态。"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from stableagent.core.state import Message, ProviderConfig, ToolCall
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse

DEFAULT_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        kwargs: dict[str, Any] = {"base_url": base_url, "headers": headers, "timeout": timeout}
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
        api_key: str | None = None,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> AnthropicProvider:
        return cls(
            api_key=api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", ""),
            model=config.model,
            base_url=config.api_base or DEFAULT_BASE_URL,
            timeout=config.request_timeout_seconds,
            transport=transport,
        )

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        **options: Any,
    ) -> dict[str, Any]:
        system, wire = _to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": wire,
            "max_tokens": int(options.pop("max_tokens", 4096)),
            **options,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "input_schema": s.get("parameters") or {"type": "object", "properties": {}},
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
        response = self._client.post("/v1/messages", json=self._payload(messages, tools, **options))
        response.raise_for_status()
        return _parse_response(response.json())

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        response = await self._aclient.post(
            "/v1/messages", json=self._payload(messages, tools, **options)
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
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        current_tool: dict[str, Any] | None = None
        async with self._aclient.stream("POST", "/v1/messages", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :]
                if data.strip() in ("", "[DONE]"):
                    continue
                event = json.loads(data)
                etype = event.get("type")
                if etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        text_parts.append(delta["text"])
                        yield ProviderChunk(text=delta["text"])
                    elif delta.get("type") == "input_json_delta" and current_tool is not None:
                        current_tool["partial"] += delta.get("partial_json") or ""
                elif etype == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        current_tool = {
                            "id": block.get("id") or "",
                            "name": block.get("name") or "",
                            "partial": "",
                        }
                elif etype == "content_block_stop" and current_tool is not None:
                    raw = current_tool["partial"] or "{}"
                    tool_calls.append(
                        ToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            arguments=json.loads(raw),
                        )
                    )
                    current_tool = None
        if tool_calls:
            for i, call in enumerate(tool_calls):
                yield ProviderChunk(tool_call=call, done=i == len(tool_calls) - 1)
        else:
            yield ProviderChunk(done=True)


def _to_anthropic_messages(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    wire: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            if message.content:
                system_parts.append(message.content)
            continue
        if message.role == "assistant":
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for tc in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            wire.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
            continue
        if message.role == "tool":
            wire.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content or "",
                        }
                    ],
                }
            )
            continue
        wire.append({"role": "user", "content": message.content or ""})
    system = "\n".join(system_parts) if system_parts else None
    return system, wire


def _parse_response(data: dict[str, Any]) -> ProviderResponse:
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            texts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            calls.append(
                ToolCall(
                    id=block.get("id") or "",
                    name=block.get("name") or "",
                    arguments=dict(block.get("input") or {}),
                )
            )
    usage_raw = data.get("usage") or {}
    usage = {
        "prompt_tokens": int(usage_raw.get("input_tokens") or 0),
        "completion_tokens": int(usage_raw.get("output_tokens") or 0),
        "total_tokens": int(usage_raw.get("input_tokens") or 0)
        + int(usage_raw.get("output_tokens") or 0),
    }
    content = "".join(texts) if texts else None
    return ProviderResponse(content=content, tool_calls=calls, usage=usage)
