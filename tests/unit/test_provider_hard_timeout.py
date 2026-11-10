"""P7 M2：AgentLoop 内层硬超时（与 httpx request_timeout 分离）。"""

from __future__ import annotations

import asyncio
from typing import Any

from stableagent.core.loop import AgentLoop
from stableagent.core.state import (
    AgentState,
    AgentStatus,
    Config,
    EventType,
    Message,
    ProviderConfig,
)
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.tools.registry import ToolRegistry


class _SlowProvider(Provider):
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(content="ok")

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return ProviderResponse(content="ok")

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ):
        yield ProviderChunk(done=True)


async def test_hard_timeout_fails_step_without_success() -> None:
    provider = _SlowProvider(delay=0.2)
    loop = AgentLoop(
        provider,
        ToolRegistry(),
        Config(provider=ProviderConfig(call_hard_timeout_seconds=0.05)),
    )
    state = AgentState(
        run_id="r1", agent_name="t", messages=[Message(role="user", content="hi")]
    )
    new_state, events = await loop.advance(state)
    assert new_state.status is AgentStatus.FAILED
    assert events[-1].type is EventType.ERROR
    assert events[-1].data["reason"] == "provider_error"
    assert "TimeoutError" in events[-1].data["detail"]


async def test_none_hard_timeout_allows_slow_call() -> None:
    provider = _SlowProvider(delay=0.05)
    loop = AgentLoop(
        provider,
        ToolRegistry(),
        Config(provider=ProviderConfig(call_hard_timeout_seconds=None)),
    )
    state = AgentState(
        run_id="r1", agent_name="t", messages=[Message(role="user", content="hi")]
    )
    new_state, events = await loop.advance(state)
    assert new_state.status is AgentStatus.SUCCEEDED
    assert events[-1].type is EventType.DONE
