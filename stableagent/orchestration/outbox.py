"""Durable outbox：领域状态与待执行副作用同事务落库，提交之后才做 I/O。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from stableagent.orchestration.errors import DeferrableEffectError, PermanentEffectError
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
        eligible: Mapping[EffectKind, Callable[[Effect], bool]] | None = None,
        backoff: Callable[[int], int] | None = None,
        on_exhausted: Callable[[Effect, str], None] | None = None,
    ) -> None:
        self._store = store
        self._handlers = dict(handlers)
        self._clock = clock
        self._max_attempts = max_attempts
        self._backoff_ms = backoff_ms
        self._eligible = dict(eligible or {})
        self._backoff = backoff
        self._on_exhausted = on_exhausted
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
            if not self._eligible.get(effect.kind, lambda e: True)(effect):
                continue  # 门控关闭：不领取、不记 attempts（设计裁定 2：事实源在 Store）
            claimed = self._claim(effect)
            try:
                await handler(claimed)
            except DeferrableEffectError as exc:
                self._defer(claimed, repr(exc))  # 回退与"真实失败"两条账
                continue
            except PermanentEffectError as exc:
                self._fail_permanently(claimed, repr(exc))  # 重放只会被同样拒绝：不进退避
                continue
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
        delay = (
            self._backoff(effect.attempts) if self._backoff else self._backoff_ms * effect.attempts
        )
        due_at = self._clock.now_ms() + delay if state is EffectState.RETRY_WAIT else effect.due_at
        with self._store.transaction():
            updated = self._store.update_effect(
                effect.model_copy(update={"state": state, "last_error": detail, "due_at": due_at})
            )
        self._outcomes.append((updated.id, updated.state))
        if state is EffectState.FAILED and self._on_exhausted is not None:
            self._on_exhausted(updated, detail)  # 钩子在事务外：service 会开自己的短事务（L6）
        return updated

    def _fail_permanently(self, effect: Effect, detail: str) -> Effect:
        """重放只会得到同样的拒绝（如 404）：直接 FAILED、保持 due_at、不占退避预算。"""
        state = next_effect_status(effect.state, EffectAction.FAIL)
        with self._store.transaction():
            updated = self._store.update_effect(
                effect.model_copy(update={"state": state, "last_error": detail})
            )
        self._outcomes.append((updated.id, updated.state))
        if self._on_exhausted is not None:
            self._on_exhausted(updated, detail)  # 钩子在事务外（L6）
        return updated

    def _defer(self, effect: Effect, detail: str) -> Effect:
        """可延期的"没到时候"：RETRY 转态、还掉 claim 的那一次 attempts、固定退避。

        与 `_fail_or_retry` 的差别是账本：顺延不进 max_attempts 判定——合法驻留等待
        多久都不该把 effect 判死（设计裁定 2 的崩溃丢驻留路径）。
        """
        with self._store.transaction():
            updated = self._store.update_effect(
                effect.model_copy(
                    update={
                        "state": next_effect_status(effect.state, EffectAction.RETRY),
                        "attempts": max(effect.attempts - 1, 0),
                        "due_at": self._clock.now_ms() + self._backoff_ms,
                        "last_error": detail,
                    }
                )
            )
        self._outcomes.append((updated.id, updated.state))
        return updated
