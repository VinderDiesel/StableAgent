"""B 节：loop 分支单测（无工具 / 多轮 / 提前终止 / 失败归一化 / 状态机 / F4）。"""

from collections.abc import Sequence
from typing import Any

from stableagent import Agent
from stableagent.core.state import (
    AgentStatus,
    Config,
    ControlConfig,
    Event,
    EventType,
)
from stableagent.tools.registry import ToolRegistry
from tests.support import BoomTool, EchoTool, FakeProvider, final_response, tool_response


def make_agent(
    provider: FakeProvider,
    tools: Sequence[type] = (EchoTool,),
    config: Config | None = None,
    **agent_kwargs: Any,
) -> Agent:
    registry = ToolRegistry()
    for tool_type in tools:
        registry.register(tool_type())
    return Agent(
        name="t",
        provider=provider,
        config=config or Config(),
        registry=registry,
        **agent_kwargs,
    )


async def collect(agent: Agent, task: str = "question") -> list[Event]:
    return [event async for event in agent.stream(task)]


async def test_b1_no_tools_single_step() -> None:
    agent = make_agent(FakeProvider([final_response("answer")]))
    events = await collect(agent)
    assert [e.type for e in events] == [EventType.THINKING, EventType.DONE]
    assert events[-1].data["result"] == "answer"
    assert agent._last_state is not None
    assert agent._last_state.status is AgentStatus.SUCCEEDED
    assert agent._last_state.step == 1


async def test_b2_multi_round_tool_pairs_and_message_pairing() -> None:
    provider = FakeProvider(
        [tool_response("c1", "one"), tool_response("c2", "two"), final_response("done")]
    )
    agent = make_agent(provider)
    events = await collect(agent)
    types = [e.type for e in events]
    assert types.count(EventType.TOOL_CALL) == 2
    assert types.count(EventType.TOOL_RESULT) == 2
    assert types[-1] is EventType.DONE
    assert types.count(EventType.DONE) == 1 and types.count(EventType.ERROR) == 0
    # step 单调不减
    assert all(a.step <= b.step for a, b in zip(events, events[1:], strict=False))
    # 工具消息与 ToolCall 配对（契约二不变式）
    assert agent._last_state is not None
    calls = [tc for m in agent._last_state.messages for tc in m.tool_calls]
    tool_msgs = [m for m in agent._last_state.messages if m.role == "tool"]
    assert {tc.id for tc in calls} == {m.tool_call_id for m in tool_msgs}


async def test_b3_max_steps_terminates_with_error() -> None:
    provider = FakeProvider([tool_response("c1", "one"), tool_response("c2", "two")])
    config = Config(control=ControlConfig(max_steps=1))
    agent = make_agent(provider, config=config)
    events = await collect(agent)
    terminals = [e for e in events if e.type in (EventType.DONE, EventType.ERROR)]
    assert len(terminals) == 1  # F4：恰好一个终止事件
    assert terminals[0].type is EventType.ERROR
    assert terminals[0] is events[-1]  # 且位于流末尾
    assert terminals[0].data["reason"] == "max_steps_exceeded"
    assert agent._last_state is not None
    assert agent._last_state.status is AgentStatus.FAILED
    assert "max_steps" in (agent._last_state.error or "")


async def test_b3_provider_exception_normalized_to_error_event() -> None:
    provider = FakeProvider([tool_response("c1", "one")])  # 第二轮脚本耗尽 -> IndexError
    agent = make_agent(provider)
    events = await collect(agent)
    assert events[-1].type is EventType.ERROR
    assert events[-1].data["reason"] == "provider_error"
    assert "IndexError" in events[-1].data["detail"]
    assert agent._last_state is not None
    assert agent._last_state.status is AgentStatus.FAILED


async def test_b4_tool_exception_feeds_model_not_crash() -> None:
    provider = FakeProvider(
        [
            tool_response("c1", "will fail"),
            final_response("recovered"),
        ]
    )
    registry = ToolRegistry()

    class BoomAsEcho(BoomTool):
        name = "echo"

    registry.register(BoomAsEcho())
    agent = Agent(name="t", provider=provider, config=Config(), registry=registry)
    events = await collect(agent)
    result_event = next(e for e in events if e.type is EventType.TOOL_RESULT)
    assert result_event.data["evidence"]["ok"] is False
    assert "RuntimeError" in result_event.data["evidence"]["error"]
    assert events[-1].type is EventType.DONE  # loop 未崩溃，正常收敛
    # 归一化结果作为 role=tool 消息回喂模型（B4 消费证明）
    second_call_messages = provider.calls[1][0]
    tool_msg = next(m for m in second_call_messages if m.role == "tool")
    assert "RuntimeError" in tool_msg.content


async def test_b5_success_state_machine_path() -> None:
    provider = FakeProvider([tool_response("c1", "x"), final_response()])
    agent = make_agent(provider)
    await collect(agent)
    assert agent._last_state is not None
    assert agent._last_state.status is AgentStatus.SUCCEEDED
    assert agent._last_state.step == 2
    assert agent._last_state.error is None


def test_event_type_set_includes_p1_additions() -> None:
    assert {t.value for t in EventType} >= {"thinking", "paused", "done", "error"}
