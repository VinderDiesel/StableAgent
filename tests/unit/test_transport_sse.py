"""P7 M5：SSE cursor / backpressure / 授权重检 / 脱敏。"""

from __future__ import annotations

import pytest

from stableagent.observability.events import DomainEvent, DomainEventKind
from stableagent.transport.sse import (
    SseBufferOverflow,
    encode_sse,
    iter_sse_from_events,
)


def _ev(eid: str, ts: int, **attrs: object) -> DomainEvent:
    return DomainEvent(
        id=eid,
        timestamp=ts,
        tenant_id="default",
        kind=DomainEventKind.EFFECT_SUCCEEDED,
        outcome="ok",
        payload_digest="d",
        attrs=dict(attrs),
        ticket_id="tk-1",
    )


def test_cursor_and_encode() -> None:
    events = [_ev("e1", 1), _ev("e2", 2, token="secret-value")]
    out = list(iter_sse_from_events(events, cursor="e1"))
    assert [e.id for e in out] == ["e2"]
    assert "secret-value" not in encode_sse(out[0])
    assert "[REDACTED]" in encode_sse(out[0]) or "token" in encode_sse(out[0])


def test_backpressure_and_auth_cutoff() -> None:
    events = [_ev(f"e{i}", i) for i in range(5)]
    with pytest.raises(SseBufferOverflow):
        list(iter_sse_from_events(events, max_buffer=2))

    allowed = {"ok": True}

    def check() -> bool:
        return allowed["ok"]

    gen = iter_sse_from_events(events, auth_check=check, max_buffer=10)
    first = next(gen)
    assert first.id == "e0"
    allowed["ok"] = False
    assert list(gen) == []
