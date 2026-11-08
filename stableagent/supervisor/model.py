"""P6 领域记录。Trigger 不可变；时间用毫秒整数；ID 由调用方注入。"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def canonical_json(value: Any) -> str:
    """跨进程稳定的 JSON 写法：键有序、无多余空白、非 ASCII 原样保留。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class TriggerKind(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    ISSUE_COMMENT = "issue_comment"
    REVIEW_COMMENT = "review_comment"
    REVIEW = "review"
    CI_STATUS = "ci_status"


class ThreadKind(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    REVIEW = "review"


class ThreadStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    STOPPED = "stopped"


class ChannelKind(str, Enum):
    ISSUE_COMMENT = "issue_comment"
    PULL_REQUEST_COMMENT = "pull_request_comment"
    REVIEW_REPLY = "review_reply"


class SubscriptionMode(str, Enum):
    OWN = "own"
    CONTEXT = "context"


class DecisionOutcome(str, Enum):
    ACCEPT = "accept"
    OBSERVE = "observe"
    IGNORE = "ignore"
    ATTACH = "attach"
    STOP = "stop"
    UNAVAILABLE = "unavailable"


class DecisionStrength(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    NOT_ADMITTED = "not_admitted"


class SourceHealth(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class Trigger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source: Literal["github"] = "github"
    external_id: str
    kind: TriggerKind
    actor: str
    thread_keys: list[str] = Field(default_factory=list)
    occurred_at: int
    received_at: int
    payload: dict[str, Any]
    digest: str = ""

    @model_validator(mode="before")
    @classmethod
    def _seal_digest(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return data
        expected = payload_digest(payload)
        supplied = data.get("digest") or expected
        if supplied != expected:
            raise ValueError("trigger digest does not match payload")
        return {**data, "digest": supplied}


class Thread(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    source: Literal["github"] = "github"
    repository: str
    kind: ThreadKind
    external_ref: str
    last_trigger_at: int
    status: ThreadStatus = ThreadStatus.OPEN


class Channel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    thread_key: str
    kind: ChannelKind
    external_ref: str


class Subscription(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    ticket_id: str
    thread_key: str
    mode: SubscriptionMode
    created_at: int
    ended_at: int | None = None


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    trigger_ids: list[str]
    outcome: DecisionOutcome
    strength: DecisionStrength
    reason_codes: list[str] = Field(default_factory=list)
    policy_version: str
    mandate_id: str | None = None
    request_digest: str
    attempt: int = Field(default=1, ge=1)
    created_at: int


class SourceCursor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    cursor: str | None = None
    last_success_at: int | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    health: SourceHealth = SourceHealth.OK
