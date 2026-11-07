"""A4 + L5（前三个时机）：执行面前复检 Mandate，拒绝时 park 而不启动 Worker。

复检的 when 由调用面决定：launch（首次）/ retry（attempt>1）/ recover / deliver（续跑）；
cancel 不启动任何东西，因此不复检。拒绝只写结构化 BLOCKED 报告，不靠异常文案判型（D4）。
"""

from __future__ import annotations

import pathlib

import pytest

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action, ExplicitRequest
from stableagent.control.policy import P5_POLICY_VERSION, StaticPolicy, StaticPolicyConfig
from stableagent.integrations.repository import RepositoryWorkerExecutor
from stableagent.orchestration.errors import PermanentEffectError
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Mandate,
    ReportKind,
    Ticket,
    TicketStatus,
)
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds


class _Inner:
    """WorkerExecutor 的计数替身：断言"拒绝 = 没有启动"。"""

    def __init__(self) -> None:
        self.launched: list[str] = []
        self.recovered: list[str] = []
        self.delivered: list[str] = []
        self.cancelled: list[str] = []

    async def launch(self, assignment: Assignment, ticket: Ticket, inputs) -> None:
        self.launched.append(assignment.id)

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None:
        self.recovered.append(assignment.id)

    async def deliver(self, assignment: Assignment, ticket: Ticket, inputs) -> None:
        self.delivered.append(assignment.id)

    async def cancel(self, assignment: Assignment) -> None:
        self.cancelled.append(assignment.id)


def _rig(tmp_path: pathlib.Path, *, expires_at: int | None = None, attempt: int = 1):
    clock = FixedClock()
    ids = SequenceIds()
    store = Store(tmp_path / "p5.db", clock=clock)
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="owner/repo",
            actors=frozenset({"octocat"}),
            grantable_actions=frozenset(Action) - {Action.ENABLE_AUTO_MERGE},
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    audit = AuditWriter(policy, store, clock=clock, ids=ids)
    admission = policy.admit_explicit(
        ExplicitRequest(
            repository="owner/repo",
            requester="octocat",
            mentioned=True,
            stable_id="github:owner/repo:issue_comment:1",
            text="@agent 改代码",
        )
    )
    mandate = policy.create_mandate(admission, actions=[Action.READ_REPOSITORY])
    if expires_at is not None:
        mandate = mandate.model_copy(update={"expires_at": expires_at})
    store.insert_mandate(mandate)
    ticket = Ticket(
        id="tk-1",
        title="改代码",
        task="改代码",
        created_at=clock.now_ms(),
        status=TicketStatus.RUNNING,
        mandate_id=mandate.id,
    )
    store.insert_ticket(ticket)
    assignment = Assignment(
        id="as-1",
        ticket_id=ticket.id,
        attempt=attempt,
        status=AssignmentStatus.RUNNING,
    )
    store.insert_assignment(assignment)
    return store, policy, audit, ticket, assignment


async def _call(executor: RepositoryWorkerExecutor, method: str, assignment, ticket) -> None:
    if method == "launch":
        await executor.launch(assignment, ticket, [])
    elif method == "recover":
        await executor.recover(assignment, ticket)
    else:
        await executor.deliver(assignment, ticket, [])


@pytest.mark.parametrize(
    ("method", "when"),
    [("launch", "launch"), ("recover", "recover"), ("deliver", "launch")],
)
async def test_revalidate_precedes_the_action(
    tmp_path: pathlib.Path, method: str, when: str
) -> None:
    store, policy, audit, ticket, assignment = _rig(tmp_path)
    inner = _Inner()
    executor = RepositoryWorkerExecutor(inner, store=store, policy=policy, audit=audit)
    await _call(executor, method, assignment, ticket)
    rows = store.list_audit(ticket_id=ticket.id, kind="revalidate")
    assert rows[-1]["action"] == when
    assert getattr(
        inner, {"launch": "launched", "recover": "recovered", "deliver": "delivered"}[method]
    ) == [assignment.id]


async def test_retry_attempt_is_audited_as_retry(tmp_path: pathlib.Path) -> None:
    store, policy, audit, ticket, assignment = _rig(tmp_path, attempt=2)
    inner = _Inner()
    executor = RepositoryWorkerExecutor(inner, store=store, policy=policy, audit=audit)
    await executor.launch(assignment, ticket, [])
    rows = store.list_audit(ticket_id=ticket.id, kind="revalidate")
    assert rows[-1]["action"] == "retry"
    assert inner.launched == [assignment.id]


async def test_expired_mandate_parks_without_starting_the_worker(tmp_path: pathlib.Path) -> None:
    store, policy, audit, ticket, assignment = _rig(tmp_path, expires_at=1)
    inner = _Inner()
    executor = RepositoryWorkerExecutor(inner, store=store, policy=policy, audit=audit)
    with pytest.raises(PermanentEffectError):
        await executor.launch(assignment, ticket, [])
    assert inner.launched == []
    reports = store.list_reports(ticket.id)
    assert reports[-1].kind is ReportKind.BLOCKED
    assert reports[-1].details == {"when": "launch", "policy_version": P5_POLICY_VERSION}


async def test_p4_local_mandate_is_refused_on_the_external_surface(
    tmp_path: pathlib.Path,
) -> None:
    store, policy, audit, ticket, assignment = _rig(tmp_path)
    local = Mandate(id="md-local", granted_at=1)
    store.insert_mandate(local)
    store.update_ticket(ticket.model_copy(update={"mandate_id": local.id}))
    ticket = store.get_ticket(ticket.id)
    inner = _Inner()
    executor = RepositoryWorkerExecutor(inner, store=store, policy=policy, audit=audit)
    with pytest.raises(PermanentEffectError) as excinfo:
        await executor.launch(assignment, ticket, [])
    assert "policy_version_drift" in str(excinfo.value)
    assert inner.launched == []
    assert store.list_reports(ticket.id)[-1].kind is ReportKind.BLOCKED


async def test_missing_mandate_is_a_permanent_failure(tmp_path: pathlib.Path) -> None:
    store, policy, audit, ticket, assignment = _rig(tmp_path)
    store.update_ticket(ticket.model_copy(update={"mandate_id": "md-ghost"}))
    inner = _Inner()
    executor = RepositoryWorkerExecutor(inner, store=store, policy=policy, audit=audit)
    with pytest.raises(PermanentEffectError) as excinfo:
        await executor.launch(assignment, store.get_ticket(ticket.id), [])
    assert "mandate_missing" in str(excinfo.value)
    assert inner.launched == []


async def test_cancel_delegates_without_revalidation(tmp_path: pathlib.Path) -> None:
    store, policy, audit, ticket, assignment = _rig(tmp_path)
    inner = _Inner()
    executor = RepositoryWorkerExecutor(inner, store=store, policy=policy, audit=audit)
    await executor.cancel(assignment)
    assert inner.cancelled == [assignment.id]
    assert store.list_audit(ticket_id=ticket.id, kind="revalidate") == []
