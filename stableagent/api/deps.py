"""FastAPI 依赖：Header Principal + 租户绑定校验。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Request

from stableagent.api.state import AppState
from stableagent.control.rbac import (
    Permission,
    Principal,
    RbacDeniedError,
    authorize,
    parse_roles,
)
from stableagent.control.tenancy import TenantMismatchError, assert_same_tenant
from stableagent.observability import DEFAULT_TENANT


def get_state(request: Request) -> AppState:
    state = request.app.state.api
    assert isinstance(state, AppState)
    return state


def principal_from_headers(
    *,
    x_tenant_id: Annotated[str, Header()] = DEFAULT_TENANT,
    x_actor_id: Annotated[str, Header()] = "",
    x_roles: Annotated[str, Header()] = "viewer",
) -> Principal:
    if not x_actor_id:
        raise HTTPException(status_code=401, detail={"code": "auth.missing_actor"})
    try:
        roles = parse_roles(x_roles)
        return Principal(tenant_id=x_tenant_id, actor_id=x_actor_id, roles=roles)
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail={"code": "auth.invalid", "reason": str(error)}
        ) from error


def require_principal(
    request: Request,
    principal: Principal,
    permission: Permission,
) -> Principal:
    state = get_state(request)
    try:
        assert_same_tenant(state.tenant_id, principal.tenant_id)
        authorize(principal, permission)
    except TenantMismatchError as error:
        raise HTTPException(
            status_code=403, detail={"code": error.code, "reason": error.reason}
        ) from error
    except RbacDeniedError as error:
        raise HTTPException(
            status_code=403, detail={"code": error.code, "reason": error.reason}
        ) from error
    return principal
