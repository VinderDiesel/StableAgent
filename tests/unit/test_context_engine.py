"""D 节：预算治理。一个引擎服务两个位点、边界语义、三态阶梯、reject 的终止语义。

计数用 `FlatCounter`（一条消息 10、一个规格 20），D2 的边界才写得成整数而不是
"启发式恰好算出这个数"。真实启发式的量级差异由 A3 的对照测试负责。
"""

from typing import Any

import pytest

from stableagent.context.budget import BudgetDecision, ContextReport, count_orphan_tool_calls
from stableagent.context.capability import CapabilityReport
from stableagent.context.compression import is_summary_message
from stableagent.context.engine import ContextEngine, EngineAssembler, Plan
from stableagent.context.tokens import HeuristicTokenCounter
from stableagent.core.checkpoint.recovery import migrate_state
from stableagent.core.checkpoint.storage import MemoryCheckpointStore
from stableagent.core.state import (
    AgentState,
    AgentStatus,
    CheckpointConfig,
    Config,
    ContextConfig,
    ControlConfig,
    EventType,
    Message,
    ToolCall,
)
from stableagent.providers.base import ProviderResponse
from tests.support import FakeProvider, final_response
from tests.unit.test_capability import names_of, specs, stub_types
from tests.unit.test_compression import corpus
from tests.unit.test_loop_branches import collect, make_agent

PER_MESSAGE = 10
PER_SPEC = 20
TASK = "run a SQL query on the warehouse"
# 相关性视图在 TASK 下命中 list_runs + query_sql（见 C1），所以发出 2 个规格而不是 10 个
FULL_VIEW = 7 * PER_MESSAGE + 10 * PER_SPEC  # 不组装不裁剪：270
SHARED_VIEW = 7 * PER_MESSAGE + 2 * PER_SPEC  # 默认计划（keep 8 / cap 8）实际发出：110
# 新任务的第一帧：1 条 user 消息 + 2 个规格
FIRST_VIEW = 1 * PER_MESSAGE + 2 * PER_SPEC


class FlatCounter:
    """定长计数器：预算边界、阶梯层数、before/after 都能写成整数断言。"""

    def count(self, text: str) -> int:
        return len(text)

    def count_messages(self, messages: list[Message]) -> int:
        return PER_MESSAGE * len(messages)

    def count_tools(self, selected: list[dict[str, Any]]) -> int:
        return PER_SPEC * len(selected)


def engine_for(**overrides: Any) -> ContextEngine:
    config = ContextConfig(
        compression="truncation",
        capability_filter="relevance",
        **overrides,
    )
    return ContextEngine(config, token_counter=FlatCounter())


def loaded(turns: int = 3, step: int = 0) -> AgentState:
    """一个"已经跑了几轮"的快照：7 条消息（1 user + 3 轮工具往返）。

    任务文本换成英文的 TASK——`corpus()` 的中文任务在这 10 个英文工具描述上一个词都
    不命中，会走 C4 的全量兜底，那时 `SHARED_VIEW` 就不是 110 而是 270 了。
    """
    messages = corpus(turns)
    messages[0] = Message(role="user", content=TASK)
    return AgentState(
        run_id="r",
        agent_name="a",
        status=AgentStatus.RUNNING,
        step=step,
        messages=messages,
    )


def call(*names: str, usage: int | None = None) -> ProviderResponse:
    """带 N 个工具调用的响应（一步之内：一条 assistant + N 条 tool）。"""
    tool_calls = [
        ToolCall(id=f"c{index}-{name}", name=name, arguments={}) for index, name in enumerate(names)
    ]
    return ProviderResponse(
        content=f"thinking {names}",
        tool_calls=tool_calls,
        usage={} if usage is None else {"prompt_tokens": usage},
    )


def context_config(**context: Any) -> Config:
    return Config(context=ContextConfig(**context))


