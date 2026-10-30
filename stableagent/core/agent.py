"""Agent 运行时装配（契约 v1.1.0）：run / arun / stream 三形态 + pause/resume 接入点。

P1 语义（docs/p1-dod-checklist.md 第 0 节）：
- F3：pause() 在步边界生效；无进行中运行时为显式 no-op（返回 False）。
  resume()/aresume() 从 PAUSED 快照续跑；无暂停态时抛 ValueError。
- pause/resume 的最小实现只在内存（快照即 AgentState），P2 替换为持久化层，
  不改本调用面。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from stableagent.context.assembly import ContextAssembler
from stableagent.core.loop import AgentLoop, ToolSelector
from stableagent.core.state import AgentState, AgentStatus, Config, Event, Message
from stableagent.providers.base import Provider
from stableagent.tools.registry import ToolRegistry


class Agent:
    def __init__(
        self,
        name: str,
        provider: Provider | str = "openai:gpt-4o",
        config: Config | None = None,
        registry: ToolRegistry | None = None,
        assembler: ContextAssembler | None = None,
        tool_selector: ToolSelector | None = None,
    ) -> None:
        self.name = name
        self.provider = provider
        self.config = config if config is not None else Config()
        self._registry = registry if registry is not None else ToolRegistry()
        self._assembler = assembler
        self._tool_selector = tool_selector
        self._loop: AgentLoop | None = None
        self._last_state: AgentState | None = None
        self._running = False

    def _engine(self) -> AgentLoop:
        if isinstance(self.provider, str):
            raise ValueError(
                "P1 需注入 Provider 实例（如 OpenAIProvider）；字符串寻址在后续版本提供"
            )
        if self._loop is None:
            self._loop = AgentLoop(
                provider=self.provider,
                registry=self._registry,
                config=self.config,
                assembler=self._assembler,
                tool_selector=self._tool_selector,
            )
        return self._loop

    async def _drive(self, task: str) -> AsyncIterator[tuple[Event, AgentState]]:
        if self._last_state is not None and self._last_state.status is AgentStatus.PAUSED:
            raise RuntimeError("存在暂停中的运行：请先 resume()，或使用新的 Agent 实例")
        state = AgentState(
            run_id=uuid.uuid4().hex,
            agent_name=self.name,
            status=AgentStatus.RUNNING,
            messages=[Message(role="user", content=task)],
        )
        engine = self._engine()
        self._running = True
        try:
            async for event, after in engine.drive(state):
                self._last_state = after
                yield event, after
        finally:
            self._running = False

    async def stream(self, task: str) -> AsyncIterator[Event]:
        async for event, _ in self._drive(task):
            yield event

    async def arun(self, task: str) -> AgentState:
        final: AgentState | None = None
        async for _, state in self._drive(task):
            final = state
        assert final is not None, "drive 至少产出一个事件（F4）"
        return final

    def run(self, task: str) -> AgentState:
        return asyncio.run(self.arun(task))

    def pause(self) -> bool:
        if not self._running:
            return False  # F3：显式 no-op
        self._engine().pause_requested = True
        return True

    async def aresume(self) -> AgentState:
        state = self._resume_state()
        engine = self._engine()
        final = state
        self._running = True
        try:
            async for _, after in engine.drive(state):
                self._last_state = after
                final = after
        finally:
            self._running = False
        return final

    def resume(self) -> AgentState:
        return asyncio.run(self.aresume())

    def _resume_state(self) -> AgentState:
        if self._last_state is None or self._last_state.status is not AgentStatus.PAUSED:
            raise ValueError("没有可恢复的 PAUSED 状态")
        return self._last_state.model_copy(update={"status": AgentStatus.RUNNING})
