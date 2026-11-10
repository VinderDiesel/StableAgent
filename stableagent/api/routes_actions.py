"""写动作路由：经 Console，RBAC + 审计。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from stableagent.api.deps import get_state, principal_from_headers, require_principal
from stableagent.control.errors import ApprovalDecidedError, UnknownApprovalError
from stableagent.control.rbac import Permission, Principal
from stableagent.supervisor.console import CliError
from stableagent.supervisor.router import RouterPermissionError

router = APIRouter(prefix="/v1", tags=["actions"])


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    confirm: str | None = None


class ActorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str | None = None
    reason: str | None = None


def _map_cli_error(error: Exception) -> HTTPException:
    if isinstance(error, CliError):
        status = 400 if error.code in {1, 2, 3} else 500
        return HTTPException(
            status_code=status,
            detail={"code": f"cli.{error.code}", "reason": error.detail},
        )
    if isinstance(error, RouterPermissionError):
        return HTTPException(
            status_code=403,
            detail={"code": "operator.required", "reason": str(error)},
        )
    if isinstance(error, UnknownApprovalError):
        return HTTPException(
            status_code=404, detail={"code": "approval.not_found", "reason": str(error)}
        )
    if isinstance(error, ApprovalDecidedError):
        return HTTPException(
            status_code=409, detail={"code": "approval.decided", "reason": str(error)}
        )
    return HTTPException(status_code=500, detail={"code": "internal", "reason": str(error)})


@router.post("/approvals/{approval_id}/decide")
def decide_approval(
    approval_id: str,
    body: DecideBody,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    require_principal(request, principal, Permission.DECIDE_APPROVAL)
    state = get_state(request)
    confirm = body.confirm or approval_id
    try:
        if body.approved:
            result = state.console.approve(
                approval_id, actor=principal.actor_id, confirm=confirm
            )
        else:
            result = state.console.deny(
                approval_id, actor=principal.actor_id, confirm=confirm
            )
    except Exception as error:  # noqa: BLE001 — 映射为 HTTP
        raise _map_cli_error(error) from error
    state.audit.record(
        action="decide_approval",
        actor=principal.actor_id,
        tenant_id=principal.tenant_id,
        approval_id=approval_id,
        approved=body.approved,
    )
    return {"approval": result.model_dump(mode="json"), "approved": body.approved}


@router.post("/tickets/{ticket_id}/stop")
def stop_ticket(
    ticket_id: str,
    body: ActorBody,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    require_principal(request, principal, Permission.STOP_TICKET)
    state = get_state(request)
    confirm = body.confirm or ticket_id
    try:
        ticket = state.console.stop(
            ticket_id, actor=principal.actor_id, confirm=confirm, reason=body.reason
        )
    except Exception as error:  # noqa: BLE001
        raise _map_cli_error(error) from error
    state.audit.record(
        action="stop",
        actor=principal.actor_id,
        tenant_id=principal.tenant_id,
        ticket_id=ticket_id,
    )
    return {"ticket": ticket.model_dump(mode="json")}


@router.post("/tickets/{ticket_id}/retry")
def retry_ticket(
    ticket_id: str,
    body: ActorBody,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    require_principal(request, principal, Permission.RETRY)
    state = get_state(request)
    confirm = body.confirm or ticket_id
    try:
        result = state.console.retry(ticket_id, actor=principal.actor_id, confirm=confirm)
    except Exception as error:  # noqa: BLE001
        raise _map_cli_error(error) from error
    state.audit.record(
        action="retry",
        actor=principal.actor_id,
        tenant_id=principal.tenant_id,
        target_id=ticket_id,
    )
    return {"result": result.model_dump(mode="json")}


@router.post("/tickets/{ticket_id}/reevaluate")
def reevaluate(
    ticket_id: str,
    body: ActorBody,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    """reevaluate 目标为 trigger_id；路径参数名沿用资源 id。"""
    require_principal(request, principal, Permission.REEVALUATE)
    state = get_state(request)
    confirm = body.confirm or ticket_id
    try:
        decision = state.console.reevaluate(
            ticket_id, actor=principal.actor_id, confirm=confirm
        )
    except Exception as error:  # noqa: BLE001
        raise _map_cli_error(error) from error
    state.audit.record(
        action="reevaluate",
        actor=principal.actor_id,
        tenant_id=principal.tenant_id,
        trigger_id=ticket_id,
    )
    return {"decision": decision.model_dump(mode="json")}


@router.post("/runtime/pause")
def pause_runtime(
    body: ActorBody,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    require_principal(request, principal, Permission.RUNTIME_CONTROL)
    state = get_state(request)
    confirm = body.confirm or "pause"
    try:
        control = state.console.pause(
            actor=principal.actor_id, confirm=confirm, reason=body.reason
        )
    except Exception as error:  # noqa: BLE001
        raise _map_cli_error(error) from error
    state.audit.record(
        action="pause", actor=principal.actor_id, tenant_id=principal.tenant_id
    )
    return {"runtime": control.model_dump(mode="json")}


@router.post("/runtime/resume")
def resume_runtime(
    body: ActorBody,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    require_principal(request, principal, Permission.RUNTIME_CONTROL)
    state = get_state(request)
    confirm = body.confirm or "resume"
    try:
        control = state.console.resume(
            actor=principal.actor_id, confirm=confirm, reason=body.reason
        )
    except Exception as error:  # noqa: BLE001
        raise _map_cli_error(error) from error
    state.audit.record(
        action="resume", actor=principal.actor_id, tenant_id=principal.tenant_id
    )
    return {"runtime": control.model_dump(mode="json")}
