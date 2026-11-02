"""Agent 运行时装配（契约 v1.1.0）：三形态 + P2 可靠性外层（loop 零修改）。

P1 语义保持不变（docs/acceptance/p1-core-runtime.md 第 0 节）：
- F3：`pause()` 步边界生效；无进行中运行显式 no-op（返回 False）；
  `resume()/aresume()` 无 PAUSED 态抛 ValueError；暂停期间拒绝新任务抛 RuntimeError。
- F4：`done|error` 每次运行恰好一个且位于流末尾。

P2 新增（全部在本外层，`core/loop.py` 不感知，见 docs/acceptance/p2-checkpoint-recovery.md L1/L3）：
- checkpoint：消费 `drive()` 的 `(事件, 快照)` 对，在步边界落盘并注入 `checkpoint` 事件；
  终止态只落盘、不发事件（保证 F4）。
- 失败自愈：错误分类决定 指数退避重试（可轮转 fallback Provider）/ 转人工暂停 / 认失败。
- 崩溃恢复：`recover()/arecover()` 从存储最近快照续跑（版本迁移在 `recovery.py`）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from stableagent.context.assembly import ContextAssembler
from stableagent.context.budget import ContextReport
from stableagent.context.capability import CapabilityReport
from stableagent.context.compression import Summariser
from stableagent.context.engine import ContextEngine, EngineAssembler
from stableagent.context.tokens import TokenCounter
from stableagent.core.checkpoint.manager import CheckpointManager
from stableagent.core.checkpoint.recovery import (
    RecoveryDirector,
    RetryPolicy,
    Sleeper,
    load_latest_state,
    repair_for_retry,
)
from stableagent.core.checkpoint.storage import (
    CheckpointRecord,
    CheckpointStore,
    RedisLike,
    build_store,
)
from stableagent.core.loop import AgentLoop, ToolSelector
from stableagent.core.state import (
    TERMINAL_EVENTS,
    AgentState,
    AgentStatus,
    Config,
    Event,
    EventType,
    Message,
)
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
        *,
        checkpoint_store: CheckpointStore | None = None,
        redis_client: RedisLike | None = None,
        fallback: Provider | None = None,
        sleep: Sleeper | None = None,
        token_counter: TokenCounter | None = None,
        summariser: Summariser | None = None,
    ) -> None:
        self.name = name
        self.provider = provider
        self.config = config if config is not None else Config()
        self._registry = registry if registry is not None else ToolRegistry()
        context_config = self.config.context
        self._selector_overridden = tool_selector is not None
        if self._selector_overridden:
            # 调用方的选择器决定发什么工具，引擎无从预知——按全量规格保守估算，
            # 并且不报一份"其实没生效"的动态视图结果。
            context_config = context_config.model_copy(update={"capability_filter": "static"})
        # P3 D1：三个开关全关时不建引擎（L4 默认路径零开销）；建了就由它统一服务两侧。
        self._context = (
            None
            if (
                context_config.assembly == "passthrough"
                and context_config.compression == "none"
                and context_config.capability_filter == "static"
                and context_config.max_context_tokens is None
            )
            else ContextEngine(context_config, token_counter=token_counter, summariser=summariser)
        )
        self._assembler = assembler or (
            EngineAssembler(self._context) if self._context is not None else None
        )
        # 动态视图只在引擎里跑：配了非 static 过滤器就一定建了引擎，不再单建一份事实源。
        self._tool_selector = tool_selector or (
            self._context.select if self._context is not None else None
        )
        self._providers: list[Provider | str | None] = [provider, fallback]
        self._sleep = sleep or asyncio.sleep
        self._loop: AgentLoop | None = None
        self._loop_provider: Provider | str | None = None
        self._last_state: AgentState | None = None
        self._running = False
        store = build_store(
            self.config.checkpoint, injected=checkpoint_store, redis_client=redis_client
        )
        self._checkpointer = (
            CheckpointManager(
                store,
                auto_save_every=self.config.checkpoint.auto_save_every,
            )
            if store is not None
            else None
        )
        self._director = RecoveryDirector(
            strategy=self.config.checkpoint.recovery_strategy,
            policy=RetryPolicy.from_config(self.config.checkpoint),
            provider_count=self._provider_slots,
        )
        self.last_checkpoint: CheckpointRecord | None = None

    @property
    def _provider_slots(self) -> int:
        return sum(1 for item in self._providers if isinstance(item, Provider))

    @property
    def checkpoints(self) -> CheckpointManager | None:
        """P2 落盘入口；未启用持久化时为 None（P1 默认行为不变）。"""
        return self._checkpointer

    @property
    def capability_report(self) -> CapabilityReport | None:
        """最近一次能力视图筛选的产出（只读）；未启用动态视图时为 None。"""
        if self._selector_overridden:
            return None  # 选择器被接管：报任何筛选结果都是在说谎
        return self._context.capability_report if self._context is not None else None

    @property
    def context_reports(self) -> list[ContextReport]:
        """D6：每步上下文决策的历史。默认只读属性，不发事件（沿用 P2 静默落盘的先例）。"""
        return list(self._context.reports) if self._context is not None else []

    def _engine(self, provider_index: int = 0) -> AgentLoop:
        target = self._providers[min(provider_index, len(self._providers) - 1)]
        if not isinstance(target, Provider):
            raise ValueError("需注入 Provider 实例（如 OpenAIProvider）；字符串寻址在后续版本提供")
        if self._loop is None or self._loop_provider is not target:
            loop = AgentLoop(
                provider=target,
                registry=self._registry,
                config=self.config,
                assembler=self._assembler,
                tool_selector=self._tool_selector,
            )
            if self._loop is not None:  # 暂停标志跨 provider 切换保留（F3）
                loop.pause_requested = self._loop.pause_requested
            self._loop = loop
            self._loop_provider = target
        return self._loop

    # -------------------------------------------------------------- 可靠性外层

    async def _save(self, state: AgentState, *, force: bool) -> CheckpointRecord | None:
        if self._checkpointer is None:
            return None
        record = await self._checkpointer.save(state, force=force)
        if record is not None:
            self.last_checkpoint = record
        return record

    @staticmethod
    def _checkpoint_event(record: CheckpointRecord) -> Event:
        return Event(
            type=EventType.CHECKPOINT,
            data={
                "run_id": record.run_id,
                "step": record.step,
                "contract_version": record.contract_version,
                "saved_at": record.saved_at,
            },
            step=record.step,
        )

    @staticmethod
    def _paused_event(state: AgentState, reason: str) -> Event:
        return Event(
            type=EventType.PAUSED,
            data={"step": state.step, "reason": reason, "detail": state.error},
            step=state.step,
        )

    async def _pipeline(self, state: AgentState) -> AsyncIterator[tuple[Event, AgentState]]:
        """事件即时透传（F3 的步边界语义依赖这一点），checkpoint 以"回看"方式补发：
        第 k+1 步的第一个事件到达即说明第 k 步已完整结束，此时落盘并补发 checkpoint 事件。
        失败自愈决策同样只出现在这一层。"""
        attempts = 0
        current = state
        try:
            while True:
                engine = self._engine(attempts % max(self._provider_slots, 1))
                restart_from: AgentState | None = None
                budget_paused = False
                if self._context is not None:
                    # D1：同一段的两个位点共用一个引擎实例；先落快照（L5），再按落定后的量判预算。
                    current = self._context.fold(current)
                    # D5：入场闸。命中 reject 就在这里产生唯一终止事件，连 drive() 都不进，
                    # 因此也不会被失败自愈重试（同参数再判一次必然同样超限，属 non_retryable）。
                    gate = self._context.preflight(current, self._registry.specs())
                    if gate.action == "reject":
                        failed = current.model_copy(
                            update={"status": AgentStatus.FAILED, "error": gate.detail}
                        )
                        self._last_state = failed
                        await self._save(failed, force=True)
                        yield (
                            Event(
                                type=EventType.ERROR,
                                data={
                                    "reason": "budget_exceeded",
                                    "detail": gate.detail,
                                    "estimate": gate.estimate,
                                    "limit": gate.limit,
                                },
                                step=failed.step,
                            ),
                            failed,
                        )
                        return
                step_seen: int | None = None
                pending: AgentState | None = None
                async for event, after in engine.drive(current):
                    if pending is not None and after.step != step_seen:
                        record = await self._save(pending, force=False)
                        if record is not None:
                            yield self._checkpoint_event(record), pending
                    step_seen = after.step
                    pending = after
                    self._last_state = after
                    if self._context is not None:
                        self._context.observe(after)  # A4：估算与真实 prompt_tokens 成对留档
                        if self._context.exhausted_reject:
                            # 运行中不做紧急制动（引擎模块头的理由），走 F3 的步边界暂停
                            self._context.exhausted_reject = False
                            engine.pause_requested = True
                            budget_paused = True

                    if event.type is EventType.ERROR:
                        detail = str(event.data.get("detail") or after.error or event.data)
                        decision = self._director.decide(detail, attempts=attempts)
                        if decision.action == "retry":
                            attempts = decision.attempt
                            repaired = repair_for_retry(after)
                            self._last_state = repaired
                            yield (
                                Event(
                                    type=EventType.RETRY,
                                    data={
                                        "attempt": decision.attempt,
                                        "delay_seconds": decision.delay_seconds,
                                        "error_class": decision.error_class.value,
                                        "provider_index": decision.provider_index,
                                        "detail": decision.detail,
                                    },
                                    step=repaired.step,
                                ),
                                repaired,
                            )
                            await self._sleep(decision.delay_seconds)
                            restart_from = repaired
                            break
                        if decision.action == "needs_human":
                            paused = after.model_copy(update={"status": AgentStatus.PAUSED})
                            self._last_state = paused
                            record = await self._save(paused, force=True)
                            if record is not None:
                                yield self._checkpoint_event(record), paused
                            yield self._paused_event(paused, "needs_human"), paused
                            return
                        await self._save(after, force=True)
                        yield event, after
                        return

                    if event.type in TERMINAL_EVENTS:  # done：静默落盘，终止事件保持末尾（F4）
                        await self._save(after, force=True)
                        yield event, after
                        return

                    if event.type is EventType.PAUSED:
                        if budget_paused:
                            # 预算暂停带可读原因：用户暂停的事件形状保持 P1 原样，不改既有断言
                            paused = after.model_copy(
                                update={"error": "context budget exhausted after full ladder"}
                            )
                            self._last_state = paused
                            record = await self._save(paused, force=True)
                            if record is not None:
                                yield self._checkpoint_event(record), paused
                            yield self._paused_event(paused, "budget_exceeded"), paused
                            return
                        record = await self._save(after, force=True)
                        if record is not None:
                            yield self._checkpoint_event(record), after
                        yield event, after
                        return

                    yield event, after
                if restart_from is None:
                    return
                current = restart_from
        finally:
            # 预算暂停标志只在"本段还要继续"时有意义。最后一步才判出超限时本段已经
            # DONE/FAILED，留着它会让下一段刚开场就被 F3 暂停——那是上一条注释里的
            # 语义泄漏，不是暂停功能。
            if self._loop is not None:
                self._loop.pause_requested = False

    # ------------------------------------------------------------------ 三形态

    async def _drive(self, task: str) -> AsyncIterator[tuple[Event, AgentState]]:
        if self._last_state is not None and self._last_state.status is AgentStatus.PAUSED:
            raise RuntimeError("存在暂停中的运行：请先 resume()，或使用新的 Agent 实例")
        state = AgentState(
            run_id=uuid.uuid4().hex,
            agent_name=self.name,
            status=AgentStatus.RUNNING,
            messages=[Message(role="user", content=task)],
        )
        self._running = True
        try:
            async for pair in self._pipeline(state):
                yield pair
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

    # -------------------------------------------------------------- 暂停 / 恢复

    def pause(self) -> bool:
        if not self._running:
            return False  # F3：显式 no-op
        self._engine().pause_requested = True
        return True

    async def _continue(self, state: AgentState) -> AgentState:
        final = state
        async for _, after in self._continue_pairs(state):
            final = after
        return final

    async def _continue_pairs(self, state: AgentState) -> AsyncIterator[tuple[Event, AgentState]]:
        self._running = True
        try:
            async for pair in self._pipeline(state):
                yield pair
        finally:
            self._running = False

    async def aresume(self) -> AgentState:
        return await self._continue(self._resume_state())

    def resume(self) -> AgentState:
        return asyncio.run(self.aresume())

    def _resume_state(self) -> AgentState:
        if self._last_state is None or self._last_state.status is not AgentStatus.PAUSED:
            raise ValueError("没有可恢复的 PAUSED 状态")
        return self._last_state.model_copy(update={"status": AgentStatus.RUNNING})

    # ------------------------------------------------------------------ 崩溃恢复

    @property
    def _store(self) -> CheckpointStore:
        if self._checkpointer is None:
            raise ValueError("未启用 checkpoint：请设置 auto_save_every 或注入 checkpoint_store")
        return self._checkpointer.store

    @property
    def last_state(self) -> AgentState | None:
        """最近一次产出的快照（演示/调试用只读视图，不是第二份状态源）。"""
        return self._last_state

    def _recovery_target(self, run_id: str | None) -> str:
        target = run_id
        if target is None and self._last_state is not None:
            target = self._last_state.run_id
        if target is None:
            raise ValueError("没有可恢复的运行：请提供 run_id 或启用 checkpoint")
        return target

    async def _recovery_state(self, run_id: str | None) -> AgentState:
        """载入最近快照：终止态原样返回（不续跑），其余洗成 RUNNING 待续跑。"""
        target = self._recovery_target(run_id)
        state = await load_latest_state(self._store, target)
        if state is None:
            raise ValueError(f"存储中没有 run_id={target} 的快照")
        if state.status in (AgentStatus.SUCCEEDED, AgentStatus.FAILED):
            return state
        self._last_state = None  # 外部快照续跑：绕开"暂停中拒绝新任务"守卫
        return state.model_copy(update={"status": AgentStatus.RUNNING, "error": None})

    async def arecover(self, run_id: str | None = None) -> AgentState:
        """从最近快照续跑：接受 RUNNING（崩溃）与 PAUSED（暂停/需人工）快照。"""
        state = await self._recovery_state(run_id)
        if state.status in (AgentStatus.SUCCEEDED, AgentStatus.FAILED):
            return state
        return await self._continue(state)

    async def stream_recover(self, run_id: str | None = None) -> AsyncIterator[Event]:
        """`arecover` 的事件流形态：恢复过程的续跑事件逐条可见。"""
        state = await self._recovery_state(run_id)
        if state.status in (AgentStatus.SUCCEEDED, AgentStatus.FAILED):
            return
        async for event, _ in self._continue_pairs(state):
            yield event

    def recover(self, run_id: str | None = None) -> AgentState:
        return asyncio.run(self.arecover(run_id))

    async def checkpoint_history(self, run_id: str) -> list[CheckpointRecord]:
        return await self._store.records(run_id)
