"""P4 领域记录。新字段一律带默认值（兼容扩展规则），时间用毫秒整数，ID 由注入工厂生成。"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator


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