def budget_agent(provider: FakeProvider, on_budget_exceeded: str, limit: int) -> Any:
    """动态视图 + 截断压缩 + 内存 checkpoint 的三态通用装配（预算相关参数由调用方给）。"""
    return make_agent(
        provider,
        tools=stub_types(),
        config=Config(
            checkpoint=CheckpointConfig(auto_save_every=1),
            context=ContextConfig(
                compression="truncation",
                capability_filter="relevance",
                max_context_tokens=limit,
                on_budget_exceeded=on_budget_exceeded,
            ),
        ),
        token_counter=FlatCounter(),
    )


# ------------------------------------------------------------------- D1 一个引擎两个位点


async def test_d1_the_same_engine_serves_the_loop_and_the_outer_layer() -> None:
    provider = FakeProvider([final_response("ok")])
    agent = make_agent(
        provider,
        tools=stub_types(),
        config=context_config(max_context_tokens=SHARED_VIEW, capability_filter="relevance"),
        token_counter=FlatCounter(),
    )
    await collect(agent, TASK)
    engine = agent._context
    assert engine is not None
    assert isinstance(agent._assembler, EngineAssembler)
    assert agent._assembler._engine is engine  # 组装位点用的就是这个实例
    selector = agent._tool_selector
    assert selector is not None and getattr(selector, "__self__", None) is engine
    # 外层判的量必须等于真发到线上的量，否则"入场时以为没超"就是自欺
    sent_messages, sent_specs = provider.calls[0][0], provider.calls[0][1] or []
    assert engine.estimate(sent_messages, sent_specs) == FIRST_VIEW
    assert agent.context_reports[-1].estimated_prompt_tokens == engine.estimate(
        sent_messages, sent_specs
    )


def test_d1_engine_exposes_both_loop_adapters() -> None:
    engine = engine_for(max_context_tokens=10_000)
    assembler = EngineAssembler(engine)
    state = loaded()
    assert assembler.assemble(state.messages) == engine.assemble(state.messages)
    assert engine.select(specs(), state) == engine.select(specs(), state)
    assert isinstance(engine.capability_report, CapabilityReport)


# ---------------------------------------------------------------------- D2 判据与边界


def test_d2_estimate_is_the_payload_about_to_be_sent_not_the_history_length() -> None:
    engine = ContextEngine(ContextConfig(), token_counter=FlatCounter())  # 静态视图 + 不压缩
    state = loaded()
    assert engine.estimate(state.messages, specs()) == FULL_VIEW
    decision = engine.preflight(state, specs())
    assert decision.estimate == FULL_VIEW  # 判据含工具规格（A2），不只是历史条数
    assert decision.limit is None and decision.within_budget is True


@pytest.mark.parametrize("limit,within", [(SHARED_VIEW, True), (SHARED_VIEW - 1, False)])
def test_d2_hard_limit_boundary_is_inclusive(limit: int, within: bool) -> None:
    """恰好等于上限不动作；比上限多 1 才触发。`<=` 写成 `<` 就会在这里露出来。"""
    engine = engine_for(max_context_tokens=limit, on_budget_exceeded="reject")
    decision = engine.preflight(loaded(), specs())
    assert decision.estimate == SHARED_VIEW
    assert decision.within_budget is within
    assert decision.action == ("none" if within else "reject")


def test_d2_select_side_records_the_same_verdict() -> None:
    for limit, within in ((SHARED_VIEW, True), (SHARED_VIEW - 1, False)):
        engine = engine_for(max_context_tokens=limit, on_budget_exceeded="compress")
        state = loaded(step=4)
        engine.assemble(state.messages)
        engine.select(specs(), state)
        report = engine.last_report
        assert report is not None
        assert (report.within_budget, report.after, report.before) == (
            within,
            SHARED_VIEW,
            FULL_VIEW,
        )
        assert report.action == ("none" if within else "compress")


# ------------------------------------------------------------------------ D3 compress


def test_d3_compress_shrinks_history_before_touching_the_tool_view() -> None:
    engine = engine_for(max_context_tokens=95, on_budget_exceeded="compress")
    decision = engine.preflight(loaded(), specs())
    assert decision.within_budget is True and decision.levels_tried == 2
    assert engine.plan == Plan(keep_recent=4, max_visible_tools=8)  # 历史动了，视图没动
    assert "plan=" in decision.detail


