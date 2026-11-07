"""GitHub 外部写的 outbox handler：先复检、再审批、最后才碰网络（步骤 3.10）。

三条 handler 共用一个顺序工厂 `_write_effect`，顺序固定且不可交换：

1. 读 Ticket / Mandate——两者缺失都是永久失败（重放不会长出这两行）；
2. `policy.revalidate(mandate, when="external_write")` + 审计行（L5）；
3. `ApprovalGate.check`——它自己写 authorize 审计行，高危动作没有全等批准就不放行；
4. 判定不过 → 结构化 BLOCKED Report（含 reason / status_code / policy_version）+ 永久失败；
5. 调 `GitHubClient`（PR handler 先 `await push_branch(head)`，若调用方注入）；
6. `GitHubApiError` → FAILED Report 保留状态码与三分类；retryable 原样抛出走退避，
   其余转成永久失败——重放只会再得一次同样的 4xx。

payload 形状（不含任何凭据，L7）：
`{ticket_id, assignment_id, stable_id, repository, body}`，另加
comment/review 的 `url`、PR 的 `head/base/title`、review 的 `reviewers`。
`github_resource` 是审批方与 handler 共用的资源形状：两边必须算出同一个 digest。
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from stableagent.control.approval import ApprovalGate
from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action, Resource
from stableagent.control.policy import StaticPolicy
from stableagent.integrations.errors import GitHubApiError
from stableagent.integrations.github_client import GitHubClient
from stableagent.orchestration.errors import PermanentEffectError
from stableagent.orchestration.model import (
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Report,
    ReportKind,
    SystemClock,
    Uuid4Id,
)
from stableagent.orchestration.outbox import EffectHandler
from stableagent.orchestration.store import Store

PushBranch = Callable[[str], Awaitable[None]]


def github_effect_key(kind: EffectKind, stable_id: str, digest: str) -> str:
    """幂等键：外部稳定 id 打头，加请求摘要；同一次写入重放命中同一行。"""
    return f"{kind.value}:{stable_id}:{digest}"


def github_resource(action: Action, payload: Mapping[str, Any]) -> Resource:
    """审批绑定用的资源形状；正文只以 sha256 进 `extra`，不落原文（L4/L7）。"""
    repository = _text(payload, "repository")
    if action is Action.COMMENT_ISSUE:
        return Resource(
            kind="comment",
            url=_text(payload, "url"),
            extra={
                "repository": repository,
                "body_sha256": _text_digest(_text(payload, "body")),
            },
        )
    if action is Action.CREATE_PULL_REQUEST:
        return Resource(
            kind="pull_request",
            extra={
                "repository": repository,
                "head": _text(payload, "head"),
                "base": _text(payload, "base"),
                "title_sha256": _text_digest(_text(payload, "title")),
                "body_sha256": _text_digest(_text(payload, "body")),
            },
        )
    if action is Action.REQUEST_REVIEW:
        return Resource(
            kind="review",
            url=_text(payload, "url"),
            extra={
                "repository": repository,
                "reviewers": sorted(_reviewers(payload)),
            },
        )
    raise PermanentEffectError(f"not_a_github_write:{action.value}")


def github_comment_handler(
    *,
    store: Store,
    policy: StaticPolicy,
    audit: AuditWriter,
    gate: ApprovalGate,
    client: GitHubClient,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
) -> EffectHandler:
    async def write(effect: Effect) -> None:
        payload = effect.payload
        await client.create_comment(
            repository=_text(payload, "repository"),
            url=_text(payload, "url"),
            body=_text(payload, "body"),
        )

    return _write_effect(
        kind=EffectKind.GITHUB_COMMENT,
        action=Action.COMMENT_ISSUE,
        store=store,
        policy=policy,
        audit=audit,
        gate=gate,
        clock=clock or SystemClock(),
        ids=ids or Uuid4Id(),
        write=write,
    )


def github_pull_request_handler(
    *,
    store: Store,
    policy: StaticPolicy,
    audit: AuditWriter,
    gate: ApprovalGate,
    client: GitHubClient,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
    push_branch: PushBranch | None = None,
) -> EffectHandler:
    async def write(effect: Effect) -> None:
        payload = effect.payload
        head = _text(payload, "head")
        if push_branch is not None:
            await push_branch(head)
        await client.create_pull_request(
            repository=_text(payload, "repository"),
            head=head,
            base=_text(payload, "base"),
            title=_text(payload, "title"),
            body=_text(payload, "body"),
        )

    return _write_effect(
        kind=EffectKind.GITHUB_PULL_REQUEST,
        action=Action.CREATE_PULL_REQUEST,
        store=store,
        policy=policy,
        audit=audit,
        gate=gate,
        clock=clock or SystemClock(),
        ids=ids or Uuid4Id(),
        write=write,
    )


def github_request_review_handler(
    *,
    store: Store,
    policy: StaticPolicy,
    audit: AuditWriter,
    gate: ApprovalGate,
    client: GitHubClient,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
) -> EffectHandler:
    async def write(effect: Effect) -> None:
        payload = effect.payload
        await client.request_review(
            repository=_text(payload, "repository"),
            url=_text(payload, "url"),
            reviewers=_reviewers(payload),
        )

    return _write_effect(
        kind=EffectKind.GITHUB_REQUEST_REVIEW,
        action=Action.REQUEST_REVIEW,
        store=store,
        policy=policy,
        audit=audit,
        gate=gate,
        clock=clock or SystemClock(),
        ids=ids or Uuid4Id(),
        write=write,
    )


def _write_effect(
    *,
    kind: EffectKind,
    action: Action,
    store: Store,
    policy: StaticPolicy,
    audit: AuditWriter,
    gate: ApprovalGate,
    clock: Clock,
    ids: IdFactory,
    write: Callable[[Effect], Awaitable[None]],
) -> EffectHandler:
    async def handle(effect: Effect) -> None:
        payload = effect.payload
        ticket_id = _text(payload, "ticket_id")
        assignment_id = _text(payload, "assignment_id")
        ticket = store.get_ticket(ticket_id)
        if ticket is None:
            raise PermanentEffectError(f"ticket_missing:{ticket_id}")
        mandate = store.get_mandate(ticket.mandate_id)
        if mandate is None:
            raise PermanentEffectError(f"mandate_missing:{ticket.mandate_id}")

        revalidation = policy.revalidate(mandate, when="external_write")
        audit.revalidated(
            mandate,
            "external_write",
            revalidation,
            ticket_id=ticket.id,
            assignment_id=assignment_id,
        )
        if not revalidation.allowed:
            _park(
                store,
                ids,
                clock,
                ticket.id,
                assignment_id,
                revalidation.reason,
                revalidation.policy_version,
            )
            raise PermanentEffectError(f"{revalidation.reason}:external_write")

        authorization = gate.check(
            mandate, action, github_resource(action, payload), ticket_id=ticket.id
        )
        if not authorization.allowed:
            _park(
                store,
                ids,
                clock,
                ticket.id,
                assignment_id,
                authorization.reason,
                authorization.policy_version,
            )
            raise PermanentEffectError(f"{authorization.reason}:{action.value}")

        try:
            await write(effect)
        except GitHubApiError as exc:
            _report(
                store,
                ids,
                clock,
                ticket.id,
                assignment_id,
                ReportKind.FAILED,
                f"github write failed: {exc.category}/{exc.status_code}",
                {"status_code": exc.status_code, "category": exc.category},
            )
            if exc.category == "retryable":
                raise
            raise PermanentEffectError(f"{exc.category}:{exc.status_code}") from exc

    return handle


def _park(
    store: Store,
    ids: IdFactory,
    clock: Clock,
    ticket_id: str,
    assignment_id: str,
    reason: str,
    policy_version: str,
) -> None:
    _report(
        store,
        ids,
        clock,
        ticket_id,
        assignment_id,
        ReportKind.BLOCKED,
        f"github write parked: {reason}",
        {"reason": reason, "status_code": None, "policy_version": policy_version},
    )


def _report(
    store: Store,
    ids: IdFactory,
    clock: Clock,
    ticket_id: str,
    assignment_id: str,
    kind: ReportKind,
    summary: str,
    details: dict[str, Any],
) -> None:
    store.insert_report(
        Report(
            id=ids.new_id("rp"),
            ticket_id=ticket_id,
            assignment_id=assignment_id,
            kind=kind,
            summary=summary,
            created_at=clock.now_ms(),
            details=details,
        )
    )


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PermanentEffectError(f"payload_missing:{key}")
    return value


def _reviewers(payload: Mapping[str, Any]) -> list[str]:
    reviewers = payload.get("reviewers")
    if not isinstance(reviewers, list) or not all(
        isinstance(item, str) and item for item in reviewers
    ):
        raise PermanentEffectError("payload_missing:reviewers")
    return list(reviewers)


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
