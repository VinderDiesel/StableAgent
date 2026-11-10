"""常驻运行面：单实例锁、启动恢复与 poll/route/tick 推进（规格 WP-6.4）。

方向锁：只依赖 supervisor 既有组件与 orchestration 公开入口，不 import execution/integrations。
组成根只注入依赖，不存业务规则（WP-6.4 第 1 条）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from stableagent.control.approval import ApprovalService
from stableagent.control.audit import AuditWriter
from stableagent.control.policy import StaticPolicy
from stableagent.observability.emitter import DomainEventEmitter
from stableagent.orchestration.executor import WorkerExecutor
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Clock,
    EffectKind,
    IdFactory,
    Ticket,
    TicketStatus,
)
from stableagent.orchestration.outbox import EffectDispatcher, EffectHandler
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService, RecoverySummary
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import Decision, RuntimeControl, SourceCursor
from stableagent.supervisor.notifications import NotificationConfig, NotificationDrafter
from stableagent.supervisor.router import (
    RouterConfig,
    RouterService,
    RouterStateError,
    require_operator,
)
from stableagent.supervisor.service import SupervisorService
from stableagent.supervisor.source import PollBatch, Source, SourceError
from stableagent.supervisor.store import SupervisorStore


class RuntimeLockError(RuntimeError):
    """单实例锁被占用或中途丢失：调用方不得继续写库。"""


class RuntimeStateError(RuntimeError):
    """Runtime 生命周期被打乱（未 start 就 step），不是可重试的外部错误。"""


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    router: RouterConfig
    owner: str = "supervisor-runtime"
    poll_limit: int = 100
    lock_ttl_ms: int = 30_000
    stop_grace_ms: int = 5_000
    step_interval_ms: int = 1_000
    notifications: NotificationConfig | None = None


@dataclass(frozen=True)
class RuntimeStart:
    recovery: RecoverySummary
    routed: int
    paused: bool


@dataclass(frozen=True)
class RuntimeStep:
    paused: bool
    received: int
    routed: int
    duplicates: int
    enqueued: int
    dispatched: int
    external: int
    running: int
    source_error: str | None = None
    drafted: int = 0


@dataclass(frozen=True)
class RuntimeStop:
    drained: bool
    running_assignments: int


class RuntimeStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    started: bool
    paused: bool
    owner: str | None
    paused_at: int | None
    paused_by: str | None
    reason: str | None
    source_health: tuple[SourceCursor, ...]
    queued_tickets: int
    running_assignments: int
    due_effects: int
    failed_effects: int
    pending_approvals: int


def read_runtime_status(store: SupervisorStore, clock: Clock) -> RuntimeStatus:
    now = clock.now_ms()
    lock = store.get_runtime_lock()
    control = store.get_runtime_control()
    started = lock is not None and lock.expires_at > now
    return RuntimeStatus(
        started=started,
        paused=control.paused,
        owner=lock.owner if started and lock is not None else None,
        paused_at=control.changed_at if control.paused else None,
        paused_by=control.actor if control.paused else None,
        reason=control.reason if control.paused else None,
        source_health=tuple(store.list_source_cursors()),
        queued_tickets=store.count_tickets(status=TicketStatus.QUEUED),
        running_assignments=store.count_assignments(status=AssignmentStatus.RUNNING),
        due_effects=store.count_due_effects(now),
        failed_effects=len(store.list_failed_effects()),
        pending_approvals=len(store.list_pending_approvals()),
    )


@dataclass(frozen=True)
class RuntimeBundle:
    runtime: Runtime
    controller: RuntimeController
    router: RouterService
    service: SupervisorService
    orchestration: OrchestrationService
    scheduler: Scheduler
    effects: EffectDispatcher


class Runtime:
    def __init__(
        self,
        *,
        store: SupervisorStore,
        source: Source,
        router: RouterService,
        orchestration: OrchestrationService,
        scheduler: Scheduler,
        effects: EffectDispatcher,
        config: RuntimeConfig,
        clock: Clock,
        drafter: NotificationDrafter | None = None,
    ) -> None:
        self._store = store
        self._source = source
        self._router = router
        self._orchestration = orchestration
        self._scheduler = scheduler
        self._effects = effects
        self._config = config
        self._clock = clock
        self._drafter = drafter
        self._started = False
        self._stop_requested = False
        self._was_paused = False

    @property
    def config(self) -> RuntimeConfig:
        return self._config

    def start(self) -> RuntimeStart:
        """先锁、再恢复、后补路由；任何 effect/worker 派发都留到 step()。"""
        now = self._clock.now_ms()
        if not self._store.acquire_runtime_lock(
            self._config.owner, now=now, ttl_ms=self._config.lock_ttl_ms
        ):
            lock = self._store.get_runtime_lock()
            raise RuntimeLockError(f"运行锁被占用：owner={lock.owner if lock else '?'}")
        # 锁已在手 = 上一进程确定死亡：RUNNING 收成 INTERRUPTED，并重置在途 effect。
        recovery = self._orchestration.recover()
        control = self._store.get_runtime_control()
        routed = 0 if control.paused else self._route_pending()
        self._was_paused = control.paused
        self._started = True
        return RuntimeStart(recovery=recovery, routed=routed, paused=control.paused)

    async def step(self) -> RuntimeStep:
        if not self._started:
            raise RuntimeStateError("Runtime 未 start：step 不得写库")
        self._renew_lock()
        source_error: str | None = None
        try:
            batch = await self._source.poll(limit=self._config.poll_limit)
        except SourceError as error:
            # Source 失败已由 Source 记进 source_health；Runtime 只让本轮空转，不杀调度。
            batch = PollBatch()
            source_error = error.code
        control = self._store.get_runtime_control()
        if control.paused:
            self._was_paused = True
            return RuntimeStep(
                paused=True,
                received=len(batch.events),
                routed=0,
                duplicates=batch.duplicates,
                enqueued=0,
                dispatched=0,
                external=0,
                running=len(self._scheduler.running_assignments()),
                source_error=source_error,
                drafted=0,
            )
        self._renew_lock()
        routed = 0
        if self._was_paused:
            routed += self._route_pending()
            self._was_paused = False
        duplicates = 0
        for event in batch.events:
            reception = self._router.receive(event.trigger)
            if reception.duplicate:
                duplicates += 1
            else:
                routed += 1
        tick = await self._scheduler.tick()
        drafted = 0
        if self._drafter is not None:
            drafted = self._drafter.draft_due()
        external = await self._effects.dispatch_due()
        return RuntimeStep(
            paused=False,
            received=len(batch.events),
            routed=routed,
            duplicates=duplicates + batch.duplicates,
            enqueued=tick.enqueued,
            dispatched=tick.dispatched,
            external=external,
            running=tick.running,
            source_error=source_error,
            drafted=drafted,
        )

    def status(self) -> RuntimeStatus:
        return read_runtime_status(self._store, self._clock)

    def pause(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        return self._store.set_runtime_paused(
            True, actor=actor, now=self._clock.now_ms(), reason=reason
        )

    def resume(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        control = self._store.set_runtime_paused(
            False, actor=actor, now=self._clock.now_ms(), reason=reason
        )
        if self._started:
            self._renew_lock()
            self._route_pending()
        self._was_paused = False
        return control

    async def serve(self, *, max_steps: int | None = None) -> int:
        """常驻循环；max_steps 只给测试/演练用，正常部署不传。"""
        steps = 0
        try:
            while not self._stop_requested and (max_steps is None or steps < max_steps):
                await self.step()
                steps += 1
                if self._stop_requested or (max_steps is not None and steps >= max_steps):
                    break
                await asyncio.sleep(self._config.step_interval_ms / 1000.0)
        finally:
            await self.stop()
        return steps

    async def stop(self) -> RuntimeStop:
        """先停 intake（不再 step），再等宽限期；不取消 Worker、不删任何历史。"""
        self._stop_requested = True
        if not self._started:
            return RuntimeStop(drained=True, running_assignments=0)
        drained = await self._scheduler.wait_idle(timeout_ms=self._config.stop_grace_ms)
        running = len(self._scheduler.running_assignments())
        self._store.release_runtime_lock(self._config.owner)
        self._started = False
        return RuntimeStop(drained=drained, running_assignments=running)

    def _renew_lock(self) -> None:
        if not self._store.heartbeat_runtime_lock(
            self._config.owner, now=self._clock.now_ms(), ttl_ms=self._config.lock_ttl_ms
        ):
            self._started = False
            raise RuntimeLockError("运行锁已丢失：owner 变更或租约过期")

    def _route_pending(self) -> int:
        routed = 0
        for trigger in self._store.list_unrouted_triggers(limit=self._config.poll_limit):
            if not self._router.receive(trigger).duplicate:
                routed += 1
        return routed


def build_runtime(
    *,
    store: SupervisorStore,
    policy: StaticPolicy,
    source: Source,
    executor: WorkerExecutor,
    effect_handlers: Mapping[EffectKind, EffectHandler],
    config: RuntimeConfig,
    clock: Clock,
    ids: IdFactory,
    scheduler_max_concurrency: int = 1,
    audit: AuditWriter | None = None,
    emitter: DomainEventEmitter | None = None,
) -> RuntimeBundle:
    """组成根：只注入依赖；observe 评估器与 P4 服务在此装配。"""
    obs = emitter if emitter is not None else DomainEventEmitter(store, clock=clock, ids=ids)
    router = RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        config.router,
        clock=clock,
        ids=ids,
        audit=audit,
        emitter=obs,
    )
    approvals = ApprovalService(store, clock=clock, ids=ids)
    service = SupervisorService(
        store, policy, router, clock=clock, ids=ids, audit=audit, approvals=approvals
    )
    orchestration = OrchestrationService(store, clock=clock, ids=ids)
    scheduler = Scheduler(
        store=store,
        service=orchestration,
        executor=executor,
        clock=clock,
        max_concurrency=scheduler_max_concurrency,
    )
    effects = EffectDispatcher(store, effect_handlers, clock=clock, emitter=obs)
    drafter = None
    if config.notifications is not None:
        drafter = NotificationDrafter(
            store,
            approvals,
            config.notifications,
            repository=config.router.repository,
            clock=clock,
        )
    runtime = Runtime(
        store=store,
        source=source,
        router=router,
        orchestration=orchestration,
        scheduler=scheduler,
        effects=effects,
        config=config,
        clock=clock,
        drafter=drafter,
    )
    controller = RuntimeController(runtime, service, orchestration)
    return RuntimeBundle(
        runtime=runtime,
        controller=controller,
        router=router,
        service=service,
        orchestration=orchestration,
        scheduler=scheduler,
        effects=effects,
    )


class RuntimeController:
    """运行控制命令面：只做 operator 校验与委托，业务规则仍在既有服务层。"""

    def __init__(
        self,
        runtime: Runtime,
        supervisor: SupervisorService,
        orchestration: OrchestrationService,
    ) -> None:
        self._runtime = runtime
        self._supervisor = supervisor
        self._orchestration = orchestration

    def status(self) -> RuntimeStatus:
        return self._runtime.status()

    def pause_processing(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        self._require_operator(actor)
        return self._runtime.pause(actor=actor, reason=reason)

    def resume_processing(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        self._require_operator(actor)
        return self._runtime.resume(actor=actor, reason=reason)

    def retry_ticket(self, ticket_id: str, *, actor: str) -> Assignment:
        self._require_operator(actor)
        ticket = self._orchestration.get_ticket(ticket_id).ticket
        if ticket.status is not TicketStatus.FAILED:
            raise RouterStateError(f"retry_ticket 只接受 FAILED：{ticket.status.value}")
        return self._orchestration.retry_ticket(ticket_id)

    def stop_ticket(self, ticket_id: str, *, actor: str, reason: str | None = None) -> Ticket:
        self._require_operator(actor)
        return self._supervisor.stop(ticket_id, actor=actor, reason=reason)

    def reevaluate(self, trigger_id: str, *, actor: str) -> Decision:
        self._require_operator(actor)
        return self._supervisor.reevaluate(trigger_id, actor=actor)

    def _require_operator(self, actor: str) -> None:
        require_operator(self._runtime.config.router.operators, actor)
