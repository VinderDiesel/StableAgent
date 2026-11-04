"""B1 前提：resource_digest 的纯函数口径、Action 集合形状与审批记录的必填字段。"""

from __future__ import annotations

import hashlib
import json

import pydantic
import pytest

from stableagent.control.model import (
    Action,
    Admission,
    ApprovalRequest,
    ApprovalStatus,
    Decision,
    Resource,
    resource_digest,
)

# 规格 2.1 的列举顺序即枚举顺序：测试要能指出"多一个/少一个/换顺序"三种漂移。
SPEC_ACTIONS = (
    "prepare_worktree",
    "read_repository",
    "write_repository",
    "run_command",
    "call_mcp_tool",
    "create_branch",
    "commit_changes",
    "push_branch",
    "create_pull_request",
    "comment_issue",
    "comment_pull_request",
    "request_review",
    "enable_auto_merge",
)


def test_action_enum_has_exactly_spec_thirteen_members() -> None:
    assert [action.value for action in Action] == list(SPEC_ACTIONS)
    assert len(Action) == 13
    # enable_auto_merge 在枚举里只服务"点名拒绝"的可测性；它不进入任何授权集合（L6 由 Task 5 钉）
    assert Action.ENABLE_AUTO_MERGE.value == "enable_auto_merge"
    assert all(action.value.replace("_", "").islower() for action in Action)


def test_resource_digest_is_stable_under_key_order() -> None:
    """extra 键乱序、字段声明序不同都不得改变 digest：批准绑定的正是这个值（B1）。"""
    first = Resource(kind="command", command="pytest", extra={"cwd": "/w", "shell": False})
    second = Resource(kind="command", command="pytest", extra={"shell": False, "cwd": "/w"})
    assert resource_digest(Action.RUN_COMMAND, first) == resource_digest(Action.RUN_COMMAND, second)
    documented = hashlib.sha256(
        json.dumps(
            {"action": Action.RUN_COMMAND.value, "resource": first.model_dump()},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    assert resource_digest(Action.RUN_COMMAND, first) == documented
    assert len(documented) == 64


def test_resource_digest_changes_on_action_or_resource() -> None:
    base = Resource(kind="path", path="src/a.py")
    digest = resource_digest(Action.WRITE_REPOSITORY, base)
    assert digest != resource_digest(Action.READ_REPOSITORY, base)
    assert digest != resource_digest(
        Action.WRITE_REPOSITORY, Resource(kind="path", path="src/b.py")
    )
    assert digest != resource_digest(
        Action.WRITE_REPOSITORY, Resource(kind="path", path="src/a.py", extra={"v": 1})
    )
    assert digest != resource_digest(
        Action.WRITE_REPOSITORY, Resource(kind="file", path="src/a.py")
    )


def test_approval_request_requires_ticket_and_digest() -> None:
    with pytest.raises(pydantic.ValidationError):
        ApprovalRequest(id="ap-1", action=Action.PUSH_BRANCH, requested_at=1)
    with pytest.raises(pydantic.ValidationError):
        ApprovalRequest(
            id="ap-1",
            ticket_id="tk-1",
            action=Action.PUSH_BRANCH,
            reason="首次推送外部分支",
            requested_at=1,
        )
    request = ApprovalRequest(
        id="ap-1",
        ticket_id="tk-1",
        action=Action.PUSH_BRANCH,
        resource_digest="d" * 64,
        reason="首次推送外部分支",
        requested_at=1_700_000_000_000,
    )
    assert request.status is ApprovalStatus.PENDING
    assert (request.assignment_id, request.decided_by, request.decided_at) == (None, None, None)
    assert [status.value for status in ApprovalStatus] == [
        "pending",
        "approved",
        "denied",
        "revoked",
    ]


def test_admission_and_decision_are_plain_records() -> None:
    admission = Admission(
        admitted=False,
        reason_code="actor_not_allowlisted",
        requester="octocat",
        repository="acme/widgets",
        stable_id="acme/widgets#42",
        request_digest="a" * 64,
    )
    decision = Decision(allowed=False, reason="not_granted", policy_version="p5-v1")
    assert admission.model_dump()["reason_code"] == "actor_not_allowlisted"
    assert decision.model_dump() == {
        "allowed": False,
        "reason": "not_granted",
        "policy_version": "p5-v1",
    }
