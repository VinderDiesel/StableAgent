"""A/C/D 节：Agent 外层的 checkpoint 注入、失败自愈与跨实例恢复（全离线）。"""

from __future__ import annotations

from typing import Any

from stableagent import Agent
from stableagent.core.checkpoint.storage import MemoryCheckpointStore
from stableagent.core.state import (
    TERMINAL_EVENTS,
    AgentState,
    AgentStatus,
    Config,
    Event,
    EventType,
)
from stableagent.tools.registry import ToolRegistry
from tests.support import (
    EchoTool,
    FakeProvider,
    FakeRedisClient,
    FlakyProvider,
    final_response,
    tool_response,
)

TASK = "问题"


def make_agent(
    provider: Any,
    *,
    config: Config | None = None,
    store: MemoryCheckpointStore | None = None,
    sleep: Any = None,
    fallback: Any = None,
    tools: tuple[type, ...] = (EchoTool,),
) -> Agent:
    registry = ToolRegistry()
    for tool_type in tools:
        registry.register(tool_type())
    return Agent(
        name="t",
        provider=provider,
        config=config or Config(),
        registry=registry,
        checkpoint_store=store if store is not None else MemoryCheckpointStore(),
        sleep=sleep or _no_sleep,
        fallback=fallback,
    )


async def _no_sleep(seconds: float) -> None:
    return None


def checkpoint_config(**overrides: Any) -> Config:
    base: dict[str, Any] = {
        "checkpoint": {"backend": "memory", "auto_save_every": 1, "recovery_strategy": "none"},
        "control": {"max_steps": 10},
    }
    base["checkpoint"].update(overrides)
    return Config.model_validate(base)


async def collect(agent: Agent, task: str = TASK) -> list[Event]:
    return [event async for event in agent.stream(task)]


def types_of(events: list[Event]) -> list[EventType]:
    return [event.type for event in events]


# ------------------------------------------------------------------ checkpoint 注入


async def test_checkpoint_events_land_at_step_boundaries_and_never_after_done() -> None:
    agent = make_agent(
        FakeProvider([tool_response("c1", "a"), tool_response("c2", "b"), final_response("done")]),
        config=checkpoint_config(),
    )
    events = await collect(agent)
    types = types_of(events)

    assert types[-1] is EventType.DONE, "F4：注入 checkpoint 后终止事件仍在末尾"
    assert types.count(EventType.CHECKPOINT) == 2, "第 1、2 步各一次；终止步静默落盘"
    assert types[:6] == [
        EventType.THINKING,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.CHECKPOINT,
        EventType.THINKING,
        EventType.TOOL_CALL,
    ]
    checkpoints = [event for event in events if event.type is EventType.CHECKPOINT]
    assert [event.step for event in checkpoints] == [1, 2]


async def test_terminal_snapshot_is_persisted_silently() -> None:
    store = MemoryCheckpointStore()
    agent = make_agent(
        FakeProvider([tool_response("c1", "a"), final_response("ok")]),
        config=checkpoint_config(),
        store=store,
    )
    await collect(agent)
    run_id = agent.last_state.run_id
    records = await store.records(run_id)
    assert [record.step for record in records] == [1, 2]
    final = AgentState.loads(records[-1].payload)
    assert final.status is AgentStatus.SUCCEEDED


async def test_auto_save_every_two_saves_only_even_steps() -> None:
    agent = make_agent(
        FakeProvider([tool_response("c1", "a"), tool_response("c2", "b"), final_response("done")]),
        config=checkpoint_config(auto_save_every=2),
    )
    events = await collect(agent)
    checkpoints = [event.step for event in events if event.type is EventType.CHECKPOINT]
    assert checkpoints == [2]  # 第 1 步不满足取模判据


async def test_no_store_injected_and_no_config_means_no_checkpoint_events() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(
        name="t",
        provider=FakeProvider([final_response("ok")]),
        config=Config(),
        registry=registry,
    )
    events = [event async for event in agent.stream(TASK)]
    assert types_of(events) == [EventType.THINKING, EventType.DONE]
    assert agent.checkpoints is None


# ------------------------------------------------------------------- D 节暂停语义


