"""Durable outbox：领域状态与待执行副作用同事务落库，提交之后才做 I/O。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from stableagent.orchestration.model import (
    Clock,
    Effect,
    EffectAction,
    EffectKind,
    EffectState,
    next_effect_status,
)
from stableagent.orchestration.store import Store

EffectHandler = Callable[[Effect], Awaitable[None]]


class EffectDispatcher:
    def __init__(
        self,
        store: Store,
        handlers: Mapping[EffectKind, EffectHandler],
        *,
        clock: Clock,
        max_attempts: int = 3,
        backoff_ms: int = 1000,
    ) -> None:
        self._store = store
        self._handlers = dict(handlers)
        self._clock = clock
        self._max_attempts = max_attempts
        self._backoff_ms = backoff_ms
        self._outcomes: list[tuple[str, EffectState]] = []

    def outcomes(self) -> list[tuple[str, EffectState]]:
        return list(self._outcomes)

    async def dispatch_due(self, *, limit: int = 10) -> int:
        """返回成功执行的 effect 数。I/O 必须在事务之外（L6）。"""
        assert not self._store.in_transaction, "handler 的 I/O 不得持有事务"
        handled = 0
        for effect in self._store.due_effects(self._clock.now_ms(), limit=limit):
            handler = self._handlers.get(effect.kind)
            if handler is None:
                continue
            claimed = self._claim(effect)
            try:
                await handler(claimed)
            except Exception as exc:  # 副作用失败：退避重试，达上限才失败
                self._fail_or_retry(claimed, repr(exc))
                continue
            self._settle(claimed, EffectAction.SUCCEED)
            handled += 1
        return handled

    def _claim(self, effect: Effect) -> Effect:
        with self._store.transaction():
            claimed = self._store.update_effect(
                effect.model_copy(
                    update={
                        "state": next_effect_status(effect.state, EffectAction.CLAIM),
                        "attempts": effect.attempts + 1,
                    }
                )
            )
        return claimed

    def _settle(self, effect: Effect, action: EffectAction) -> Effect:
        with self._store.transaction():
            updated = self._store.update_effect(
                effect.model_copy(update={"state": next_effect_status(effect.state, action)})
            )
        self._outcomes.append((updated.id, updated.state))
        return updated

    def _fail_or_retry(self, effect: Effect, detail: str) -> Effect:
        action = EffectAction.RETRY if effect.attempts < self._max_attempts else EffectAction.FAIL
        state = next_effect_status(effect.state, action)
        due_at = (
            self._clock.now_ms() + self._backoff_ms * effect.attempts
            if state is EffectState.RETRY_WAIT
            else effect.due_at
        )
        with self._store.transaction():
            updated = self._store.update_effect(
                effect.model_copy(update={"state": state, "last_error": detail, "due_at": due_at})
            )
        self._outcomes.append((updated.id, updated.state))
        return updated
