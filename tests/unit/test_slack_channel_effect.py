"""P7 M3：Slack Channel Outbox effect 幂等与跨租户拒绝。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.orchestration.errors import PermanentEffectError
from stableagent.orchestration.model import Effect, EffectKind
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.orchestration.store import Store
from stableagent.supervisor.channels.slack import (
    SlackChannelWriter,
    slack_effect_key,
    slack_message_effect,
)
from stableagent.supervisor.model import Channel, ChannelKind
from tests.support import FixedClock


@pytest.mark.asyncio
async def test_slack_effect_writes_and_blocks_cross_tenant(tmp_path: Path) -> None:
    clock = FixedClock()
    store = Store(tmp_path / "e.db", clock=clock)
    writer = SlackChannelWriter(tenant_id="tenant-a")
    dispatcher = EffectDispatcher(
        store,
        {EffectKind.SLACK_MESSAGE: slack_message_effect(writer)},
        clock=clock,
    )
    key = slack_effect_key("ch-1", "deadbeef")
    with store.transaction():
        store.enqueue_effect(
            Effect(
                id="ef-1",
                kind=EffectKind.SLACK_MESSAGE,
                idempotency_key=key,
                payload={
                    "channel_id": "ch-1",
                    "thread_key": "slack:W1:C1",
                    "external_ref": "C1",
                    "body": "hello",
                    "tenant_id": "tenant-a",
                },
                due_at=clock.now_ms(),
            )
        )
        store.enqueue_effect(
            Effect(
                id="ef-2",
                kind=EffectKind.SLACK_MESSAGE,
                idempotency_key=key,  # 幂等：同键不新增
                payload={
                    "channel_id": "ch-1",
                    "thread_key": "slack:W1:C1",
                    "external_ref": "C1",
                    "body": "hello",
                    "tenant_id": "tenant-a",
                },
                due_at=clock.now_ms(),
            )
        )
    assert await dispatcher.dispatch_due() == 1
    assert len(writer.calls) == 1

    with pytest.raises(PermanentEffectError):
        await writer.write(
            channel=Channel(
                id="ch-x",
                thread_key="slack:W1:C1",
                kind=ChannelKind.SLACK_MESSAGE,
                external_ref="C1",
            ),
            body="nope",
            tenant_id="tenant-b",
        )
