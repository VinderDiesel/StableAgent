"""Slack Fake Source：确定性 fixture，无真实 HTTP（ADR-07 决策 11）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stableagent.orchestration.model import Clock
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    SourceCursor,
    SourceHealth,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.source import (
    PollBatch,
    SourceError,
    SourceEvent,
    SourceTransientError,
    stable_channel_id,
    stable_trigger_id,
)
from stableagent.supervisor.store import SupervisorStore


class SlackReadItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: str
    channel_id: str
    user: str
    text: str
    occurred_at: int
    workspace: str = "W1"
    mentioned: bool = False
    bot: bool = False


class SlackFakeSource:
    """内存队列驱动的 Slack Source：独立 cursor、bot 过滤、去重、租户隔离。"""

    def __init__(
        self,
        store: SupervisorStore,
        *,
        bot_user_id: str,
        clock: Clock,
        tenant_id: str = "default",
        workspace: str = "W1",
    ) -> None:
        self._store = store
        self._bot_user_id = bot_user_id
        self._clock = clock
        self._tenant_id = tenant_id
        self._workspace = workspace
        self._queue: list[SlackReadItem] = []
        self._cursor_key = f"slack:{tenant_id}"

    def enqueue(self, *items: SlackReadItem) -> None:
        self._queue.extend(items)

    def normalize(self, item: SlackReadItem) -> SourceEvent | None:
        if item.bot or item.user.casefold() == self._bot_user_id.casefold():
            return None
        thread_key = f"slack:{item.workspace}:{item.channel_id}"
        external_id = f"slack:{item.workspace}:{item.channel_id}:{item.ts}"
        payload: dict[str, Any] = {
            "text": item.text,
            "channel_id": item.channel_id,
            "ts": item.ts,
            "mentioned": item.mentioned,
            "workspace": item.workspace,
        }
        channel_ref = item.channel_id
        return SourceEvent(
            trigger=Trigger(
                id=stable_trigger_id(external_id),
                source="slack",
                external_id=external_id,
                kind=TriggerKind.MESSAGE,
                actor=item.user,
                thread_keys=[thread_key],
                occurred_at=item.occurred_at,
                received_at=self._clock.now_ms(),
                payload=payload,
                tenant_id=self._tenant_id,
            ),
            thread=Thread(
                key=thread_key,
                source="slack",
                repository=f"slack/{item.workspace}",
                kind=ThreadKind.CHANNEL,
                external_ref=channel_ref,
                last_trigger_at=item.occurred_at,
                status=ThreadStatus.OPEN,
                tenant_id=self._tenant_id,
            ),
            channel=Channel(
                id=stable_channel_id(thread_key, ChannelKind.SLACK_MESSAGE, channel_ref),
                thread_key=thread_key,
                kind=ChannelKind.SLACK_MESSAGE,
                external_ref=channel_ref,
            ),
        )

    async def poll(self, *, limit: int = 100) -> PollBatch:
        stored = self._store.get_source_cursor(self._cursor_key)
        try:
            batch = list(self._queue[:limit])
            self._queue = self._queue[limit:]
            events, in_batch_dup = self._dedupe(batch)
        except SourceError as error:
            self._record_failure(stored, error)
            raise
        return self._persist(events, stored, in_batch_dup)

    def _dedupe(self, items: list[SlackReadItem]) -> tuple[list[SourceEvent], int]:
        by_external: dict[str, SourceEvent] = {}
        duplicates = 0
        for item in items:
            event = self.normalize(item)
            if event is None:
                continue
            if event.trigger.external_id in by_external:
                duplicates += 1
                continue
            by_external[event.trigger.external_id] = event
        ordered = sorted(
            by_external.values(),
            key=lambda e: (e.trigger.occurred_at, e.trigger.external_id),
        )
        return ordered, duplicates

    def _persist(
        self,
        events: list[SourceEvent],
        stored: SourceCursor | None,
        in_batch_duplicates: int,
    ) -> PollBatch:
        offset = 0
        if stored and stored.cursor and stored.cursor.startswith("offset:"):
            offset = int(stored.cursor.split(":", 1)[1])
        next_cursor = f"offset:{offset + len(events)}"
        with self._store.transaction():
            fresh: list[SourceEvent] = []
            duplicates = 0
            for event in events:
                if event.trigger.tenant_id != self._tenant_id:
                    raise SourceError("source.tenant", "cross-tenant event rejected")
                if not self._store.insert_trigger(event.trigger):
                    duplicates += 1
                    continue
                self._store.upsert_thread(event.thread)
                if event.channel is not None:
                    self._store.insert_channel(event.channel)
                fresh.append(event)
            self._store.put_source_cursor(
                SourceCursor(
                    source=self._cursor_key,
                    cursor=next_cursor,
                    last_success_at=self._clock.now_ms(),
                    consecutive_failures=0,
                    health=SourceHealth.OK,
                    tenant_id=self._tenant_id,
                )
            )
        return PollBatch(
            events=fresh,
            next_cursor=next_cursor,
            duplicates=duplicates + in_batch_duplicates,
        )

    def _record_failure(self, stored: SourceCursor | None, error: SourceError) -> None:
        health = (
            SourceHealth.DEGRADED
            if isinstance(error, SourceTransientError)
            else SourceHealth.FAILED
        )
        previous = stored.consecutive_failures if stored else 0
        with self._store.transaction():
            self._store.put_source_cursor(
                SourceCursor(
                    source=self._cursor_key,
                    cursor=stored.cursor if stored else None,
                    last_success_at=stored.last_success_at if stored else None,
                    consecutive_failures=previous + 1,
                    health=health,
                    tenant_id=self._tenant_id,
                )
            )


class SlackFixture(BaseModel):
    """测试装置：预置消息列表。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[SlackReadItem] = Field(default_factory=list)
