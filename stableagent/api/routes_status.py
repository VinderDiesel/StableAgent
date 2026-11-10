"""只读监管路由。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from stableagent.api.deps import get_state, principal_from_headers, require_principal
from stableagent.control.rbac import Permission, Principal

router = APIRouter(prefix="/v1", tags=["status"])


@router.get("/status")
def get_status(
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    require_principal(request, principal, Permission.READ_STATUS)
    state = get_state(request)
    view = state.console.status()
    return {
        "tenant_id": state.tenant_id,
        "runtime": view.runtime.model_dump(mode="json"),
        "sections": view.sections.model_dump(mode="json"),
    }


@router.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    require_principal(request, principal, Permission.READ_TICKET)
    state = get_state(request)
    try:
        detail = state.console.ticket(ticket_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail={"code": "ticket.not_found", "id": ticket_id}
        ) from error
    return detail.model_dump(mode="json")


@router.get("/threads/{thread_id}")
def get_thread(
    thread_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
) -> dict[str, Any]:
    require_principal(request, principal, Permission.READ_TICKET)
    state = get_state(request)
    events = state.console.thread(thread_id)
    return {"thread_id": thread_id, "events": [e.model_dump(mode="json") for e in events]}


@router.get("/ui")
def attention_ui() -> dict[str, str]:
    """占位：静态页由 app mount；此端点便于探测。"""
    return {"ui": "/"}
