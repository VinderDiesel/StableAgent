"""C3/F3：pause/resume 接入点语义。"""

import pytest

from stableagent.core.state import AgentStatus, EventType
from tests.support import FakeProvider, final_response, tool_response
from tests.unit.test_loop_branches import collect, make_agent


def _provider() -> FakeProvider:
    return FakeProvider([tool_response("c1", "a"), tool_response("c2", "b"), final_response("end")])


async def test_pause_noop_when_not_running() -> None:
    agent = make_agent(_provider())
    assert agent.pause() is False  # 空闲态：显式 no-op（F3）
    await collect(agent)
    assert agent.pause() is False  # 结束态：同样 no-op


def test_pause_without_prior_run_raises_on_resume() -> None:
    agent = make_agent(_provider())
    with pytest.raises(ValueError, match="PAUSED"):
        agent.resume()


async def test_pause_takes_effect_at_step_boundary_and_resume_completes() -> None:
    agent = make_agent(_provider())
    events = []
    paused_requested = False
    async for event in agent.stream("question"):
        events.append(event)
        if event.type is EventType.TOOL_RESULT and not paused_requested:
            assert agent.pause() is True  # 运行中：置位成功
            paused_requested = True

    assert events[-1].type is EventType.PAUSED  # 步边界生效（F3）
    assert events[-1].data["step"] == 1
    assert agent._last_state is not None
    assert agent._last_state.status is AgentStatus.PAUSED

    with pytest.raises(RuntimeError, match="resume"):  # 暂停期间拒绝新任务
        await agent.arun("another task")

    final = await agent.aresume()
    assert final.status is AgentStatus.SUCCEEDED
    assert final.step == 3


async def test_pause_flag_consumed_once_then_second_run_unaffected() -> None:
    agent = make_agent(
        FakeProvider([tool_response("c1", "a"), final_response("x"), final_response("y")])
    )
    events = []
    async for event in agent.stream("q1"):
        events.append(event)
        if event.type is EventType.TOOL_RESULT:
            agent.pause()
    assert events[-1].type is EventType.PAUSED

    resumed = await agent.aresume()
    assert resumed.status is AgentStatus.SUCCEEDED

    second = await collect(agent, "q2")  # 陈旧标志已复位：不会被误暂停
    assert second[-1].type is EventType.DONE
