"""P6 领域记录：digest 规范化、Trigger 不可变、枚举值钉死。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
    canonical_json,
    payload_digest,
)


def _trigger(**overrides: object) -> Trigger:
    data: dict[str, object] = {
        "id": "tg-0001",
        "external_id": "issue:1",
        "kind": TriggerKind.ISSUE,
        "actor": "alice",
        "thread_keys": ["issue:acme/widgets#1"],
        "occurred_at": 1_700_000_000_000,
        "received_at": 1_700_000_000_001,
        "payload": {"number": 1, "title": "add rate limiter"},
    }
    data.update(overrides)
    return Trigger(**data)


def test_canonical_json_and_digest_are_order_independent() -> None:
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert payload_digest({"b": 2, "a": 1}) == payload_digest({"a": 1, "b": 2})
    assert payload_digest({"a": 1}) != payload_digest({"a": 2})


def test_trigger_fills_digest_and_rejects_mismatch() -> None:
    trigger = _trigger()
    assert trigger.digest == payload_digest({"number": 1, "title": "add rate limiter"})
    with pytest.raises(ValidationError):
        _trigger(digest="0" * 64)


def test_trigger_is_frozen_and_forbids_extra_fields() -> None:
    trigger = _trigger()
    with pytest.raises(ValidationError):
        trigger.actor = "mallory"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _trigger(unexpected=True)


def test_enum_values_are_pinned() -> None:
    assert {k.value for k in TriggerKind} == {
        "issue",
        "pull_request",
        "issue_comment",
        "review_comment",
        "review",
        "ci_status",
        "message",
    }
    assert {k.value for k in DecisionOutcome} == {
        "accept",
        "observe",
        "ignore",
        "attach",
        "stop",
        "unavailable",
    }
    assert {k.value for k in DecisionStrength} == {"explicit", "inferred", "not_admitted"}
    assert {k.value for k in SubscriptionMode} == {"own", "context"}
    assert {k.value for k in SourceHealth} == {"ok", "degraded", "failed"}


def test_records_have_safe_defaults() -> None:
    thread = Thread(
        key="issue:acme/widgets#1",
        repository="acme/widgets",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=1,
    )
    assert thread.status is ThreadStatus.OPEN
    channel = Channel(
        id="ch-1", thread_key=thread.key, kind=ChannelKind.ISSUE_COMMENT, external_ref="#1"
    )
    assert channel.kind is ChannelKind.ISSUE_COMMENT
    subscription = Subscription(
        id="sb-1",
        ticket_id="tk-1",
        thread_key=thread.key,
        mode=SubscriptionMode.OWN,
        created_at=1,
    )
    assert subscription.ended_at is None
    decision = Decision(
        id="dc-1",
        trigger_ids=["tg-0001"],
        outcome=DecisionOutcome.OBSERVE,
        strength=DecisionStrength.INFERRED,
        policy_version="p6-v1",
        request_digest="d",
        created_at=1,
    )
    assert decision.reason_codes == [] and decision.mandate_id is None and decision.attempt == 1
    cursor = SourceCursor(source="github")
    assert cursor.health is SourceHealth.OK and cursor.consecutive_failures == 0


def test_thread_kinds_are_pinned() -> None:
    assert {k.value for k in ThreadKind} == {
        "issue",
        "pull_request",
        "review",
        "repository",
        "channel",
    }
