"""A1/A2/A4：字段语义、序列化 round-trip、模型级校验与确定性 ID。"""

from __future__ import annotations

import pytest

from stableagent.orchestration.model import (
    Assignment,
    Dependency,
    Mandate,
    Ticket,
)
from tests.support import FixedClock, SequenceIds


def _ticket(ids: SequenceIds, clock: FixedClock) -> Ticket:
    return Ticket(
        id=ids.new_id("tk"),
        title="写一份恢复报告",
        task="统计 checkpoint 事件数量",
        created_at=clock.now_ms(),
        mandate_id=ids.new_id("md"),
    )


def test_ticket_round_trips_and_is_deterministic() -> None:
    ids, clock = SequenceIds(), FixedClock()
    ticket = _ticket(ids, clock)
    assert ticket.id == "tk-0001" and ticket.status.value == "open"
    assert Ticket.model_validate_json(ticket.model_dump_json()) == ticket


def test_mandate_defaults_are_the_p4_local_shape() -> None:
    mandate = Mandate(id="md-1", granted_at=0)
    assert (mandate.origin_kind, mandate.requester, mandate.policy_version) == (
        "local",
        "local",
        "p4-local-v1",
    )


def test_assignment_attempt_starts_at_one_and_rejects_zero() -> None:
    with pytest.raises(ValueError):
        Assignment(id="as-1", ticket_id="tk-1", attempt=0)


def test_worker_run_id_is_nullable_until_launch() -> None:
    assignment = Assignment(id="as-1", ticket_id="tk-1", attempt=1)
    assert assignment.worker_run_id is None
    assert assignment.model_copy(update={"worker_run_id": "r1"}).worker_run_id == "r1"


def test_dependency_rejects_self_reference() -> None:
    with pytest.raises(ValueError):
        Dependency(ticket_id="tk-1", depends_on_ticket_id="tk-1")


def test_assignment_belongs_to_a_ticket_by_construction() -> None:
    # ticket_id 无默认值：缺了就构造不出来，A4 的"必属某 Ticket"是结构保证而非运行时检查。
    with pytest.raises(ValueError):
        Assignment(id="as-1", attempt=1)
