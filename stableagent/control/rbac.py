"""租户作用域 RBAC（P7 M5）：viewer / reviewer / operator / admin。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stableagent.observability import DEFAULT_TENANT


class Role(str, Enum):
    VIEWER = "viewer"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class Permission(str, Enum):
    READ_STATUS = "read_status"
    READ_TICKET = "read_ticket"
    READ_EVENTS = "read_events"
    DECIDE_APPROVAL = "decide_approval"
    STOP_TICKET = "stop_ticket"
    RETRY = "retry"
    REEVALUATE = "reevaluate"
    RUNTIME_CONTROL = "runtime_control"
    MANAGE_POLICY = "manage_policy"
    MANAGE_MCP = "manage_mcp"


_RANK = {
    Role.VIEWER: 0,
    Role.REVIEWER: 1,
    Role.OPERATOR: 2,
    Role.ADMIN: 3,
}

_PERMISSION_MIN_ROLE: dict[Permission, Role] = {
    Permission.READ_STATUS: Role.VIEWER,
    Permission.READ_TICKET: Role.VIEWER,
    Permission.READ_EVENTS: Role.VIEWER,
    Permission.DECIDE_APPROVAL: Role.REVIEWER,
    Permission.STOP_TICKET: Role.OPERATOR,
    Permission.RETRY: Role.OPERATOR,
    Permission.REEVALUATE: Role.OPERATOR,
    Permission.RUNTIME_CONTROL: Role.OPERATOR,
    Permission.MANAGE_POLICY: Role.ADMIN,
    Permission.MANAGE_MCP: Role.ADMIN,
}


class RbacError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class RbacDeniedError(RbacError):
    def __init__(self, reason: str) -> None:
        super().__init__("rbac.denied", reason)


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = DEFAULT_TENANT
    actor_id: str
    roles: frozenset[Role] = Field(default_factory=lambda: frozenset({Role.VIEWER}))

    @field_validator("actor_id")
    @classmethod
    def _actor_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("actor_id 不能为空")
        return value

    @field_validator("tenant_id")
    @classmethod
    def _tenant_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("tenant_id 不能为空")
        return value

    def max_rank(self) -> int:
        if not self.roles:
            return -1
        return max(_RANK[role] for role in self.roles)


def parse_roles(raw: str) -> frozenset[Role]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return frozenset({Role.VIEWER})
    return frozenset(Role(p) for p in parts)


def authorize(principal: Principal, permission: Permission) -> None:
    needed = _PERMISSION_MIN_ROLE[permission]
    if principal.max_rank() < _RANK[needed]:
        raise RbacDeniedError(
            f"actor {principal.actor_id} lacks {permission.value} "
            f"(need {needed.value}+)"
        )
