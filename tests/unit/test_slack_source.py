"""P7 M3：SlackFakeSource 去重、bot 过滤、重放与租户隔离。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.supervisor.model import TriggerKind
from stableagent.supervisor.sources.slack import SlackFakeSource, SlackReadItem
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock) -> SupervisorStore:
    return SupervisorStore(tmp_path / "s.db", clock=clock)


def _item(**kwargs: object) -> SlackReadItem:
    base = {
        "ts": "1.0",
        "channel_id": "C1",
        "user": "U_alice",
        "text": "hello @bot",
        "occurred_at": 10,
        "mentioned": True,
    }
    base.update(kwargs)
    return SlackReadItem.model_validate(base)


@pytest.mark.asyncio
async def test_poll_filters_bot_and_dedupes(
    store: SupervisorStore, clock: FixedClock
) -> None:
    source = SlackFakeSource(store, bot_user_id="B_bot", clock=clock, tenant_id="t1")
    source.enqueue(
        _item(ts="1.0", user="B_bot", text="i am bot"),
        _item(ts="2.0", user="U_alice", text="hi"),
        _item(ts="2.0", user="U_alice", text="hi again"),  # same ts+channel → same external_id
        _item(ts="3.0", user="U_bob", text="yo", bot=True),
    )
    batch = await source.poll()
    assert len(batch.events) == 1
    assert batch.events[0].trigger.kind is TriggerKind.MESSAGE
    assert batch.events[0].trigger.source == "slack"
    assert batch.events[0].trigger.actor == "U_alice"
    assert batch.duplicates >= 1
    # 重启重放：同一队列空了；cursor 前进
    again = await source.poll()
    assert again.events == []
    cursor = store.get_source_cursor("slack:t1")
    assert cursor is not None and cursor.cursor == "offset:1"


@pytest.mark.asyncio
async def test_cross_tenant_cursor_isolation(
    store: SupervisorStore, clock: FixedClock
) -> None:
    a = SlackFakeSource(store, bot_user_id="B", clock=clock, tenant_id="A")
    b = SlackFakeSource(store, bot_user_id="B", clock=clock, tenant_id="B")
    a.enqueue(_item(ts="1.0", user="U1", text="a"))
    b.enqueue(_item(ts="1.0", user="U1", text="b", channel_id="C2"))
    await a.poll()
    await b.poll()
    assert store.get_source_cursor("slack:A") is not None
    assert store.get_source_cursor("slack:B") is not None
    assert store.get_source_cursor("slack:A") != store.get_source_cursor("slack:B")


@pytest.mark.asyncio
async def test_transient_failure_marks_degraded(
    store: SupervisorStore, clock: FixedClock
) -> None:
    from stableagent.supervisor.source import SourceTransientError

    source = SlackFakeSource(store, bot_user_id="B", clock=clock, tenant_id="t1")

    def _boom(_items: object) -> tuple[list[object], int]:
        raise SourceTransientError("rate limited")

    source._dedupe = _boom  # type: ignore[method-assign]
    source.enqueue(_item())
    with pytest.raises(SourceTransientError):
        await source.poll()
    cursor = store.get_source_cursor("slack:t1")
    assert cursor is not None
    assert cursor.health.value == "degraded"
    assert cursor.consecutive_failures == 1
