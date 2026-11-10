"""Ticket 时间线 SSE。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from stableagent.api.deps import get_state, principal_from_headers, require_principal
from stableagent.control.rbac import Permission, Principal, authorize
from stableagent.control.tenancy import assert_same_tenant
from stableagent.transport.sse import (
    SseBufferOverflow,
    SseEvent,
    encode_sse,
    iter_sse_from_events,
)

router = APIRouter(prefix="/v1", tags=["events"])


@router.get("/events/tickets/{ticket_id}")
def ticket_events_sse(
    ticket_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(principal_from_headers)],
    cursor: Annotated[str | None, Query()] = None,
    max_buffer: Annotated[int, Query(ge=1, le=10_000)] = 256,
) -> StreamingResponse:
    require_principal(request, principal, Permission.READ_EVENTS)
    state = get_state(request)

    try:
        state.console.ticket(ticket_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail={"code": "ticket.not_found", "id": ticket_id}
        ) from error

    events = [
        event
        for event in state.store.list_domain_events(
            ticket_id=ticket_id, tenant_id=state.tenant_id, limit=max_buffer + 1
        )
        if event.tenant_id == state.tenant_id
    ]

    def _auth_ok() -> bool:
        try:
            assert_same_tenant(state.tenant_id, principal.tenant_id)
            authorize(principal, Permission.READ_EVENTS)
            return True
        except Exception:  # noqa: BLE001
            return False

    try:
        stream_events = list(
            iter_sse_from_events(
                events,
                cursor=cursor,
                max_buffer=max_buffer,
                auth_check=_auth_ok,
            )
        )
    except SseBufferOverflow as error:
        raise HTTPException(
            status_code=429, detail={"code": "sse.backpressure", "reason": error.reason}
        ) from error

    def generate() -> Iterator[str]:
        if not _auth_ok():
            return
        for item in stream_events:
            if not _auth_ok():
                return
            yield encode_sse(item)
        yield encode_sse(
            SseEvent(
                id=f"end-{ticket_id}",
                event="stream.end",
                data={"ticket_id": ticket_id},
            )
        )

    return StreamingResponse(generate(), media_type="text/event-stream")
