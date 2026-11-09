"""WP-6.4 第 4 条：五个崩溃窗口在重启后不丢语义、不重复外部写入。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
)
from stableagent.orchestration.model import (
    AssignmentStatus,
    Effect,
    EffectKind,
    IdFactory,
)
from stableagent.supervisor.router import RouterConfig
from stableagent.supervisor.runtime import RuntimeConfig, build_runtime
from stableagent.supervisor.source import GitHubSource
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _issue_item(number: int = 1) -> GitHubReadItem:
    return GitHubReadItem(
        kind=GitHubReadKind.ISSUE,
        occurred_at=1,
        resource={
            "number": number,
            "user": {"login": "alice"},
            "body": "@agent please add rate limiter",
        },
    )


class RecordingExecutor:
    def __init__(self) -> None:
        self.launched: list[str] = []
        self.recovered: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)

    async def deliver(self, assignment, ticket, inputs) -> None:
        self.delivered.extend((ticket.id, item.payload) for item in inputs)

    async def cancel(self, assignment) -> None:
        self.cancelled.append(assignment.id)

    async def recover(self, assignment, ticket) -> None:
        self.recovered.append(assignment.id)


def _build(
    *,
    store: SupervisorStore,
    client: FakeGitHubReadClient,
    executor: RecordingExecutor,
    handlers,
    clock: FixedClock,
    ids: IdFactory,
    owner: str,
):
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY}),
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    source = GitHubSource(store, client, repository="o/r", bot_login="agent", clock=clock)
    bundle = build_runtime(
        store=store,
        policy=policy,
        source=source,
        executor=executor,
        effect_handlers=handlers,
        config=RuntimeConfig(
            router=RouterConfig(
                repository="o/r",
                operators=frozenset({"alice"}),
                actions=("read_repository",),
            ),
            owner=owner,
            lock_ttl_ms=1_000,
            stop_grace_ms=10,
            step_interval_ms=0,
        ),
        clock=clock,
        ids=ids,
    )
    return bundle, source


@pytest.fixture
def env(tmp_path: Path):
    clock, ids = FixedClock(), SequenceIds()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    client = FakeGitHubReadClient([_issue_item()])
    try:
        yield store, client, clock, ids
    finally:
        store.close()


async def test_trigger_persisted_before_routing_is_routed_on_restart(env) -> None:
    store, client, clock, ids = env
    first, source = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    first.runtime.start()
    await source.poll(limit=10)  # Trigger/Thread/cursor 已落库，路由尚未发生
    assert store.list_decisions() == []

    clock.advance(2_000)  # 进程死亡：锁过期
    restarted, _ = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-b",
    )

    started = restarted.runtime.start()

    assert started.routed == 1
    assert len(store.list_decisions()) == 1
    assert store.list_unrouted_triggers() == []


async def test_ticket_committed_before_dispatch_is_dispatched_after_restart(env) -> None:
    store, client, clock, ids = env
    first, source = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    first.runtime.start()
    batch = await source.poll(limit=10)
    first.router.receive(batch.events[0].trigger)  # 五件原子写已提交，launch 尚未派发
    assert len(store.list_tickets()) == 1

    clock.advance(2_000)
    executor = RecordingExecutor()
    restarted, _ = _build(
        store=store,
        client=client,
        executor=executor,
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-b",
    )
    restarted.runtime.start()

    step = await restarted.runtime.step()
    await asyncio.sleep(0)

    assert step.dispatched == 1
    assert len(store.list_tickets()) == 1
    assert len(executor.launched) == 1


async def test_github_comment_effect_is_neither_lost_nor_replayed(env) -> None:
    store, client, clock, ids = env
    calls: list[str] = []

    async def comment_handler(effect) -> None:
        calls.append(effect.id)

    handlers = {EffectKind.GITHUB_COMMENT: comment_handler}
    first, _ = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers=handlers,
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    first.runtime.start()
    store.enqueue_effect(
        Effect(
            id="ef-comment",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="comment:1",
            payload={"repository": "o/r", "url": "https://example/1", "body": "done"},
            due_at=clock.now_ms(),
        )
    )

    clock.advance(2_000)
    restarted, _ = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers=handlers,
        clock=clock,
        ids=ids,
        owner="runtime-b",
    )
    restarted.runtime.start()
    await restarted.runtime.step()
    assert calls == ["ef-comment"]

    clock.advance(2_000)
    third, _ = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers=handlers,
        clock=clock,
        ids=ids,
        owner="runtime-c",
    )
    third.runtime.start()
    await third.runtime.step()
    assert calls == ["ef-comment"]  # 已成功 effect 不重放


async def test_worker_run_crash_becomes_interrupted_and_relaunched(env) -> None:
    store, client, clock, ids = env
    first_executor = RecordingExecutor()
    first, _ = _build(
        store=store,
        client=client,
        executor=first_executor,
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    first.runtime.start()
    await first.runtime.step()
    await asyncio.sleep(0)
    ticket = store.list_tickets()[0]
    assignment = store.list_assignments(ticket.id)[0]
    assert assignment.status is AssignmentStatus.RUNNING

    clock.advance(2_000)
    second_executor = RecordingExecutor()
    restarted, _ = _build(
        store=store,
        client=client,
        executor=second_executor,
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-b",
    )

    started = restarted.runtime.start()
    assert started.recovery.interrupted_assignments == 1
    assert started.recovery.relaunched == 1

    await restarted.runtime.step()
    await asyncio.sleep(0)
    assert second_executor.recovered == [assignment.id]
    assert second_executor.launched == []  # 走 recover，不冷启动


async def test_cursor_update_failure_rolls_back_the_whole_poll(env, monkeypatch) -> None:
    store, client, clock, ids = env
    bundle, source = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    bundle.runtime.start()

    def boom(cursor) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(store, "put_source_cursor", boom)
        with pytest.raises(sqlite3.OperationalError):
            await bundle.runtime.step()

    assert store.get_source_cursor("github") is None
    assert store.list_triggers() == []  # Trigger/Thread 与 cursor 同批回滚

    step = await bundle.runtime.step()
    assert step.received == 1
    assert len(store.list_triggers()) == 1
    assert len(store.list_decisions()) == 1  # 游标未推进，下一次 poll 不丢事件
