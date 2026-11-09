"""SupervisorStore：P5 库无损扩展、Trigger 去重、Thread 单调状态。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stableagent.orchestration.model import Mandate, Ticket
from stableagent.orchestration.store import Store
from stableagent.supervisor import SUPERVISOR_SCHEMA_VERSION
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
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock


def _trigger(external_id: str = "issue:1", *, id: str = "tg-0001") -> Trigger:
    return Trigger(
        id=id,
        external_id=external_id,
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"number": 1, "title": "add rate limiter"},
    )


def _thread(*, at: int = 10, status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    return Thread(
        key="issue:acme/widgets#1",
        repository="acme/widgets",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=at,
        status=status,
    )


@pytest.fixture
def store(tmp_path: Path) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=FixedClock())


def test_supervisor_schema_extends_a_p5_store_without_touching_it(tmp_path: Path) -> None:
    path = tmp_path / "p6.db"
    clock = FixedClock()
    p5 = Store(path, clock=clock)
    p5.insert_mandate(Mandate(id="md-1", granted_at=clock.now_ms()))
    p5.insert_ticket(
        Ticket(id="tk-1", title="t", task="t", created_at=clock.now_ms(), mandate_id="md-1")
    )
    p5.close()

    store = SupervisorStore(path, clock=clock)
    try:
        assert store.schema_version() == SUPERVISOR_SCHEMA_VERSION
        assert store.get_ticket("tk-1") is not None
        versions = {
            row[0]
            for row in store._conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert {"p5-control-v1", SUPERVISOR_SCHEMA_VERSION} <= versions
    finally:
        store.close()


def test_supervisor_schema_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "p6.db"
    first = SupervisorStore(path, clock=FixedClock())
    first.close()
    second = SupervisorStore(path, clock=FixedClock())
    try:
        assert second.schema_version() == SUPERVISOR_SCHEMA_VERSION
        assert second.list_triggers() == []
    finally:
        second.close()


def test_trigger_insert_is_idempotent_by_external_id(store: SupervisorStore) -> None:
    assert store.insert_trigger(_trigger()) is True
    assert store.insert_trigger(_trigger(id="tg-9999")) is False
    assert [t.id for t in store.list_triggers()] == ["tg-0001"]


def test_trigger_round_trip_preserves_payload_and_digest(store: SupervisorStore) -> None:
    trigger = _trigger()
    store.insert_trigger(trigger)
    loaded = store.get_trigger("tg-0001")
    assert loaded == trigger
    assert store.get_trigger_by_external("github", "issue:1") == trigger
    assert store.get_trigger_by_external("github", "issue:404") is None


def test_list_triggers_filters_by_thread_key(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    other = _trigger(external_id="issue:2", id="tg-0002").model_copy(
        update={"thread_keys": ["issue:acme/widgets#2"]}
    )
    store.insert_trigger(other)
    assert [t.id for t in store.list_triggers(thread_key="issue:acme/widgets#1")] == ["tg-0001"]


def test_thread_upsert_is_monotonic_for_time_and_status(store: SupervisorStore) -> None:
    store.upsert_thread(_thread(at=10))
    store.upsert_thread(_thread(at=20, status=ThreadStatus.CLOSED))
    store.upsert_thread(_thread(at=15, status=ThreadStatus.OPEN))
    loaded = store.get_thread("issue:acme/widgets#1")
    assert loaded is not None
    assert loaded.last_trigger_at == 20
    assert loaded.status is ThreadStatus.CLOSED


def test_thread_external_ref_is_unique_per_source_and_repository(store: SupervisorStore) -> None:
    store.upsert_thread(_thread())
    duplicate = _thread().model_copy(update={"key": "issue:acme/widgets#1-dup"})
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_thread(duplicate)


def test_supervisor_and_ticket_writes_share_one_transaction(store: SupervisorStore) -> None:
    """ADR-06 决策 7：Trigger 去重与 Ticket 创建必须同生共死。"""
    with pytest.raises(RuntimeError), store.transaction():
        store.insert_trigger(_trigger())
        store.insert_ticket(
            Ticket(id="tk-tx", title="t", task="t", created_at=1, mandate_id="md-none")
        )
        raise RuntimeError("rollback")
    assert store.get_trigger("tg-0001") is None
    assert store.get_ticket("tk-tx") is None


def test_channel_is_unique_per_destination(store: SupervisorStore) -> None:
    channel = Channel(
        id="ch-1",
        thread_key="issue:acme/widgets#1",
        kind=ChannelKind.ISSUE_COMMENT,
        external_ref="#1",
    )
    assert store.insert_channel(channel) is True
    duplicate = channel.model_copy(update={"id": "ch-2"})
    assert store.insert_channel(duplicate) is False
    assert [c.id for c in store.list_channels(channel.thread_key)] == ["ch-1"]


def test_live_subscription_is_idempotent_and_can_be_resubscribed(
    store: SupervisorStore,
) -> None:
    live = Subscription(
        id="sb-1",
        ticket_id="tk-1",
        thread_key="issue:acme/widgets#1",
        mode=SubscriptionMode.OWN,
        created_at=10,
    )
    assert store.insert_subscription(live) is True
    assert store.insert_subscription(live.model_copy(update={"id": "sb-2"})) is False
    assert store.end_subscription("sb-1", ended_at=20) == 1
    assert store.end_subscription("sb-1", ended_at=21) == 0
    assert store.insert_subscription(live.model_copy(update={"id": "sb-3"})) is True
    live_rows = store.list_subscriptions(ticket_id="tk-1", live_only=True)
    assert [s.id for s in live_rows] == ["sb-3"]


def test_subscription_list_filters_by_thread_and_mode(store: SupervisorStore) -> None:
    for index, mode in enumerate((SubscriptionMode.OWN, SubscriptionMode.CONTEXT), start=1):
        store.insert_subscription(
            Subscription(
                id=f"sb-{index}",
                ticket_id="tk-1",
                thread_key="issue:acme/widgets#1",
                mode=mode,
                created_at=index,
            )
        )
    assert len(store.list_subscriptions(thread_key="issue:acme/widgets#1")) == 2
    assert [s.mode for s in store.list_subscriptions(ticket_id="tk-1")] == [
        SubscriptionMode.OWN,
        SubscriptionMode.CONTEXT,
    ]


def _decision(*, attempt: int = 1, trigger_id: str = "tg-0001") -> Decision:
    return Decision(
        id=f"dc-{attempt}",
        trigger_ids=[trigger_id],
        outcome=DecisionOutcome.OBSERVE,
        strength=DecisionStrength.INFERRED,
        reason_codes=["would_accept"],
        policy_version="p6-v1",
        request_digest="digest-1",
        attempt=attempt,
        created_at=10 * attempt,
    )


def test_decision_round_trip_keeps_trigger_links(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    store.insert_decision(_decision())
    loaded = store.get_decision("dc-1")
    assert loaded is not None
    assert loaded.trigger_ids == ["tg-0001"]
    assert store.list_decisions(trigger_id="tg-0001")[0].id == "dc-1"
    assert store.list_decisions(trigger_id="tg-404") == []


def test_decision_request_digest_is_idempotent_per_attempt(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    store.insert_decision(_decision())
    duplicate = _decision().model_copy(update={"id": "dc-dup"})
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_decision(duplicate)
    store.insert_decision(_decision(attempt=2).model_copy(update={"id": "dc-2"}))
    assert [d.attempt for d in store.list_decisions()] == [1, 2]


def test_decision_filters_by_mandate(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    bound = _decision().model_copy(update={"id": "dc-bound", "mandate_id": "md-1"})
    store.insert_decision(bound)
    assert [d.id for d in store.list_decisions(mandate_id="md-1")] == ["dc-bound"]


def test_source_cursor_upsert_keeps_one_row(store: SupervisorStore) -> None:
    store.put_source_cursor(SourceCursor(source="github", cursor="1"))
    store.put_source_cursor(
        SourceCursor(
            source="github",
            cursor="2",
            last_success_at=20,
            consecutive_failures=3,
            health=SourceHealth.DEGRADED,
        )
    )
    loaded = store.get_source_cursor("github")
    assert loaded is not None
    assert (loaded.cursor, loaded.consecutive_failures, loaded.health) == (
        "2",
        3,
        SourceHealth.DEGRADED,
    )
    assert [c.source for c in store.list_source_cursors()] == ["github"]


def test_next_decision_attempt_counts_per_digest(store: SupervisorStore) -> None:
    assert store.next_decision_attempt("digest-a") == 1
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=[],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=["x"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=1,
            created_at=1,
        )
    )
    assert store.next_decision_attempt("digest-a") == 2
    assert store.next_decision_attempt("digest-b") == 1


def test_find_accepted_decision_skips_observe_rows(store: SupervisorStore) -> None:
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=[],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=["x"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=1,
            created_at=1,
        )
    )
    store.insert_decision(
        Decision(
            id="dc-2",
            trigger_ids=[],
            outcome=DecisionOutcome.ACCEPT,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=["admitted"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=2,
            created_at=2,
        )
    )
    found = store.find_accepted_decision("digest-a")
    assert found is not None
    assert found.id == "dc-2"
    assert store.find_accepted_decision("digest-missing") is None
