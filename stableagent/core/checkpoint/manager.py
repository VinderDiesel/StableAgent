"""快照触发与写入序列化（P2 交付：`manager.py`）。

职责边界：本模块决定**何时写**（`auto_save_every` + 暂停/终止点必存）与**写什么**
（`AgentState` 原文），不关心 loop 语义、不关心存储后端差异。
写入失败按 DoD B5 降级为计数与告警载荷，绝不打断运行。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from stableagent.core.checkpoint.storage import CheckpointRecord, CheckpointStore
from stableagent.core.state import AgentState, AgentStatus

__all__ = ["CheckpointManager"]

_PERSIST_STATUSES = frozenset({AgentStatus.PAUSED, AgentStatus.SUCCEEDED, AgentStatus.FAILED})


class CheckpointManager:
    def __init__(
        self,
        store: CheckpointStore,
        *,
        auto_save_every: int = 0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._auto_save_every = auto_save_every
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._saved: set[tuple[str, int]] = set()
        self.failures = 0
        self.last_record: CheckpointRecord | None = None

    @property
    def store(self) -> CheckpointStore:
        return self._store

    def should_save(self, state: AgentState) -> bool:
        """步边界触发判据：终止/暂停态必存，其余按 auto_save_every 取模。"""
        if state.status in _PERSIST_STATUSES:
            return True
        if self._auto_save_every <= 0:
            return False
        return state.step > 0 and state.step % self._auto_save_every == 0

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    async def save(self, state: AgentState, *, force: bool = False) -> CheckpointRecord | None:
        """按判据落盘；未触发/已写过/写入失败一律返回 None（不抛）。"""
        key = (state.run_id, state.step)
        if not force and not self.should_save(state):
            return None
        async with self._lock_for(state.run_id):
            if key in self._saved and not force:
                return None
            record = CheckpointRecord.from_state(state, saved_at=self._clock())
            try:
                await self._store.save(record)
            except Exception:  # 存储故障降级：任务继续（DoD B5）
                self.failures += 1
                return None
            self._saved.add(key)
            self.last_record = record
            return record

    async def load(self, run_id: str) -> CheckpointRecord | None:
        return await self._store.load(run_id)

    async def history(self, run_id: str) -> list[CheckpointRecord]:
        return await self._store.records(run_id)
