"""P1 离线演示：脚本化 Provider 驱动真实 loop，任何机器可直接运行（无需 API Key）。

展示事件流形态与 P2 断点恢复的接口；真实模型调用见 01_basic_research_agent.py。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from stableagent import Agent, EventType
from stableagent.core.state import Config, Message, ToolCall
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult


class ScriptedProvider(Provider):
    """模拟一个先查资料、再统计字数、最后总结的模型。"""

    def __init__(self) -> None:
        self._script = [
            ProviderResponse(
                content="先看看语料目录。",
                tool_calls=[ToolCall(id="c1", name="list_files", arguments={})],
                usage={"prompt_tokens": 20, "completion_tokens": 5},
            ),
            ProviderResponse(
                content="目录里有 2 个文件，统计一下总字数。",
                tool_calls=[ToolCall(id="c2", name="word_count", arguments={"dir": "corpus"})],
                usage={"prompt_tokens": 40, "completion_tokens": 8},
            ),
            ProviderResponse(
                content=(
                    "研究完成：语料共 2 个文件、8612 字，核心论点是 checkpoint 决定 Agent 可用性。"
                ),
                usage={"prompt_tokens": 70, "completion_tokens": 30},
            ),
        ]
        self._index = 0

    def complete(
        self, messages: list[Message], tools: Any = None, **options: Any
    ) -> ProviderResponse:
        response = self._script[self._index]
        self._index += 1
        return response

    async def acomplete(
        self, messages: list[Message], tools: Any = None, **options: Any
    ) -> ProviderResponse:
        return self.complete(messages, tools, **options)

    async def stream(
        self, messages: list[Message], tools: Any = None, **options: Any
    ) -> AsyncIterator[ProviderChunk]:
        response = self.complete(messages, tools, **options)
        if response.content:
            yield ProviderChunk(text=response.content)
        for call in response.tool_calls:
            yield ProviderChunk(tool_call=call)
        yield ProviderChunk(done=True)


class ListFiles(Tool):
    name = "list_files"
    description = "列出语料文件"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output="agents.md, context.md")


class WordCount(Tool):
    name = "word_count"
    description = "统计语料字数"
    parameters = {"type": "object", "properties": {"dir": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=8612)


async def main() -> None:
    registry = ToolRegistry()
    registry.register(ListFiles())
    registry.register(WordCount())

    agent = Agent(
        name="offline-demo", provider=ScriptedProvider(), config=Config(), registry=registry
    )

    async for event in agent.stream("统计语料并给出核心论点"):
        label = {
            EventType.THINKING: "思考",
            EventType.TOOL_CALL: "调用",
            EventType.TOOL_RESULT: "结果",
            EventType.DONE: "完成",
        }.get(event.type, event.type.value)
        print(f"[{event.step}] {label}: {event.data}")

    state = agent._last_state
    assert state is not None
    print(f"\nstatus={state.status.value} steps={state.step} usage={state.usage}")
    print(
        f"快照可直接重建（P2 恢复的地基）: AgentState.loads(state.snapshot()) -> "
        f"{type(state).__name__}(step={state.step})"
    )


if __name__ == "__main__":
    asyncio.run(main())
