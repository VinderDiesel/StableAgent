"""A3 + L5 审计半边：四类决策各写恰好一行，行内含"决策当刻"的 policy_version 快照。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from stableagent.control.audit import AuditWriter
from stableagent.control.model import (
    Action,
    ApprovalRequest,
    ApprovalStatus,
    Decision,
    ExplicitRequest,
    Resource,
    resource_digest,
)
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds

REPOSITORY = "acme/widgets"

AUDIT_COLUMNS = {
    "id",
    "kind",
    "ticket_id",
    "assignment_id",
    "mandate_id",
    "action",
    "resource_digest",
    "policy_version",
    "decision",
    "reason",
    "created_at",
}
APPROVAL_COLUMNS = {
    "id",
    "ticket_id",
    "assignment_id",
    "action",
    "resource_digest",
    "reason",
    "requested_at",
    "status",
    "decided_by",
    "decided_at",
}
STABLE_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


def _config(**overrides) -> StaticPolicyConfig:
    base: dict[str, object] = {
        "repository": REPOSITORY,
        "actors": frozenset({"octocat"}),
        "grantable_actions": frozenset(Action) - {Action.ENABLE_AUTO_MERGE},
        "allowed_commands": ["pytest"],
        "allowed_mcp": ["context7:resolve-library-id"],
        "default_validity_ms": 60_000,
    }
    base.update(overrides)
    return StaticPolicyConfig(**base)


def _request(**overrides) -> ExplicitRequest:
    base: dict[str, object] = {
        "repository": REPOSITORY,
        "requester": "octocat",
        "mentioned": True,
        "stable_id": f"{REPOSITORY}#42",
        "text": "@agent 跑一遍测试",
    }
    base.update(overrides)
    return ExplicitRequest(**base)


def _policy(clock: FixedClock, **overrides) -> StaticPolicy:
    return StaticPolicy(_config(**overrides), clock=clock, ids=SequenceIds())


def _mandate(policy: StaticPolicy):
    return policy.create_mandate(
        policy.admit_explicit(_request()),
        actions=[Action.RUN_COMMAND],
        allowed_commands=["pytest"],
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock):
    handle = Store(tmp_path / "control.db", clock=clock)
    yield handle
    handle.close()


def test_each_decision_kind_writes_exactly_one_row(store: Store, clock: FixedClock) -> None:
    """四类决策各一次 → 4 行，kind/decision/reason 与各槽位齐全（A3）。"""
    policy = _policy(clock)
    audit = AuditWriter(policy, store, clock=clock, ids=SequenceIds())

    admission = policy.admit_explicit(_request())
    mandate = _mandate(policy)
    resource = Resource(kind="command", command="pytest")
    authorization = policy.authorize(mandate, Action.RUN_COMMAND, resource)
    revalidation = policy.revalidate(mandate, when="launch")
    approval = ApprovalRequest(
        id="ap-0001",
        ticket_id="tk-0001",
        assignment_id="as-0001",
        action=Action.RUN_COMMAND,
        resource_digest=resource_digest(Action.RUN_COMMAND, resource),
        reason="human_required",
        requested_at=clock.now_ms(),
        status=ApprovalStatus.APPROVED,
        decided_by="octocat",
        decided_at=clock.now_ms(),
    )

    audit.admitted(admission)
    audit.authorized(
        mandate, Action.RUN_COMMAND, resource, authorization, ticket_id=approval.ticket_id
    )
    audit.revalidated(mandate, "launch", revalidation, ticket_id=approval.ticket_id)
    audit.decided(
        approval, Decision(allowed=True, reason="approved", policy_version=mandate.policy_version)
    )

    rows = store.list_audit()
    assert [row["kind"] for row in rows] == ["admit", "authorize", "revalidate", "approve"]
    assert len({row["id"] for row in rows}) == 4
    for row in rows:
        assert set(row) == AUDIT_COLUMNS
        assert row["created_at"] == clock.now_ms()
        assert row["policy_version"] == "p5-v1"
        assert row["decision"] == "allow"

    admit, authorize, revalidate, approve = rows
    # 受理行没有授权对象可引用，槽位留空而不是伪造
    assert (admit["mandate_id"], admit["action"], admit["resource_digest"]) == (None, None, None)
    assert admit["reason"] == "admitted"
    assert authorize["mandate_id"] == mandate.id
    assert authorize["ticket_id"] == "tk-0001"
    assert authorize["action"] == "run_command"
    assert authorize["resource_digest"] == resource_digest(Action.RUN_COMMAND, resource)
    assert authorize["reason"] == "granted"
    # 复检行的 action 槽记的是时机名：kind 已固定为 revalidate，时机是这一行唯一的区分维度
    assert (revalidate["action"], revalidate["resource_digest"]) == ("launch", None)
    assert revalidate["mandate_id"] == mandate.id
    assert (approve["ticket_id"], approve["assignment_id"]) == ("tk-0001", "as-0001")
    assert approve["action"] == "run_command"
    assert approve["resource_digest"] == approval.resource_digest
    assert approve["reason"] == "approved"


def test_audit_snapshots_policy_version(store: Store, clock: FixedClock) -> None:
    """快照取决策当刻的策略版本：两个 config 实例先后决策 → 两行版本不同。"""
    first, second = _policy(clock), _policy(clock, policy_version="p5-v2")
    audit_ids = SequenceIds()  # 同一进程共用一个 ID 工厂：审计行 id 唯一
    writers = {
        "p5-v1": AuditWriter(first, store, clock=clock, ids=audit_ids),
        "p5-v2": AuditWriter(second, store, clock=clock, ids=audit_ids),
    }
    for policy, version in ((first, "p5-v1"), (second, "p5-v2")):
        admission = policy.admit_explicit(_request())
        writers[version].admitted(admission)
        mandate = policy.create_mandate(
            admission, actions=[Action.RUN_COMMAND], allowed_commands=["pytest"]
        )
        resource = Resource(kind="command", command="pytest")
        writers[version].authorized(
            mandate,
            Action.RUN_COMMAND,
            resource,
            policy.authorize(mandate, Action.RUN_COMMAND, resource),
        )

    assert [row["policy_version"] for row in store.list_audit(kind="admit")] == ["p5-v1", "p5-v2"]
    assert [row["policy_version"] for row in store.list_audit(kind="authorize")] == [
        "p5-v1",
        "p5-v2",
    ]

    # 被判定 Mandate 自述的版本不是快照来源：p5-v1 策略判定一份自称 p4-local-v1 的授权，
    # 行里记的仍是"我在跑哪一版策略"，否则版本漂移在审计上不可见。
    smuggled = _mandate(first).model_copy(update={"policy_version": "p4-local-v1"})
    resource = Resource(kind="command", command="pytest")
    decision = first.authorize(smuggled, Action.RUN_COMMAND, resource)
    assert decision.policy_version == "p4-local-v1"
    writers["p5-v1"].authorized(smuggled, Action.RUN_COMMAND, resource, decision)
    last = store.list_audit(kind="authorize")[-1]
    assert (last["policy_version"], last["reason"]) == ("p5-v1", "granted")

    # 换策略不改史：后续决策只新增行，已有行的快照保持原值
    third = _policy(clock, policy_version="p5-v3")
    AuditWriter(third, store, clock=clock, ids=audit_ids).admitted(third.admit_explicit(_request()))
    assert [row["policy_version"] for row in store.list_audit()] == [
        "p5-v1",
        "p5-v1",
        "p5-v2",
        "p5-v2",
        "p5-v1",
        "p5-v3",
    ]


def test_audit_reason_is_stable_code_not_free_text(store: Store, clock: FixedClock) -> None:
    """每类拒绝的 reason 都是稳定码：可正则匹配、可比较，不掺自由文本。"""
    policy = _policy(clock, default_validity_ms=1_000)
    audit = AuditWriter(policy, store, clock=clock, ids=SequenceIds())

    noisy = "拒绝理由不该带这些：中文说明、冒号 与空格"
    audit.admitted(policy.admit_explicit(_request(requester="stranger", text=noisy)))
    mandate = _mandate(policy)
    for action, resource in (
        (Action.PUSH_BRANCH, Resource(kind="branch", extra={"name": "sa-tk-1-work"})),
        (Action.RUN_COMMAND, Resource(kind="command", command="rm -rf /")),
    ):
        audit.authorized(mandate, action, resource, policy.authorize(mandate, action, resource))
    clock.advance(1_001)
    audit.revalidated(mandate, "mcp_call", policy.revalidate(mandate, when="mcp_call"))

    rows = store.list_audit()
    assert [row["reason"] for row in rows] == [
        "actor_not_allowlisted",
        "not_granted",
        "command_not_allowed",
        "mandate_expired",
    ]
    assert [row["decision"] for row in rows] == ["deny"] * 4
    for row in rows:
        assert STABLE_CODE.fullmatch(row["reason"]), row["reason"]
    # 外部文本（含正文里的授权诉求）不进审计表：只有摘要与稳定码落库（L4）
    assert "拒绝理由" not in json.dumps(rows, ensure_ascii=False)
    assert noisy not in json.dumps(rows, ensure_ascii=False)


def test_approval_rows_roundtrip_and_audit_filters(store: Store, clock: FixedClock) -> None:
    """审批行的读写口径 + 审计行的 ticket/kind 过滤（Task 7 的仓储前提）。"""
    digest = resource_digest(
        Action.PUSH_BRANCH, Resource(kind="branch", extra={"name": "sa-tk-1-work"})
    )
    store.insert_approval(
        id="ap-0001",
        ticket_id="tk-0001",
        action=Action.PUSH_BRANCH.value,
        resource_digest=digest,
        reason="human_required",
        requested_at=clock.now_ms(),
    )
    row = store.get_approval("ap-0001")
    assert row is not None
    assert set(row) == APPROVAL_COLUMNS
    assert (row["status"], row["assignment_id"], row["decided_by"], row["decided_at"]) == (
        "pending",
        None,
        None,
        None,
    )
    assert store.get_approval("ap-missing") is None

    assert (
        store.update_approval(
            "ap-0001",
            status=ApprovalStatus.APPROVED.value,
            decided_by="octocat",
            decided_at=clock.now_ms(),
        )
        == 1
    )
    assert (
        store.update_approval(
            "ap-missing",
            status=ApprovalStatus.DENIED.value,
            decided_by="octocat",
            decided_at=clock.now_ms(),
        )
        == 0
    )
    decided = store.get_approval("ap-0001")
    assert decided is not None
    assert (decided["status"], decided["decided_by"]) == ("approved", "octocat")

    store.insert_approval(
        id="ap-0002",
        ticket_id="tk-0001",
        assignment_id="as-0001",
        action=Action.PUSH_BRANCH.value,
        resource_digest=digest,
        reason="human_required",
        requested_at=clock.now_ms() + 1,
    )
    assert [item["id"] for item in store.list_approvals("tk-0001")] == ["ap-0001", "ap-0002"]
    assert [item["id"] for item in store.list_approvals("tk-0001", status="pending")] == ["ap-0002"]
    assert store.list_approvals("tk-other") == []

    audit = AuditWriter(_policy(clock), store, clock=clock, ids=SequenceIds())
    audit.admitted(_policy(clock).admit_explicit(_request()))
    audit.decided(
        ApprovalRequest(
            id="ap-0002",
            ticket_id="tk-0001",
            action=Action.PUSH_BRANCH,
            resource_digest=digest,
            reason="human_required",
            requested_at=clock.now_ms() + 1,
            status=ApprovalStatus.DENIED,
        ),
        Decision(allowed=False, reason="human_denied", policy_version="p5-v1"),
    )
    assert [row["kind"] for row in store.list_audit(ticket_id="tk-0001")] == ["approve"]
    assert [row["decision"] for row in store.list_audit(kind="admit")] == ["allow"]
    assert store.list_audit(ticket_id="tk-none") == []
