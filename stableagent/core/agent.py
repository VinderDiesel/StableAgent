"""Agent 骨架（契约 v1.0.0）：P0 仅可构造，执行引擎在 P1 落地。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from stableagent.core.state import AgentState, Config, Event


class Agent:
    def __init__(
        self,
        name: str,
        provider: str = "openai:gpt-4o",
        config: Config | None = None,
    ) -> None:
        self.name = name
        self.provider = provider
        self.config = config if config is not None else Config()

    def run(self, task: str) -> AgentState:
        raise NotImplementedError("Agent Loop 在 Phase 1 实现")

    async def arun(self, task: str) -> AgentState:
        raise NotImplementedError("Agent Loop 在 Phase 1 实现")

    def stream(self, task: str) -> AsyncIterator[Event]:
        raise NotImplementedError("Agent Loop 在 Phase 1 实现")
