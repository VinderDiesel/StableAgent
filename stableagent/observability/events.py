"""领域事件记录：稳定 kind + 关联 ID + 已脱敏 attrs + payload_digest。"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainEventKind(str, Enum):
    TRIGGER_RECEIVED = "trigger.received"
    DECISION_RECORDED = "decision.recorded"
    EFFECT_ATTEMPTED = "effect.attempted"
    EFFECT_SUCCEEDED = "effect.succeeded"
    EFFECT_FAILED = "effect.failed"


def canonical_attrs_digest(attrs: dict[str, Any]) -> str:
    """对已脱敏 attrs 做稳定 digest，便于对照而不存原文。"""
    body = json.dumps(attrs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    timestamp: int
    tenant_id: str
    kind: DomainEventKind
    outcome: str
    reason_code: str | None = None
    ticket_id: str | None = None
    assignment_id: str | None = None
    run_id: str | None = None
    effect_id: str | None = None
    trigger_id: str | None = None
    decision_id: str | None = None
    payload_digest: str
    attrs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id")
    @classmethod
    def _tenant_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("tenant_id 不能为空")
        return value

    @field_validator("outcome")
    @classmethod
    def _outcome_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("outcome 不能为空")
        return value