def test_d3_escalated_plan_applies_to_the_next_step() -> None:
    engine = engine_for(max_context_tokens=95, on_budget_exceeded="compress")
    state = loaded(step=2)
    assert len(engine.assemble(state.messages)) == 7  # 默认窗口比历史还长：没折叠
    engine.select(specs(), state)
    first = engine.last_report
    assert first is not None and (first.within_budget, first.levels_tried) == (False, 2)
    assert first.levels == ()  # 默认窗口比历史还长，这一帧什么都没折

    view = engine.assemble(state.messages)  # 第 2 步才用上更紧的计划（一步滞后是有意的）
    assert len(view) == 5 and view[0] == state.messages[0]  # 任务本体常驻（B1）
    # truncation 的取舍就是"不生成摘要"（B1）：早期轮次真的消失，不是换成一条摘要
    assert not any(is_summary_message(m) for m in view)
    assert all("FACT-0" not in m.content for m in view) and any("FACT-2" in m.content for m in view)
    kept = engine.select(specs(), state.model_copy(update={"step": 3}))
    second = engine.last_report
    assert second is not None
    assert (second.after, second.within_budget, second.levels) == (90, True, ("truncation",))
    assert len(kept) == 2 == second.tools_kept


def test_d3_compress_exhausts_when_the_floor_is_still_over_the_limit() -> None:
    engine = engine_for(max_context_tokens=49, on_budget_exceeded="compress")  # 地板是 50
    decision = engine.preflight(loaded(), specs())
    assert decision.within_budget is False and decision.exhausted is True
    assert decision.levels_tried == len(engine._ladder()) == 7
    assert "未达标" in decision.detail
    assert engine.plan == Plan(keep_recent=8, max_visible_tools=8)  # 不达标就不改计划


def test_d3_ladder_is_bounded_deterministic_and_ordered() -> None:
    engine = engine_for(max_context_tokens=1)
    assert engine.plan == Plan(keep_recent=8, max_visible_tools=8)  # 起点就是配置值
    steps = engine._ladder()
    assert [(p.keep_recent, p.max_visible_tools) for p in steps] == [
        (8, 8),
        (4, 8),
        (2, 8),
        (1, 8),
        (1, 4),
        (1, 2),
        (1, 1),
    ]
    assert steps == engine_for(max_context_tokens=1)._ladder()  # 换实例同样序列


# ------------------------------------------------------------------------- D4 degrade


def test_d4_degrade_tightens_the_view_before_shrinking_history() -> None:
    engine = engine_for(max_context_tokens=95, on_budget_exceeded="degrade")
    decision = engine.preflight(loaded(), specs())
    assert decision.within_budget is True and decision.levels_tried == 4
    assert engine.plan == Plan(keep_recent=8, max_visible_tools=1)  # 历史一条没压
    # 同一份语料、同一个上限：D3 动的是历史，D4 动的是视图——顺序就是策略
    compressing = engine_for(max_context_tokens=95, on_budget_exceeded="compress")
    assert compressing.preflight(loaded(), specs()).levels_tried != decision.levels_tried


def test_d4_report_makes_every_level_readable() -> None:
    engine = engine_for(max_context_tokens=95, on_budget_exceeded="degrade")
    state = loaded(step=6)
    engine.assemble(state.messages)
    engine.select(specs(), state)
    report = engine.last_report
    assert report is not None
    assert (report.action, report.levels_tried, report.exhausted) == ("degrade", 4, False)
    assert (report.before, report.after, report.limit, report.step) == (
        FULL_VIEW,
        SHARED_VIEW,
        95,
        6,
    )
    assert (report.tools_total, report.tools_kept) == (10, 2)
    assert report.tools_dropped_tokens > 0 and report.orphans == 0


