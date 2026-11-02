"""L4 验收资产：默认配置下的可观测输出，用来和 P2 收口版本逐字节比对。

`Config()`（`assembly="passthrough"` / `compression="none"` / `capability_filter="static"`
/ `max_context_tokens=None`）下 P3 的三条路径必须全部短路。光在 P3 代码里断言"没建引擎"
不足以证明行为一致——所以要留一份**跨版本金标准**：`tests/fixtures/l4_p2_default_stream.json`
是在 P2 收口提交（`46441f0`）的工作树里跑出来的，测试拿当前代码跑同一份脚本再比。

重新生成（改了事件流形状、确认是有意为之之后）：

    git worktree add --detach /tmp/sa-p2 46441f0
    cp tests/l4_default_stream_dump.py /tmp/sa-p2/tests/
    (cd /tmp/sa-p2 && PYTHONPATH=. python -m tests.l4_default_stream_dump \
        > ../../StableAgent/tests/fixtures/l4_p2_default_stream.json)
    git worktree remove /tmp/sa-p2

脚本本身只用两版都存在的公开 API，不含任何 P3 概念；`run_id`（uuid）归一化掉。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from stableagent import Agent
from stableagent.core.state import (
    CONTRACT_VERSION,
    Config,
    EventType,
    Message,
    ToolCall,
)
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult


class EchoTool(Tool):
    name = "echo"
    description = "echo back the input text"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=arguments.get("text", ""))


class CountTool(Tool):
    name = "count"
    description = "count words in a text"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=str(len(arguments.get("text", "").split())))


class ScriptedProvider(Provider):
    """三步脚本：调 echo → 调 count → 收敛。usage 定值，杜绝计时/随机进金标准。"""

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


def _state_payload(state: Any) -> Any:
    if state is None:
        return None
    payload = state.model_dump(mode="json")
    payload["run_id"] = "RUN"  # uuid4：两版必然不同，归一化掉才可比
    return payload


async def dump() -> dict[str, Any]:
    provider = ScriptedProvider(
        [
            ProviderResponse(
                content="thinking one",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hello"})],
                usage={"prompt_tokens": 11, "completion_tokens": 3},
            ),
            ProviderResponse(
                content="thinking two",
                tool_calls=[ToolCall(id="c2", name="count", arguments={"text": "a b c"})],
                usage={"prompt_tokens": 22, "completion_tokens": 4},
            ),
            ProviderResponse(content="final answer", usage={"prompt_tokens": 33}),
        ]
    )
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(CountTool())
    agent = Agent(name="l4", provider=provider, config=Config(), registry=registry)

    events = [event.model_dump(mode="json") async for event in agent.stream("echo then count")]
    return {
        "events": events,
        "provider_calls": [
            {
                "messages": [m.model_dump(mode="json") for m in messages],
                "tools": tools,
            }
            for messages, tools in provider.calls
        ],
        "final_state": _state_payload(getattr(agent, "last_state", None)),
        "contract_version": CONTRACT_VERSION,
        "event_types": sorted(event.value for event in EventType),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(dump()), ensure_ascii=False, indent=2, sort_keys=True))
