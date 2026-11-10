"""Slack Channel writer + Outbox handler（fake；ADR-07 决策 13）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stableagent.orchestration.errors import PermanentEffectError
from stableagent.orchestration.model import Effect, EffectKind
from stableagent.orchestration.outbox import EffectHandler
from stableagent.supervisor.model import Channel, ChannelKind
from stableagent.supervisor.source import ChannelWriter, FakeChannelWriter


class SlackChannelWriter:
    """租户隔离的 Slack 回写；底层可注入 FakeChannelWriter。"""

    def __init__(
        self,
        inner: ChannelWriter | None = None,
        *,
        tenant_id: str = "default",
    ) -> None:
        self._inner = inner if inner is not None else FakeChannelWriter()
        self._tenant_id = tenant_id
        self.calls: list[tuple[Channel, str]] = []

    async def write(
        self, *, channel: Channel, body: str, tenant_id: str = "default"
    ) -> dict[str, Any]:
        if tenant_id != self._tenant_id:
            raise PermanentEffectError("cross-tenant slack write forbidden")
        if channel.kind is not ChannelKind.SLACK_MESSAGE:
            raise PermanentEffectError(f"unsupported channel kind: {channel.kind}")
        self.calls.append((channel, body))
        return await self._inner.write(channel=channel, body=body)


def slack_message_effect(writer: SlackChannelWriter) -> EffectHandler:
    async def handle(effect: Effect) -> None:
        if effect.kind is not EffectKind.SLACK_MESSAGE:
            raise PermanentEffectError(f"unexpected kind {effect.kind}")
        payload = effect.payload
        channel = Channel(
            id=_text(payload, "channel_id"),
            thread_key=_text(payload, "thread_key"),
            kind=ChannelKind.SLACK_MESSAGE,
            external_ref=_text(payload, "external_ref"),
        )
        await writer.write(
            channel=channel,
            body=_text(payload, "body"),
            tenant_id=str(payload.get("tenant_id") or "default"),
        )

    return handle


def slack_effect_key(stable_id: str, body_digest: str) -> str:
    return f"slack_message:{stable_id}:{body_digest}"


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PermanentEffectError(f"payload.{key} 必须是非空字符串")
    return value