async def test_d4_mid_run_degrade_reaches_the_next_provider_call() -> None:
    provider = FakeProvider([call("read_file"), call("write_file"), final_response("done")])
    agent = budget_agent(provider, "degrade", limit=70)
    await collect(agent, TASK)
    # 第 1 帧：视图命中两个相关工具，还没超限
    assert names_of(provider.calls[0][1] or []) == ["list_runs", "query_sql"]
    # 第 2 帧：超限发生在这一帧的 select，所以发出去的还是旧视图（一步滞后）
    assert names_of(provider.calls[1][1] or []) == ["list_runs", "query_sql", "read_file"]
    # 第 3 帧才用上收紧后的视图：没调用过的相关工具被挤掉，调用过的常驻保留（C3）
    assert names_of(provider.calls[2][1] or []) == ["read_file", "write_file"]
    assert [r.action for r in agent.context_reports] == ["none", "degrade", "degrade"]
    # 第 2 帧靠收紧视图到达标（3 档），第 3 帧视图已经收紧到常驻底线，只能再往历史挪（6 档）
    assert [r.levels_tried for r in agent.context_reports] == [0, 3, 6]
    assert agent.context_reports[-1].exhausted is False


# -------------------------------------------------------------------------- D5 reject


async def test_d5_reject_terminates_at_entry_without_touching_the_provider() -> None:
    provider = FakeProvider([final_response("never")])
    agent = budget_agent(provider, "reject", limit=FIRST_VIEW - 1)
    events = await collect(agent, TASK)
    assert [e.type for e in events] == [EventType.ERROR]  # L2：唯一终止事件，且在末尾（F4）
    terminal = events[-1]
    assert terminal.data["reason"] == "budget_exceeded"
    assert terminal.data["estimate"] == FIRST_VIEW > terminal.data["limit"]
    assert "on_budget_exceeded=reject" in terminal.data["detail"]  # 说得清是谁拦的
    assert provider.calls == []  # 一次模型调用都没发生，也就没有"重试再判一遍"


def test_d5_reject_does_not_silently_degrade_quality_at_entry() -> None:
    """同一份上下文：compress 会压到达标，reject 不替用户降质量——入场即拒。"""
    compressing = engine_for(max_context_tokens=95, on_budget_exceeded="compress")
    rejecting = engine_for(max_context_tokens=95, on_budget_exceeded="reject")
    assert compressing.preflight(loaded(), specs()).within_budget is True
    decision = rejecting.preflight(loaded(), specs())
    assert (decision.within_budget, decision.action, decision.levels_tried) == (False, "reject", 0)
    assert rejecting.plan == Plan(keep_recent=8, max_visible_tools=8)


async def test_d5_reject_persists_a_failed_snapshot() -> None:
    store = MemoryCheckpointStore()
    agent = make_agent(
        FakeProvider([final_response("never")]),
        tools=stub_types(),
        config=Config(
            checkpoint=CheckpointConfig(auto_save_every=1),
            context=ContextConfig(
                compression="truncation",
                capability_filter="relevance",
                max_context_tokens=FIRST_VIEW - 1,
                on_budget_exceeded="reject",
            ),
        ),
        checkpoint_store=store,
        token_counter=FlatCounter(),
    )
    await collect(agent, TASK)
    assert agent.last_state is not None and agent.last_state.status is AgentStatus.FAILED
    records = await store.records(agent.last_state.run_id)
    assert len(records) == 1
    assert migrate_state(records[0].payload).status is AgentStatus.FAILED


async def test_d5_mid_run_exhaustion_pauses_at_the_step_boundary() -> None:
    """运行中不做紧急制动：本步照跑，下一步开头按 F3 暂停（与 needs_human 同形）。"""
    provider = FakeProvider(
        [call("read_file", "write_file"), call("read_file"), final_response("unused")]
    )
    agent = budget_agent(provider, "reject", limit=60)
    events = await collect(agent, TASK)
    assert (
        events[-1].type is EventType.PAUSED
        and [e.type for e in events].count(EventType.PAUSED) == 1
    )
    assert events[-1].data["reason"] == "budget_exceeded"
    assert len(provider.calls) == 2  # 超限在第 2 步的 select 判出，第 2 步该发的还是发了
    assert agent.last_state is not None and agent.last_state.status is AgentStatus.PAUSED
    assert "budget" in (agent.last_state.error or "")


