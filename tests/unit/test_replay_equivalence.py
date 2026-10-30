"""C2：断点重放等价（P1→P2 交接的核心证据）。

在第 k 步边界快照 AgentState，反序列化后从该快照继续 drive，
事件流必须与不中断跑完的剩余部分逐条一致、终态一致。
对每个步边界参数化执行。
"""

from __future__ import annotations

import pytest

from stableagent.core.loop import AgentLoop
from stableagent.core.state import AgentState, AgentStatus, Config, Event, Message
from stableagent.tools.registry import ToolRegistry
from tests.support import EchoTool, FakeProvider, final_response, tool_response

SCRIPT = [
    tool_response("c1", "one", {"prompt_tokens": 5, "completion_tokens": 1}),
    tool_response("c2", "two", {"prompt_tokens": 6, "completion_tokens": 1}),
    tool_response("c3", "three", {"prompt_tokens": 7, "completion_tokens": 1}),
    final_response("done", {"prompt_tokens": 8, "completion_tokens": 2}),
]


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


def _engine(consumed: int) -> AgentLoop:
    """fresh engine + 从 consumed 处起播的 FakeProvider（等价断点后的模型侧）。"""
    return AgentLoop(
        provider=FakeProvider(SCRIPT[consumed:]), registry=_registry(), config=Config()
    )


def _initial_state() -> AgentState:
    return AgentState(
        run_id="replay",
        agent_name="t",
        status=AgentStatus.RUNNING,
        messages=[Message(role="user", content="question")],
    )


async def _full_run() -> list[tuple[Event, AgentState]]:
    """跑完整任务：逐事件连同其产生后的快照。"""
    return [pair async for pair in _engine(0).drive(_initial_state())]


def _boundary(pairs: list[tuple[Event, AgentState]], step: int) -> tuple[str, int]:
    """步 k 的断点：(k 步末尾的快照, k 步已产出的事件数)。"""
    snapshot = next(after for _, after in reversed(pairs) if after.step == step).snapshot()
    count = sum(1 for event, _ in pairs if event.step <= step)
    return snapshot, count


@pytest.mark.parametrize("boundary_step", [1, 2, 3])
async def test_replay_from_snapshot_boundary_is_event_identical(boundary_step: int) -> None:
    pairs = await _full_run()
    full_events = [event for event, _ in pairs]
    snapshot, event_count = _boundary(pairs, boundary_step)

    resumed = AgentState.loads(snapshot)
    tail_events = [event async for event, _ in _engine(boundary_step).drive(resumed)]

    assert tail_events == full_events[event_count:]


async def test_replay_final_state_equals_uninterrupted() -> None:
    pairs = await _full_run()
    uninterrupted = pairs[-1][1]

    resumed_state = AgentState.loads(_boundary(pairs, 1)[0])
    replayed: AgentState | None = None
    async for _, state in _engine(1).drive(resumed_state):
        replayed = state
    assert replayed is not None
    assert replayed.model_dump() == uninterrupted.model_dump()
    assert replayed.status is AgentStatus.SUCCEEDED


async def test_snapshot_roundtrip_preserves_messages_with_tool_calls() -> None:
    """重放依赖 assistant.tool_calls 的无损序列化（v1.1.0 Message 扩展）。"""
    pairs = await _full_run()
    resumed = AgentState.loads(_boundary(pairs, 1)[0])
    assistant = next(m for m in resumed.messages if m.role == "assistant")
    assert assistant.tool_calls[0].id == "c1"
