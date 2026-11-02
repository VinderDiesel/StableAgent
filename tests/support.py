"""P1 测试资产：脚本化 FakeProvider 与内存工具（全离线、确定性）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from stableagent.core.state import Message, ToolCall
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.tools.registry import Tool, ToolResult


class FakeProvider(Provider):
    """按脚本顺序返回响应；脚本耗尽后抛 IndexError（验证 provider 异常归一化）。

    记录每次调用收到的 messages/tools，用于 D2/D3 注入断言。
    """

    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[tuple[list[Message], list[dict[str, Any]] | None]] = []

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self.calls.append((list(messages), tools))
        response = self._responses[self._index]
        self._index += 1
        return response

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        return self.complete(messages, tools, **options)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> AsyncIterator[ProviderChunk]:
        response = self.complete(messages, tools, **options)
        if response.content:
            yield ProviderChunk(text=response.content)
        for call in response.tool_calls:
            yield ProviderChunk(tool_call=call)
        yield ProviderChunk(done=True)


class EchoTool(Tool):
    name = "echo"
    description = "echo back the input text"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=arguments.get("text", ""))


class BoomTool(Tool):
    name = "boom"
    description = "always raises"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise RuntimeError("boom")


class FlakyProvider(FakeProvider):
    """前 fail_times 次调用抛指定异常，之后按脚本返回（P2 失败自愈测试资产）。"""

    def __init__(
        self,
        responses: list[ProviderResponse],
        *,
        fail_times: int = 1,
        exc: Exception | None = None,
    ) -> None:
        super().__init__(responses)
        self._fail_times = fail_times
        self._exc = exc or TimeoutError("gateway timeout")

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        if len(self.calls) < self._fail_times:
            self.calls.append((list(messages), tools))
            raise self._exc
        return super().complete(messages, tools, **options)


def tool_response(call_id: str, text: str, usage: dict[str, int] | None = None) -> ProviderResponse:
    return ProviderResponse(
        content=f"thinking{call_id}",
        tool_calls=[ToolCall(id=call_id, name="echo", arguments={"text": text})],
        usage=usage or {},
    )


def final_response(
    text: str = "final answer", usage: dict[str, int] | None = None
) -> ProviderResponse:
    return ProviderResponse(content=text, usage=usage or {})


class FakeRedisClient:
    """够用的 redis.asyncio 替身：值为 bytes，正好覆盖后端的解码分支。"""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(self, key: str, value: str) -> bool:
        self.data[key] = value.encode()
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0


class SequenceIds:
    """确定性 ID：同前缀按序号递增，测试可读且可断言。"""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]:04d}"


class FixedClock:
    """可推进的毫秒时钟；测试绝不读墙钟。"""

    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self._now = start

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> None:
        self._now += ms