async def test_d5_resuming_into_a_still_tight_budget_fails_instead_of_re_pausing() -> None:
    """暂停后原样续跑：预算仍装不下就给出确定失败，而不是"暂停→暂停→暂停"的空转。"""
    provider = FakeProvider([call("read_file", "write_file"), call("read_file")])
    agent = budget_agent(provider, "reject", limit=60)
    await collect(agent, TASK)
    assert agent.last_state is not None
    resumed = [event async for event in agent.stream_recover(agent.last_state.run_id)]
    assert [e.type for e in resumed] == [EventType.ERROR]
    assert resumed[-1].data["reason"] == "budget_exceeded"
    assert len(provider.calls) == 2  # 一条都没多花


async def test_d5_late_exhaustion_does_not_leave_the_loop_paused() -> None:
    """最后一步才判出超限时本步已 DONE：暂停标志不能漏给下一段刚开场就刹车。"""
    provider = FakeProvider([call("read_file", "write_file"), final_response("done")])
    agent = budget_agent(provider, "reject", limit=60)
    events = await collect(agent, TASK)
    assert events[-1].type is EventType.DONE
    assert agent._loop is not None and agent._loop.pause_requested is False


# ---------------------------------------------------------------------- D6 可观测性


async def test_d6_default_config_builds_no_engine_and_stays_silent() -> None:
    assert "context" not in {event.value for event in EventType}  # L6：没加枚举成员
    provider = FakeProvider([final_response("ok")])
    agent = make_agent(provider)
    assert agent._context is None
    assert agent.context_reports == [] and agent.capability_report is None
    events = await collect(agent)
    assert [e.type for e in events] == [EventType.THINKING, EventType.DONE]  # L4：与 P2 逐条一致
    assert len(provider.calls[0][1] or []) == 1  # 全量规格，一条没裁


async def test_d6_reports_are_one_per_step_and_the_property_is_a_copy() -> None:
    provider = FakeProvider([call("read_file"), call("write_file"), final_response("done")])
    agent = make_agent(
        provider,
        tools=stub_types(),
        config=context_config(max_context_tokens=10_000, capability_filter="relevance"),
        token_counter=FlatCounter(),
    )
    await collect(agent, TASK)
    reports = agent.context_reports
    assert len(reports) == 3
    assert [r.step for r in reports] == [0, 1, 2]  # 记的是"发出时已完成几步"
    assert all(r.action == "none" and r.within_budget for r in reports)  # 宽松上限：只记录不动作
    reports.append(ContextReport())
    assert len(agent.context_reports) == 3  # 拿到的是副本，改不动引擎的事实源


# --------------------------------------------------------------- D7 与步数控制的关系


async def test_d7_max_steps_still_terminates_when_the_budget_is_ample() -> None:
    provider = FakeProvider([call("read_file"), call("write_file")])
    config = Config(
        control=ControlConfig(max_steps=1),
        context=ContextConfig(max_context_tokens=10_000, capability_filter="relevance"),
    )
    agent = make_agent(provider, tools=stub_types(), config=config, token_counter=FlatCounter())
    events = await collect(agent, TASK)
    assert events[-1].data["reason"] == "max_steps_exceeded"  # F2 没被预算路径掩盖
    assert all(r.action == "none" and r.within_budget for r in agent.context_reports)


