"""上下文引擎（P3 D1）：同一个实例同时服务 loop 位点与 Agent 外层。

为什么必须是一个实例：预算判据比较的是"这次真要发出去的东西"（组装后的消息 + 实际注入的
工具规格，D2）。外层算一套、位点算一套，就会出现"外层以为没超、发出去爆了"的经典裂缝。

时序上有两处不对称，是这个阶段绕不开的（L1 不许改 loop，`core/loop.py:60-61` 的调用顺序
是 assemble → select）：
- **历史压缩有一步滞后**：第 k 步末尾结算时才知道超没超，于是第 k+1 步才用上更紧的计划。
  控制回路里这叫带延迟的反馈，不是缺陷；`preflight()` 在入场时就把第一步的计划定好，
  滞后只发生在运行中的第 2 步之后。
- **运行中不做紧急制动**：`advance()` 把一步的工具副作用与全部事件一起做完才 yield，
  中途 break 会丢掉"已经执行但没有事件"的步。所以 reject 的终止语义只在入场时成立
  （外层命中即不调 `drive()`，D5）；运行中压到地板仍超限，则走 F3 的步边界暂停，
  语义与 P2 的 needs_human 同形。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from stableagent.context.assembly import ASSEMBLERS, ContextAssembler
from stableagent.context.budget import (
    BudgetDecision,
    ContextReport,
    count_orphan_tool_calls,
)
from stableagent.context.capability import (
    CapabilityFilter,
    CapabilityReport,
    resolve_capability_filter,
)
from stableagent.context.compression import ContextCompressor, Summariser, resolve_compression
from stableagent.context.tokens import TokenCounter, count_context, resolve_token_counter
from stableagent.core.state import AgentState, ContextConfig, Message

__all__ = ["ContextEngine", "EngineAssembler", "Plan"]

KEEP_FLOOR = 1
TOOLS_FLOOR = 1
LADDER_STEP = 2


class Plan(BaseModel):
    """一步的上下文预算计划：历史留多少条、工具给几个。frozen 才能当适配器缓存的键。"""

    model_config = ConfigDict(frozen=True)

    keep_recent: int
    max_visible_tools: int


class ContextEngine:
    def __init__(
        self,
        config: ContextConfig,
        *,
        token_counter: TokenCounter | None = None,
        summariser: Summariser | None = None,
    ) -> None:
        self._config = config
        self._counter = resolve_token_counter(token_counter)
        self._summariser = summariser
        self._base: ContextAssembler = ASSEMBLERS[config.assembly]()
        self._plan = Plan(
            keep_recent=config.keep_recent_messages, max_visible_tools=config.max_visible_tools
        )
        self._cache: dict[Plan, tuple[ContextCompressor | None, CapabilityFilter]] = {}
        self.reports: list[ContextReport] = []
        self.exhausted_reject = False
        self._view: tuple[list[Message], tuple[str, ...]] | None = None
        self._observed_baseline = 0
        self._observed_step = -1

    # ------------------------------------------------------------------ 只读视图

    @property
    def plan(self) -> Plan:
        return self._plan

    @property
    def last_report(self) -> ContextReport | None:
        return self.reports[-1] if self.reports else None

    @property
    def capability_report(self) -> CapabilityReport:
        """当前计划下能力视图的最近产出（C6 的计量在引擎开着时也从这里取，不留两份事实源）。"""
        return self._adapters(self._plan)[1].last_report

    def estimate(self, messages: list[Message], specs: list[dict[str, Any]] | None) -> int:
        """D2 的唯一量法：将要发出的消息 + 将要注入的工具规格。"""
        return count_context(self._counter, messages, specs or [])

    def fold(self, state: AgentState) -> AgentState:
        """L5：把压缩产物写回**快照**，不是只写回本次要发的上下文。

        落点在每段入口（`_pipeline` 开头）而不是 loop 位点：位点看不到也写不了快照，
        而注入式摘要器（真实模型）的输出若只活在进程里，崩溃恢复就必然重算一次、
        且文案不同——"恢复流与不中断流逐条一致"当场破产。
        载体是 `messages` 里那条 `name=context_summary` 的标记消息，不给 `AgentState`
        再加一份会与它漂移的副本。折叠不出新东西就原样返回，不制造无意义的副本。
        """
        compressor, _ = self._adapters(self._plan)
        if compressor is None:
            return state
        result = compressor.compress(state.messages)
        if result.kept == state.messages:
            return state
        return state.model_copy(update={"messages": result.kept})

    # ------------------------------------------------------------------ loop 位点

    def assemble(self, messages: list[Message]) -> list[Message]:
        base = self._base.assemble(messages)
        compressor, _ = self._adapters(self._plan)
        levels: tuple[str, ...] = ()
        if compressor is None:
            view = list(base)
        else:
            result = compressor.compress(base)
            view, levels = result.kept, result.levels
        self._view = (view, levels)
        return view

    def select(self, specs: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
        view, levels = self._view or (self._base.assemble(state.messages), ())
        _, capability = self._adapters(self._plan)
        kept = capability.select(specs, state)
        after = self.estimate(view, kept)
        before = self.estimate(self._base.assemble(state.messages), specs)
        limit = self._config.max_context_tokens
        within = limit is None or after <= limit
        report = ContextReport(
            step=state.step,
            action="none" if within else self._config.on_budget_exceeded,
            limit=limit,
            before=before,
            after=after,
            levels=levels,
            tools_total=len(specs),
            tools_kept=capability.last_report.kept,
            tools_dropped_tokens=capability.last_report.dropped_tokens,
            orphans=count_orphan_tool_calls(view),
            within_budget=within,
            estimated_prompt_tokens=after,
        )
        if not within:
            plan, tried, fits = self._escalate(state, specs)
            report.levels_tried = tried
            report.exhausted = not fits
            if fits:
                self._plan = plan
            if not fits and self._config.on_budget_exceeded == "reject":
                self.exhausted_reject = True
        self.reports.append(report)
        return kept

    # ------------------------------------------------------------------ 外层入口

    def preflight(self, state: AgentState, specs: list[dict[str, Any]]) -> BudgetDecision:
        """入场判据（新任务 / resume / recover / 重试重启）。

        `on_budget_exceeded="reject"` 命中即由外层产生唯一终止事件、不调 `drive()`（D5）；
        配 compress/degrade 时这里就把第一步的计划定好，省掉第一步的滞后。
        `max_context_tokens` 是硬上限语义：能按默认计划发出去就发，发不下就拒——
        入场点不偷偷降质量（降质量是 compress / degrade 两个动作各自的工作）。
        """
        limit = self._config.max_context_tokens
        self._observed_baseline = int(state.usage.get("prompt_tokens", 0) or 0)
        self._observed_step = state.step  # 本次已累加的量属于上一段，别算进本段
        view = self._view_with(self._plan, state.messages)
        _, capability = self._adapters(self._plan)
        estimate = self.estimate(view, capability.select(specs, state))
        if limit is None or estimate <= limit:
            return BudgetDecision(
                within_budget=True,
                estimate=estimate,
                limit=limit,
                detail=f"within budget: {estimate} <= {limit if limit is not None else '∞'}",
            )
        configured = self._config.on_budget_exceeded
        if configured == "reject":
            return BudgetDecision(
                within_budget=False,
                estimate=estimate,
                limit=limit,
                action="reject",
                detail=f"context estimate {estimate} > limit {limit}; on_budget_exceeded=reject",
            )
        plan, tried, fits = self._escalate(state, specs)
        if fits:
            self._plan = plan
        return BudgetDecision(
            within_budget=fits,
            estimate=estimate,
            limit=limit,
            action=configured,
            exhausted=not fits,
            levels_tried=tried,
            detail=(
                f"estimate={estimate} limit={limit} levels_tried={tried} "
                f"plan={plan.model_dump() if fits else '未达标'}"
            ),
        )

    def observe(self, state: AgentState) -> None:
        """A4 事后校准：把本步 Provider 回报的 prompt_tokens **增量**记到最近一份报告旁边。

        只记录、不回灌：让估算反向修正决策，等于在快照之外再养一个事实源（违反 L5）。
        同一个步会 yield 多个事件、每个都带同一份快照，所以按 `step` 去重，
        否则第二次观测会把增量算成 0。
        """
        actual = state.usage.get("prompt_tokens")
        if actual is None or not self.reports or state.step == self._observed_step:
            return
        total = int(actual)
        self.reports[-1].observed_prompt_tokens = max(total - self._observed_baseline, 0)
        self._observed_baseline = total
        self._observed_step = state.step

    # -------------------------------------------------------------------- 内部件

    def _adapters(self, plan: Plan) -> tuple[ContextCompressor | None, CapabilityFilter]:
        if plan not in self._cache:
            config = self._config.model_copy(
                update={
                    "keep_recent_messages": plan.keep_recent,
                    "max_visible_tools": plan.max_visible_tools,
                }
            )
            self._cache[plan] = (
                resolve_compression(config, self._summariser),
                resolve_capability_filter(config, self._counter),
            )
        return self._cache[plan]

    def _view_with(self, plan: Plan, messages: list[Message]) -> list[Message]:
        compressor, _ = self._adapters(plan)
        if compressor is None:
            return list(messages)
        return compressor.compress(messages).kept

    def _ladder(self) -> list[Plan]:
        """候选计划序列。**顺序就是策略**（D3 与 D4 的差别全在这里）。"""
        keeps = _descending(self._config.keep_recent_messages, KEEP_FLOOR)
        tools = _descending(self._config.max_visible_tools, TOOLS_FLOOR)
        if self._config.on_budget_exceeded == "degrade":
            pairs = [(self._config.keep_recent_messages, t) for t in tools]
            pairs += [(k, TOOLS_FLOOR) for k in keeps[1:]]
        else:  # compress / reject：先按 B3 阶梯压历史，再动能力视图
            pairs = [(k, self._config.max_visible_tools) for k in keeps]
            pairs += [(KEEP_FLOOR, t) for t in tools[1:]]
        ladder: list[Plan] = []
        for keep, tool in pairs:
            plan = Plan(keep_recent=keep, max_visible_tools=tool)
            if plan not in ladder:
                ladder.append(plan)
        return ladder

    def _escalate(self, state: AgentState, specs: list[dict[str, Any]]) -> tuple[Plan, int, bool]:
        """逐级试到达标：返回 (计划, 试了几档, 是否达标)。压不动就交回当前计划并标未达标。"""
        limit = self._config.max_context_tokens
        assert limit is not None  # 调用方只在超限处调用
        ladder = self._ladder()
        for index, plan in enumerate(ladder, start=1):
            view = self._view_with(plan, state.messages)
            _, capability = self._adapters(plan)
            if self.estimate(view, capability.select(specs, state)) <= limit:
                return plan, index, True
        return self._plan, len(ladder), False


class EngineAssembler(ContextAssembler):
    """把引擎的组装能力包成 loop 认识的 `ContextAssembler`（L1：零修改 loop 的适配器）。"""

    def __init__(self, engine: ContextEngine) -> None:
        self._engine = engine

    def assemble(self, messages: list[Message]) -> list[Message]:
        return self._engine.assemble(messages)


def _descending(start: int, floor: int) -> list[int]:
    """按 `LADDER_STEP` 收缩到地板，含起点与地板，定长定序（升档序列本身也必须可复现）。"""
    values = [max(start, floor)]
    while values[-1] > floor:
        values.append(max(values[-1] // LADDER_STEP, floor))
    return values