async def test_d1_pause_persists_and_resume_completes() -> None:
    store = MemoryCheckpointStore()
    agent = make_agent(
        FakeProvider([tool_response("c1", "a"), final_response("final")]),
        config=checkpoint_config(),
        store=store,
    )
    events = []
    async for event in agent.stream(TASK):
        events.append(event)
        if event.type is EventType.TOOL_RESULT:
            assert agent.pause() is True

    assert types_of(events)[-1] is EventType.PAUSED
    assert events[-2].type is EventType.CHECKPOINT, "暂停点必落盘且发事件（D1）"
    assert store._data and all(  # 落盘的快照可重建
        AgentState.loads(record.payload).status is AgentStatus.PAUSED
        for record in await store.records(agent.last_state.run_id)
        if record.step == agent.last_state.step
    )

    resumed = await agent.aresume()
    assert resumed.status is AgentStatus.SUCCEEDED
    assert resumed.run_id == agent.last_state.run_id


async def test_d2_cross_instance_recover_from_store() -> None:
    store = MemoryCheckpointStore()
    first = make_agent(
        FakeProvider([tool_response("c1", "a"), tool_response("c2", "b")]),
        config=checkpoint_config(),
        store=store,
    )
    events = []
    async for event in first.stream(TASK):
        events.append(event)
        if event.type is EventType.TOOL_RESULT:
            first.pause()
    run_id = first.last_state.run_id
    assert first.last_state.status is AgentStatus.PAUSED

    fresh = make_agent(
        FakeProvider([tool_response("c3", "c"), final_response("由新实例完成")]),
        config=checkpoint_config(),
        store=store,
    )
    recovered = [event async for event in fresh.stream_recover(run_id)]
    assert types_of(recovered)[-1] is EventType.DONE
    assert fresh.last_state.step > first.last_state.step, "从断点续跑而非从头再来"
    assert fresh.last_state.messages[: len(first.last_state.messages)] == (
        first.last_state.messages
    )


SCRIPT = [tool_response("c1", "a"), tool_response("c2", "b"), final_response("最终答案")]


def clean(events: list[Event]) -> list[Event]:
    """只留业务事件：P2 注入的 checkpoint/retry 与暂停标记本身都不参与逐条比对。"""
    injected = {EventType.CHECKPOINT, EventType.RETRY, EventType.PAUSED}
    return [event for event in events if event.type not in injected]


async def test_d4_recovered_stream_matches_uninterrupted_stream_event_by_event() -> None:
    """DoD D4：中断 + 跨实例恢复后的流，逐条等于一次跑完的流（仅多出注入事件）。"""
    store = MemoryCheckpointStore()
    uninterrupted = await collect(
        make_agent(FakeProvider(list(SCRIPT)), config=checkpoint_config(), store=store)
    )

    first = make_agent(FakeProvider(list(SCRIPT[:1])), config=checkpoint_config(), store=store)
    head: list[Event] = []
    async for event in first.stream(TASK):
        head.append(event)
        if event.type is EventType.TOOL_RESULT:
            assert first.pause() is True
    run_id = first.last_state.run_id
    consumed = sum(1 for message in first.last_state.messages if message.role == "assistant")
    assert consumed == 1, "暂停在步边界：第 1 步已完整结束"

    tail = [
        event
        async for event in make_agent(
            FakeProvider(list(SCRIPT[consumed:])), config=checkpoint_config(), store=store
        ).stream_recover(run_id)
    ]
    assert clean(head) + clean(tail) == clean(uninterrupted)
    assert sum(1 for e in head + tail if e.type in TERMINAL_EVENTS) == 1, "F4 跨段仍成立"


async def test_b3_redis_backend_works_at_agent_level_with_injected_client() -> None:
    """DoD B3 的接线证明：backend=redis + 注入 fake client，暂停/恢复整条链路可用。"""
    registry = ToolRegistry()
    registry.register(EchoTool())
    config = checkpoint_config(backend="redis", redis_key_prefix="sa:demo")
    client = FakeRedisClient()

    def build(responses: list[Any]) -> Agent:
        return Agent(
            name="t",
            provider=FakeProvider(responses),
            config=config,
            registry=registry,
            redis_client=client,
            sleep=_no_sleep,
        )

    first = build(list(SCRIPT[:1]))
    head: list[Event] = []
    async for event in first.stream(TASK):
        head.append(event)
        if event.type is EventType.TOOL_RESULT:
            first.pause()
    run_id = first.last_state.run_id
    assert list(client.data) == [f"sa:demo:{run_id}"], "key 规则可预测，不污染他人空间"

    fresh = build(list(SCRIPT[1:]))
    recovered = [event async for event in fresh.stream_recover(run_id)]
    assert types_of(recovered)[-1] is EventType.DONE
    assert fresh.last_state.step > first.last_state.step
    assert clean(head) + clean(recovered) == clean(
        [e async for e in build(list(SCRIPT)).stream(TASK)]  # 与不中断跑完一致
    )


