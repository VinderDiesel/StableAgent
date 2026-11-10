"""P7 M5：RBAC 角色矩阵。"""

from __future__ import annotations

import pytest

from stableagent.control.rbac import (
    Permission,
    Principal,
    RbacDeniedError,
    Role,
    authorize,
    parse_roles,
)


def test_viewer_cannot_write() -> None:
    p = Principal(tenant_id="t", actor_id="v", roles=frozenset({Role.VIEWER}))
    authorize(p, Permission.READ_STATUS)
    with pytest.raises(RbacDeniedError):
        authorize(p, Permission.DECIDE_APPROVAL)
    with pytest.raises(RbacDeniedError):
        authorize(p, Permission.RUNTIME_CONTROL)


def test_reviewer_and_operator_and_admin() -> None:
    reviewer = Principal(
        tenant_id="t", actor_id="r", roles=frozenset({Role.REVIEWER})
    )
    authorize(reviewer, Permission.DECIDE_APPROVAL)
    with pytest.raises(RbacDeniedError):
        authorize(reviewer, Permission.STOP_TICKET)

    operator = Principal(
        tenant_id="t", actor_id="o", roles=frozenset({Role.OPERATOR})
    )
    authorize(operator, Permission.STOP_TICKET)
    authorize(operator, Permission.RUNTIME_CONTROL)
    with pytest.raises(RbacDeniedError):
        authorize(operator, Permission.MANAGE_MCP)

    admin = Principal(tenant_id="t", actor_id="a", roles=frozenset({Role.ADMIN}))
    authorize(admin, Permission.MANAGE_POLICY)
    authorize(admin, Permission.MANAGE_MCP)


def test_parse_roles() -> None:
    assert parse_roles("viewer,operator") == frozenset({Role.VIEWER, Role.OPERATOR})
    assert parse_roles("") == frozenset({Role.VIEWER})
