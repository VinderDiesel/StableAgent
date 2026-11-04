"""控制层数据：Action / Resource / Admission / Decision / ApprovalRequest 与 resource_digest。

纯数据 + 纯函数，不含判定逻辑（判定在 `control.policy`），也不落库（事实源是
`orchestration.Store` 的 `mandates` / `approval_requests` / `authorization_audit`）。
依赖方向锁死为 control → orchestration → core：本模块不 import 执行层与集成层（ADR-05 决策 1）。
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Action(str, Enum):
    """规格 2.1 的全部可见副作用；顺序即规格列举顺序。"""

    PREPARE_WORKTREE = "prepare_worktree"
    READ_REPOSITORY = "read_repository"
    WRITE_REPOSITORY = "write_repository"
    RUN_COMMAND = "run_command"
    CALL_MCP_TOOL = "call_mcp_tool"
    CREATE_BRANCH = "create_branch"
    COMMIT_CHANGES = "commit_changes"
    PUSH_BRANCH = "push_branch"
    CREATE_PULL_REQUEST = "create_pull_request"
    COMMENT_ISSUE = "comment_issue"
    COMMENT_PULL_REQUEST = "comment_pull_request"
    REQUEST_REVIEW = "request_review"
    # 只服务"点名拒绝"的可测性：它永不进入授权集合与 EffectKind（L6，ADR-05 决策 5）
    ENABLE_AUTO_MERGE = "enable_auto_merge"


class Resource(BaseModel):
    """动作的对象。五个可选槽位按 kind 取用，其余细节进 extra 并参与 digest。"""

    kind: str
    path: str | None = None
    command: str | None = None
    server: str | None = None
    tool: str | None = None
    url: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ExplicitRequest(BaseModel):
    """受理门的最小输入形状：GitHub 富字段（issue/PR/comment URL、source ID）归 M3 扩展。

    `text` 是外部文本，只进 `request_digest`，绝不参与判定（L4）。
    """

    repository: str
    requester: str
    mentioned: bool
    stable_id: str
    text: str


class Admission(BaseModel):
    """受理门的结果：失败也带稳定 reason_code，便于审计与回写文案。"""

    admitted: bool
    reason_code: str
    requester: str
    repository: str
    stable_id: str
    request_digest: str


class Decision(BaseModel):
    """authorize / revalidate 的结果，含判定时的 policy_version 快照。"""

    allowed: bool
    reason: str
    policy_version: str


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"


class ApprovalRequest(BaseModel):
    """与 `approval_requests` 表列一一对应；ID 与时间由调用方注入（沿用 P4 记录风格）。"""

    id: str
    ticket_id: str
    assignment_id: str | None = None
    action: Action
    resource_digest: str
    reason: str
    requested_at: int
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    decided_at: int | None = None


def resource_digest(action: Action, resource: Resource) -> str:
    """`(action, resource)` 的不可变指纹：规范化 JSON（排序键、不转义非 ASCII）的 sha256。

    批准绑定的是这个值，所以参数或资源任一变化都会得到新 digest，旧批准随之失效（B1）。
    """
    payload = json.dumps(
        {"action": action.value, "resource": resource.model_dump()},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