async def test_d7_budget_and_max_steps_are_two_separate_terminations() -> None:
    """同一份脚本、同样 max_steps=1，只改预算上限：终止原因从 max_steps 变成 budget。"""
    loose_provider = FakeProvider([call("read_file")])
    tight_provider = FakeProvider([call("read_file")])
    agents = [
        make_agent(
            loose_provider,
            tools=stub_types(),
            config=Config(
                control=ControlConfig(max_steps=1),
                context=ContextConfig(max_context_tokens=10_000, capability_filter="relevance"),
            ),
            token_counter=FlatCounter(),
        ),
        make_agent(
            tight_provider,
            tools=stub_types(),
            config=Config(
                control=ControlConfig(max_steps=1),
                context=ContextConfig(
                    max_context_tokens=1, capability_filter="relevance", on_budget_exceeded="reject"
                ),
            ),
            token_counter=FlatCounter(),
        ),
    ]
    reasons = []
    for agent in agents:
        events = await collect(agent, TASK)
        reasons.append(events[-1].data["reason"])
    assert reasons == ["max_steps_exceeded", "budget_exceeded"]
    assert len(loose_provider.calls) == 1 and tight_provider.calls == []  # 两条路径代价不同


# ------------------------------------------------------------------- A3/A4 度量与校准


def test_a3_the_injected_counter_is_the_one_that_decides() -> None:
    overrides = dict(
        max_context_tokens=500,
        on_budget_exceeded="reject",
        capability_filter="relevance",
        compression="truncation",
    )
    strict = ContextEngine(ContextConfig(**overrides), token_counter=HeuristicTokenCounter())
    loose = ContextEngine(ContextConfig(**overrides), token_counter=FlatCounter())
    # 同一份上下文、同一个上限：换成定长计数器就从"超限"变成"没超限"
    assert strict.preflight(loaded(), specs()).within_budget is False
    assert loose.preflight(loaded(), specs()).within_budget is True


def test_a3_a_counter_missing_a_method_fails_early() -> None:
    class Broken:
        def count(self, text: str) -> int:
            return len(text)

    with pytest.raises(TypeError, match="缺少方法"):
        ContextEngine(ContextConfig(), token_counter=Broken())


async def test_a4_observed_usage_pairs_with_the_estimate_per_step() -> None:
    provider = FakeProvider(
        [
            call("read_file", usage=111),
            call("write_file", usage=222),
            final_response("done", usage={"prompt_tokens": 333}),
        ]
    )
    agent = make_agent(
        provider,
        tools=stub_types(),
        config=context_config(max_context_tokens=100_000, capability_filter="relevance"),
        token_counter=FlatCounter(),
    )
    await collect(agent, TASK)
    rows = [(r.estimated_prompt_tokens, r.observed_prompt_tokens) for r in agent.context_reports]
    # 观测值是"本步增量"而不是跨步累加：usage 累加 111/333/666，增量正好 111/222/333
    assert rows == [(FIRST_VIEW, 111), (3 * PER_MESSAGE + 3 * PER_SPEC, 222), (130, 333)]


async def test_a4_calibration_is_reporting_only_and_never_feeds_back() -> None:
    async def run(usages: list[int]) -> list[tuple[int, str, int]]:
        provider = FakeProvider(
            [
                call("read_file", usage=usages[0]),
                call("write_file", usage=usages[1]),
                final_response("done", usage={"prompt_tokens": usages[2]}),
            ]
        )
        agent = budget_agent(provider, "degrade", limit=60)
        await collect(agent, TASK)
        return [
            (r.estimated_prompt_tokens, r.action, r.levels_tried) for r in agent.context_reports
        ]

    honest = await run([1, 2, 3])
    lying = await run([99_999, 0, 12_345])  # Provider 撒谎也不得改变任何一次决策
    assert honest == lying


# --------------------------------------------------------------- L5 压缩产物进快照


