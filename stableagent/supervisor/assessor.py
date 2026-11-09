"""推断候选评估：窄协议 + observe 下的确定性规则（规格 2.3、WP-6.3 第 3 条）。

评估器只回答"若开启 auto 是否接受"，不写库、不创建 Ticket；失败由 Router 记 unavailable。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from stableagent.supervisor.model import Thread, ThreadStatus, Trigger, TriggerKind


class AssessmentContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: Trigger
    thread: Thread | None = None
    has_live_own_subscription: bool = False
    has_live_context_subscription: bool = False
    existing_ticket_id: str | None = None
    policy_version: str


class CandidateAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    would_accept: bool
    reason_codes: list[str] = Field(default_factory=list)


@runtime_checkable
class CandidateAssessor(Protocol):
    def assess(self, context: AssessmentContext) -> CandidateAssessment: ...


class ObserveOnlyAssessor:
    """Beta 的确定性评估器；规则顺序即 reason code 的优先级。"""

    def assess(self, context: AssessmentContext) -> CandidateAssessment:
        if context.trigger.kind is TriggerKind.CI_STATUS:
            return CandidateAssessment(would_accept=False, reason_codes=["ci_context_only"])
        if context.thread is not None and context.thread.status is not ThreadStatus.OPEN:
            return CandidateAssessment(would_accept=False, reason_codes=["thread_not_open"])
        if context.existing_ticket_id is not None:
            return CandidateAssessment(would_accept=False, reason_codes=["existing_ticket_context"])
        if context.has_live_own_subscription:
            return CandidateAssessment(would_accept=False, reason_codes=["already_owned"])
        if context.has_live_context_subscription:
            return CandidateAssessment(would_accept=False, reason_codes=["context_only"])
        return CandidateAssessment(would_accept=True, reason_codes=["inferred_candidate"])
