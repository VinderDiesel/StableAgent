"""P4 领域记录。新字段一律带默认值（兼容扩展规则），时间用毫秒整数，ID 由注入工厂生成。"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field, model_validator

from stableagent.orchestration.errors import IllegalTransitionError


class IdFactory(Protocol):
    def new_id(self, prefix: str) -> str: ...


class Clock(Protocol):
    def now_ms(self) -> int: ...


class Uuid4Id:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


class SystemClock:
    def now_ms(self) -> int:
        import time

        return time.time_ns() // 1_000_000


class TicketStatus(str, Enum):
    OPEN = "open"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class AssignmentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    ENDED = "ended"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class EffectState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EffectKind(str, Enum):
    LAUNCH_ASSIGNMENT = "launch_assignment"
    DELIVER_INPUT = "deliver_input"
    CANCEL_ASSIGNMENT = "cancel_assignment"


class ReportKind(str, Enum):
    PROGRESS = "progress"
    WAITING_FOR_HUMAN = "waiting_for_human"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


class Mandate(BaseModel):
    id: str
    origin_kind: Literal["local"] = "local"
    requester: str = "local"
    scope: str = ""
    policy_version: str = "p4-local-v1"
    granted_at: int


class Ticket(BaseModel):
    id: str
    title: str
    task: str
    created_at: int
    status: TicketStatus = TicketStatus.OPEN
    mandate_id: str
    parent_id: str | None = None
    ended_at: int | None = None
    queued_at: int | None = None
    woken_at: int | None = None


class Assignment(BaseModel):
    id: str
    ticket_id: str
    attempt: int = Field(default=1, ge=1)
    status: AssignmentStatus = AssignmentStatus.PENDING
    worker_run_id: str | None = None
    started_at: int | None = None
    ended_at: int | None = None
    checkpoint_run_id: str | None = None
    failure: str | None = None
    deadline_at: int | None = None
    worker_config_version: str | None = None
    worker_config: dict[str, Any] = Field(default_factory=dict)


class TicketInput(BaseModel):
    id: str
    ticket_id: str
    sequence: int
    kind: Literal["task", "text"] = "task"
    payload: str
    created_at: int
    delivered_at: int | None = None
    consumed_at: int | None = None
    state: Literal["pending", "consumed"] = "pending"


class Report(BaseModel):
    id: str
    ticket_id: str
    assignment_id: str
    kind: ReportKind
    summary: str
    created_at: int
    event_step: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Dependency(BaseModel):
    ticket_id: str
    depends_on_ticket_id: str
    on_failure: Literal["block", "continue"] = "block"

    @model_validator(mode="after")
    def _reject_self_dependency(self) -> Dependency:
        if self.ticket_id == self.depends_on_ticket_id:
            raise ValueError(f"A ticket cannot depend on itself: {self.ticket_id}")
        return self


class Effect(BaseModel):
    id: str
    kind: EffectKind
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    state: EffectState = EffectState.PENDING
    attempts: int = 0
    due_at: int
    last_error: str | None = None


class TicketAction(str, Enum):
    QUEUE = "queue"
    START = "start"
    WAIT = "wait"
    DELIVER = "deliver"
    BLOCK = "block"
    FAIL = "fail"
    CANCEL = "cancel"
    COMPLETE = "complete"


class AssignmentAction(str, Enum):
    LAUNCH = "launch"
    END = "end"
    FAIL = "fail"
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    RELAUNCH = "relaunch"


class EffectAction(str, Enum):
    CLAIM = "claim"
    SUCCEED = "succeed"
    RETRY = "retry"
    FAIL = "fail"
    CANCEL = "cancel"
    RECLAIM = "reclaim"


_TICKET_TABLE: dict[tuple[TicketStatus, TicketAction], TicketStatus] = {
    (TicketStatus.OPEN, TicketAction.QUEUE): TicketStatus.QUEUED,
    (TicketStatus.OPEN, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.QUEUED, TicketAction.START): TicketStatus.RUNNING,
    (TicketStatus.QUEUED, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.RUNNING, TicketAction.WAIT): TicketStatus.WAITING,
    (TicketStatus.RUNNING, TicketAction.DELIVER): TicketStatus.READY,
    (TicketStatus.RUNNING, TicketAction.BLOCK): TicketStatus.BLOCKED,
    (TicketStatus.RUNNING, TicketAction.FAIL): TicketStatus.FAILED,
    (TicketStatus.RUNNING, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.WAITING, TicketAction.START): TicketStatus.RUNNING,
    (TicketStatus.WAITING, TicketAction.BLOCK): TicketStatus.BLOCKED,
    (TicketStatus.WAITING, TicketAction.FAIL): TicketStatus.FAILED,
    (TicketStatus.WAITING, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.BLOCKED, TicketAction.QUEUE): TicketStatus.QUEUED,
    (TicketStatus.BLOCKED, TicketAction.START): TicketStatus.RUNNING,
    (TicketStatus.BLOCKED, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.READY, TicketAction.COMPLETE): TicketStatus.COMPLETED,
    (TicketStatus.READY, TicketAction.CANCEL): TicketStatus.CANCELLED,
}

_ASSIGNMENT_TABLE: dict[tuple[AssignmentStatus, AssignmentAction], AssignmentStatus] = {
    (AssignmentStatus.PENDING, AssignmentAction.LAUNCH): AssignmentStatus.RUNNING,
    (AssignmentStatus.PENDING, AssignmentAction.CANCEL): AssignmentStatus.CANCELLED,
    (AssignmentStatus.RUNNING, AssignmentAction.END): AssignmentStatus.ENDED,
    (AssignmentStatus.RUNNING, AssignmentAction.FAIL): AssignmentStatus.FAILED,
    (AssignmentStatus.RUNNING, AssignmentAction.CANCEL): AssignmentStatus.CANCELLED,
    (AssignmentStatus.RUNNING, AssignmentAction.INTERRUPT): AssignmentStatus.INTERRUPTED,
    (AssignmentStatus.INTERRUPTED, AssignmentAction.RELAUNCH): AssignmentStatus.RUNNING,
    (AssignmentStatus.INTERRUPTED, AssignmentAction.CANCEL): AssignmentStatus.CANCELLED,
}

_EFFECT_TABLE: dict[tuple[EffectState, EffectAction], EffectState] = {
    (EffectState.PENDING, EffectAction.CLAIM): EffectState.RUNNING,
    (EffectState.PENDING, EffectAction.CANCEL): EffectState.CANCELLED,
    (EffectState.RUNNING, EffectAction.SUCCEED): EffectState.SUCCEEDED,
    (EffectState.RUNNING, EffectAction.RETRY): EffectState.RETRY_WAIT,
    (EffectState.RUNNING, EffectAction.FAIL): EffectState.FAILED,
    (EffectState.RUNNING, EffectAction.RECLAIM): EffectState.PENDING,
    (EffectState.RETRY_WAIT, EffectAction.CLAIM): EffectState.RUNNING,
    (EffectState.RETRY_WAIT, EffectAction.FAIL): EffectState.FAILED,
    (EffectState.RETRY_WAIT, EffectAction.CANCEL): EffectState.CANCELLED,
}

TERMINAL_TICKET_STATUSES = frozenset(
    {TicketStatus.COMPLETED, TicketStatus.CANCELLED, TicketStatus.FAILED}
)

_StateT = TypeVar("_StateT", bound=Enum)
_ActionT = TypeVar("_ActionT", bound=Enum)


def _step(
    table: dict[tuple[_StateT, _ActionT], _StateT],
    machine: str,
    current: _StateT,
    action: _ActionT,
) -> _StateT:
    try:
        return table[(current, action)]
    except KeyError as exc:
        raise IllegalTransitionError(machine, current.value, action.value) from exc


def next_ticket_status(current: TicketStatus, action: TicketAction) -> TicketStatus:
    return _step(_TICKET_TABLE, "ticket", current, action)


def next_assignment_status(current: AssignmentStatus, action: AssignmentAction) -> AssignmentStatus:
    return _step(_ASSIGNMENT_TABLE, "assignment", current, action)


def next_effect_status(current: EffectState, action: EffectAction) -> EffectState:
    return _step(_EFFECT_TABLE, "effect", current, action)


def assert_assignment_belongs_to(ticket: Ticket, assignment: Assignment) -> None:
    if assignment.ticket_id != ticket.id:
        raise IllegalTransitionError(
            "assignment", assignment.status.value, f"cross_ticket:{ticket.id}"
        )