async def test_l5_recovered_stream_with_compression_is_event_identical() -> None:
    """L5 的验收方式：开压缩，从落盘快照恢复，恢复流与不中断流逐条一致。

    根基是 `fold()`：摘要作为标记消息进了快照的 `messages`，恢复侧不必再调一次摘要器
    ——注入式模型每次文案都不同，等价性当场破产。
    """
    script = [call("read_file"), call("write_file"), call("read_file"), final_response("done")]
    config = Config(
        checkpoint=CheckpointConfig(auto_save_every=1),
        context=ContextConfig(compression="summary", keep_recent_messages=2),
    )
    store = MemoryCheckpointStore()
    steady = FakeProvider(script)
    uninterrupted = make_agent(
        steady,
        tools=stub_types(),
        config=config,
        checkpoint_store=store,
        token_counter=FlatCounter(),
    )
    events = await collect(uninterrupted, TASK)
    assert uninterrupted.last_state is not None
    run_id = uninterrupted.last_state.run_id
    records = await store.records(run_id)
    boundary = next(record for record in records if record.step == 2)
    assert not any(is_summary_message(m) for m in migrate_state(boundary.payload).messages)

    crashed = MemoryCheckpointStore()
    await crashed.save(boundary)
    restarted = FakeProvider(script[2:])
    recovered = make_agent(
        restarted,
        tools=stub_types(),
        config=config,
        checkpoint_store=crashed,
        token_counter=FlatCounter(),
    )
    tail = [
        (e.type, e.step, e.data)
        for e in events
        if e.step > 2 and e.type is not EventType.CHECKPOINT
    ]
    resumed = [
        (e.type, e.step, e.data)
        async for e in recovered.stream_recover(run_id)
        if e.type is not EventType.CHECKPOINT
    ]
    assert resumed == tail  # 逐条一致
    # 断点后第一帧的上下文与不中断那一帧完全相同：恢复没有多花一次摘要
    assert restarted.calls[0][0] == steady.calls[2][0]

    saved = await crashed.records(run_id)
    assert any(
        any(is_summary_message(m) for m in migrate_state(record.payload).messages)
        for record in saved
    )  # 摘要在快照里，不在进程内存里


def test_l5_fold_writes_the_summary_into_the_snapshot_and_is_idempotent() -> None:
    engine = ContextEngine(
        ContextConfig(compression="summary", keep_recent_messages=2),
        token_counter=FlatCounter(),
    )
    state = loaded()
    folded = engine.fold(state)
    assert folded is not state
    assert len(folded.messages) < len(state.messages)
    assert any(is_summary_message(m) for m in folded.messages)
    assert engine.fold(folded).messages == folded.messages  # 再折一次不重复摘要
    assert count_orphan_tool_calls(folded.messages) == 0  # E4：折叠没拆散配对


def test_l5_fold_is_a_no_op_when_compression_is_off() -> None:
    engine = ContextEngine(
        ContextConfig(compression="none", capability_filter="relevance", max_context_tokens=1),
        token_counter=FlatCounter(),
    )
    state = loaded()
    assert engine.fold(state) is state


def test_l5_folding_is_confluent_across_segment_boundaries() -> None:
    """分段折叠与一次性折叠结果相同——L5 的恢复等价性就靠这条合流性成立。

    它来自两个可检查的性质：抽取式摘要逐条生成、保序拼接；超长时 `_clamp` 保留尾部，
    而"尾的尾"还是"尾"。注入式模型摘要器没有这个性质（同一段历史两次文案不同），
    那也正是"摘要必须进快照"的理由：不复用落盘文案，恢复流不可能逐条一致。
    """
    config = ContextConfig(compression="summary", keep_recent_messages=2)

    def fold(messages: list[Message]) -> list[Message]:
        state = loaded().model_copy(update={"messages": messages})
        return ContextEngine(config, token_counter=FlatCounter()).fold(state).messages

    def summary(messages: list[Message]) -> str:
        return next(m.content for m in messages if is_summary_message(m))

    two_turns = corpus(2)
    four_turns = corpus(4)
    assert two_turns == four_turns[: len(two_turns)]  # 语料是同一前缀，比较才成立
    persisted = fold(two_turns)  # 段末落盘的快照
    incremental = fold([*persisted, *four_turns[len(two_turns) :]])  # 之后又跑了两轮
    assert summary(persisted) in summary(incremental)  # 已落盘的段落只被续写，不被改写
    assert incremental == fold(four_turns)  # 合流：分段先后不影响最终上下文


# --------------------------------------------------------------------- D8 三态齐全


