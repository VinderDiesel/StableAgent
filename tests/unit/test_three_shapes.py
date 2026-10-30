"""A3：三调用形态等价——run（sync）/ arun / stream 在同一脚本下产出一致。"""

from stableagent import Agent
from stableagent.core.state import AgentState, EventType
from tests.support import FakeProvider, final_response, tool_response
from tests.unit.test_loop_branches import collect, make_agent


def _comparable(state: AgentState) -> dict:
    return state.model_dump(exclude={"run_id"})


async def _fresh_agent() -> Agent:
    provider = FakeProvider(
        [tool_response("c1", "a"), tool_response("c2", "b"), final_response("end")]
    )
    return make_agent(provider)


async def test_stream_and_arun_produce_identical_events_and_state() -> None:
    agent_a = await _fresh_agent()
    events = await collect(agent_a)
    state_a = agent_a._last_state

    agent_b = await _fresh_agent()
    state_b = await agent_b.arun("question")

    assert state_a is not None and state_b is not None
    assert _comparable(state_a) == _comparable(state_b)
    streamed = [e for e in events if e.type is not EventType.PAUSED]
    assert streamed[-1].type is EventType.DONE


def test_sync_run_matches_scripted_events() -> None:
    provider = FakeProvider(
        [tool_response("c1", "a"), tool_response("c2", "b"), final_response("end")]
    )
    agent = make_agent(provider)
    state = agent.run("question")  # 同步入口（内部 asyncio.run）
    assert state.status.value == "succeeded"
    assert state.step == 3
    assert [m.role for m in state.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