async def test_recover_on_finished_run_is_noop() -> None:
    store = MemoryCheckpointStore()
    agent = make_agent(
        FakeProvider([final_response("ok")]), config=checkpoint_config(), store=store
    )
    await collect(agent)
    run_id = agent.last_state.run_id
    again = await agent.arecover(run_id)
    assert again.status is AgentStatus.SUCCEEDED


# ------------------------------------------------------------------- C 节失败自愈


async def test_c2_retryable_error_retries_then_succeeds() -> None:
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    provider = FlakyProvider(
        [tool_response("c1", "a"), final_response("重试后成功")],
        fail_times=1,
        exc=TimeoutError("gateway timeout"),
    )
    agent = make_agent(
        provider,
        config=checkpoint_config(recovery_strategy="retry_with_fallback"),
        sleep=record_sleep,
    )
    events = await collect(agent)

    assert types_of(events).count(EventType.RETRY) == 1
    assert types_of(events)[-1] is EventType.DONE
    assert slept == [1.0], "指数退避第 1 次重试等 base"
    assert agent.last_state.status is AgentStatus.SUCCEEDED
    retry = next(event for event in events if event.type is EventType.RETRY)
    assert retry.data["error_class"] == "retryable"


async def test_c2_exhausted_retries_end_with_error_at_stream_tail() -> None:
    provider = FlakyProvider(
        [final_response("never reached")], fail_times=5, exc=TimeoutError("一直超时")
    )
    agent = make_agent(
        provider,
        config=checkpoint_config(recovery_strategy="retry_with_fallback", retry_max_attempts=2),
    )
    events = await collect(agent)
    assert types_of(events).count(EventType.RETRY) == 1
    assert types_of(events)[-1] is EventType.ERROR
    assert agent.last_state.status is AgentStatus.FAILED
    assert [event for event in events if event.type in TERMINAL_EVENTS] == [events[-1]]


async def test_fallback_provider_is_used_on_retry() -> None:
    primary = FlakyProvider([final_response("不该出现")], fail_times=3, exc=ConnectionError("拒连"))
    backup = FakeProvider([final_response("备胎完成")])
    agent = make_agent(
        primary,
        config=checkpoint_config(recovery_strategy="retry_with_fallback", retry_max_attempts=3),
        fallback=backup,
    )
    events = await collect(agent)
    assert types_of(events)[-1] is EventType.DONE
    assert agent.last_state.status is AgentStatus.SUCCEEDED
    assert backup.calls, "第 1 次重试切到 fallback provider"
    retry = next(event for event in events if event.type is EventType.RETRY)
    assert retry.data["provider_index"] == 1  # 首次重试即用备胎（pool 轮转）


async def test_c6_needs_human_error_pauses_for_operator() -> None:
    store = MemoryCheckpointStore()
    provider = FlakyProvider(
        [final_response("修好后继续")], fail_times=1, exc=PermissionError("需要人工授权")
    )
    agent = make_agent(
        provider,
        config=checkpoint_config(recovery_strategy="resume"),
        store=store,
    )
    events = await collect(agent)
    assert types_of(events)[-1] is EventType.PAUSED
    assert events[-1].data["reason"] == "needs_human"
    assert agent.last_state.status is AgentStatus.PAUSED
    assert "PermissionError" in agent.last_state.error

    final = await agent.aresume()
    assert final.status is AgentStatus.SUCCEEDED


async def test_non_retryable_error_fails_without_retrying() -> None:
    provider = FlakyProvider([final_response("ok")], fail_times=1, exc=ValueError("参数不合法"))
    agent = make_agent(
        provider,
        config=checkpoint_config(recovery_strategy="retry_with_fallback"),
    )
    events = await collect(agent)
    assert EventType.RETRY not in types_of(events)
    assert types_of(events)[-1] is EventType.ERROR


async def test_b5_checkpoint_store_outage_does_not_break_the_run() -> None:
    class Broken(MemoryCheckpointStore):
        async def save(self, record: Any) -> None:
            raise OSError("远端 Redis 挂了")

    agent = make_agent(
        FakeProvider([tool_response("c1", "a"), final_response("照样跑完")]),
        config=checkpoint_config(),
        store=Broken(),
    )
    events = await collect(agent)
    assert types_of(events)[-1] is EventType.DONE
    assert EventType.CHECKPOINT not in types_of(events)
    assert agent.last_state.status is AgentStatus.SUCCEEDED