@pytest.mark.parametrize(
    "action,expected_within",
    [("compress", True), ("degrade", True), ("reject", False)],
)
def test_d8_every_mode_has_a_determined_answer_at_the_same_limit(
    action: str, expected_within: bool
) -> None:
    """同一份上下文、同一个上限：三种动作的结论都必须确定且可断言（Roadmap 点名要求）。"""
    engine = engine_for(max_context_tokens=95, on_budget_exceeded=action)
    decision = engine.preflight(loaded(), specs())
    assert isinstance(decision, BudgetDecision)
    assert decision.within_budget is expected_within
    # 超限就记下要动用的动作，即使阶梯把它救回来了也仍然记（D4 可观测）
    assert decision.action == action
    assert decision.estimate == SHARED_VIEW


@pytest.mark.parametrize("action", ["compress", "degrade", "reject"])
def test_d8_every_mode_bottoms_out_deterministically(action: str) -> None:
    """达标路径之外，"压到底也不行"同样要有确定结论：三态各一条。"""
    limit = SHARED_VIEW - 1 if action == "reject" else 49  # 49 < 阶梯地板 50
    engine = engine_for(max_context_tokens=limit, on_budget_exceeded=action)
    decision = engine.preflight(loaded(), specs())
    assert decision.within_budget is False
    if action == "reject":
        assert decision.levels_tried == 0 and decision.exhausted is False
        assert engine.exhausted_reject is False  # 入场拦截由外层直接产生事件，不靠这个标志
    else:
        assert decision.levels_tried == 7 and decision.exhausted is True


def test_d8_reachability_of_the_escalated_plan_differs_by_action_not_by_data() -> None:
    """同一份数据、同一个上限：compress 与 degrade 都能达标，但落在不同的计划上。"""
    compressing = engine_for(max_context_tokens=95, on_budget_exceeded="compress")
    degrading = engine_for(max_context_tokens=95, on_budget_exceeded="degrade")
    assert compressing.preflight(loaded(), specs()).within_budget is True
    assert degrading.preflight(loaded(), specs()).within_budget is True
    assert compressing.plan != degrading.plan
    assert (compressing.plan, degrading.plan) == (
        Plan(keep_recent=4, max_visible_tools=8),
        Plan(keep_recent=8, max_visible_tools=1),
    )


# ---------------------------------------------------------------------- E4 截断安全性


@pytest.mark.parametrize("action", ["compress", "degrade", "reject"])
def test_e4_no_ladder_level_produces_an_orphan_tool_call(action: str) -> None:
    """E4：阶梯每一档都不许造出孤儿 `tool_call`。

    "为了达标把 tool 结果切走、留下一条没人应答的 assistant" 是预算治理最坏的收场：
    预算数字变好看了，请求本身却被 Provider 拒了。每次组装都算一遍（见
    `ContextReport.orphans`），这里对整条阶梯扫一遍。
    """
    engine = engine_for(max_context_tokens=1, on_budget_exceeded=action)
    state = loaded()
    ladder = engine._ladder()
    views = [engine._view_with(plan, state.messages) for plan in ladder]
    assert count_orphan_tool_calls(state.messages) == 0  # 语料本身干净，否则断言无意义
    assert min(len(view) for view in views) < len(state.messages)  # 确实折叠过
    for plan, view in zip(ladder, views, strict=True):
        assert count_orphan_tool_calls(view) == 0, plan


def test_e4_orphan_count_is_a_per_report_self_check_not_a_one_off() -> None:
    """`orphans` 是每步报告的字段，不是测试里临时算的量：坏消息一进语料就该被点名。"""
    engine = engine_for(max_context_tokens=10_000)
    engine.assemble(loaded().messages)
    engine.select(specs(), loaded())
    assert engine.reports[-1].orphans == 0
    broken = list(loaded().messages)
    broken.pop()  # 删掉最后一条 tool 结果，留下孤儿 assistant
    engine.assemble(broken)
    engine.select(specs(), loaded())
    assert engine.reports[-1].orphans == 1
