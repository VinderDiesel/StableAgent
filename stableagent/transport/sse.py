"""SSE 时间线：cursor、重连、缓冲上限、授权重检、脱敏。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stableagent.observability.events import DomainEvent
from stableagent.observability.redaction import redact_mapping


class SseEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class SseBufferOverflow(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


AuthCheck = Callable[[], bool]
"""返回 False 表示授权已失效，应终止流。"""


def encode_sse(event: SseEvent) -> str:
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.event}\ndata: {payload}\n\n"


def domain_event_to_sse(event: DomainEvent) -> SseEvent:
    return SseEvent(
        id=event.id,
        event=event.kind.value,
        data=redact_mapping(
            {
                "outcome": event.outcome,
                "reason_code": event.reason_code,
                "ticket_id": event.ticket_id,
                "tenant_id": event.tenant_id,
                "attrs": event.attrs,
                "payload_digest": event.payload_digest,
                "timestamp": event.timestamp,
            }
        ),
    )


def iter_sse_from_events(
    events: list[DomainEvent],
    *,
    cursor: str | None = None,
    max_buffer: int = 256,
    auth_check: AuthCheck | None = None,
) -> Iterator[SseEvent]:
    """按 id 排序后从 cursor 之后重放；缓冲超限抛错（backpressure）。"""
    ordered = sorted(events, key=lambda e: (e.timestamp, e.id))
    if cursor is not None:
        idx = next((i for i, e in enumerate(ordered) if e.id == cursor), None)
        ordered = ordered[idx + 1 :] if idx is not None else ordered
    if len(ordered) > max_buffer:
        raise SseBufferOverflow(
            f"pending events {len(ordered)} exceed max_buffer {max_buffer}"
        )
    for event in ordered:
        if auth_check is not None and not auth_check():
            return
        yield domain_event_to_sse(event)
