"""Checkpoint 存储后端（P2 交付）：Memory / SQLite / Redis 三种实现 + 统一异步协议。

设计约束（docs/acceptance/p2-checkpoint-recovery.md 第 0 节）：
- L5：store 只有 `save/load/records/delete` 四个异步方法，按 `(run_id, step)` 去重覆盖写；
- Redis 客户端是**注入项**（只需 `get/set/delete` 协程），本包不硬依赖 redis 库；
- 一致性：写入只发生在步边界（由 `manager.py` 决定），本模块不理解 loop 语义。
"""

from __future__ import annotations

import abc
import asyncio
import json
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from stableagent.core.state import AgentState, CheckpointConfig

__all__ = [
    "CheckpointRecord",
    "CheckpointStore",
    "MemoryCheckpointStore",
    "RedisCheckpointStore",
    "RedisLike",
    "SqliteCheckpointStore",
    "build_store",
]


class CheckpointRecord(BaseModel):
    """一条快照记录：payload 是 AgentState 的 JSON 序列化原文。"""

    run_id: str
    step: int
    contract_version: str
    saved_at: float
    payload: str

    @classmethod
    def from_state(cls, state: AgentState, *, saved_at: float | None = None) -> CheckpointRecord:
        return cls(
            run_id=state.run_id,
            step=state.step,
            contract_version=state.contract_version,
            saved_at=time.time() if saved_at is None else saved_at,
            payload=state.snapshot(),
        )


class CheckpointStore(abc.ABC):
    """快照存储抽象。实现必须按 `(run_id, step)` 幂等覆盖写。"""

    @abc.abstractmethod
    async def save(self, record: CheckpointRecord) -> None: ...

    @abc.abstractmethod
    async def load(self, run_id: str) -> CheckpointRecord | None:
        """最近一次快照（step 最大）。"""

    @abc.abstractmethod
    async def records(self, run_id: str) -> list[CheckpointRecord]:
        """按 step 升序返回该 run 的历史快照。"""

    @abc.abstractmethod
    async def delete(self, run_id: str) -> None: ...


class MemoryCheckpointStore(CheckpointStore):
    """进程内后端：单测与开发默认；进程重启即丢失（P2 验收 B1）。"""

    def __init__(self, *, history_limit: int = 20) -> None:
        self._history_limit = history_limit
        self._data: dict[str, dict[int, CheckpointRecord]] = {}

    async def save(self, record: CheckpointRecord) -> None:
        steps = self._data.setdefault(record.run_id, {})
        steps[record.step] = record
        if len(steps) > self._history_limit:
            for stale in sorted(steps)[: len(steps) - self._history_limit]:
                del steps[stale]

    async def load(self, run_id: str) -> CheckpointRecord | None:
        steps = self._data.get(run_id)
        if not steps:
            return None
        return steps[max(steps)]

    async def records(self, run_id: str) -> list[CheckpointRecord]:
        steps = self._data.get(run_id, {})
        return [steps[step] for step in sorted(steps)]

    async def delete(self, run_id: str) -> None:
        self._data.pop(run_id, None)


class SqliteCheckpointStore(CheckpointStore):
    """文件持久化后端：同步 sqlite3 经 to_thread 下沉，调用面保持异步。"""

    def __init__(self, path: str | Path, *, history_limit: int = 20) -> None:
        self._path = str(Path(path).expanduser())
        self._history_limit = history_limit
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def path(self) -> str:
        return self._path

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    run_id TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    contract_version TEXT NOT NULL,
                    saved_at REAL NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, step)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints (run_id, step DESC)"
            )
            self._conn.commit()
        return self._conn

    def _save(self, record: CheckpointRecord) -> None:
        conn = self._connection()
        conn.execute(
            "INSERT OR REPLACE INTO checkpoints"
            " (run_id, step, contract_version, saved_at, payload) VALUES (?, ?, ?, ?, ?)",
            (
                record.run_id,
                record.step,
                record.contract_version,
                record.saved_at,
                record.payload,
            ),
        )
        conn.execute(
            "DELETE FROM checkpoints WHERE run_id = ? AND step NOT IN "
            "(SELECT step FROM checkpoints WHERE run_id = ? "
            "ORDER BY step DESC LIMIT ?)",
            (record.run_id, record.run_id, self._history_limit),
        )
        conn.commit()

    def _row_to_record(self, row: tuple[Any, ...]) -> CheckpointRecord:
        return CheckpointRecord(
            run_id=row[0],
            step=row[1],
            contract_version=row[2],
            saved_at=row[3],
            payload=row[4],
        )

    async def save(self, record: CheckpointRecord) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save, record)

    async def load(self, run_id: str) -> CheckpointRecord | None:
        records = await self.records(run_id)
        return records[-1] if records else None

    async def records(self, run_id: str) -> list[CheckpointRecord]:
        async with self._lock:
            rows = await asyncio.to_thread(self._select, run_id)
        return [self._row_to_record(row) for row in rows]

    def _select(self, run_id: str) -> list[tuple[Any, ...]]:
        cursor = self._connection().execute(
            "SELECT run_id, step, contract_version, saved_at, payload "
            "FROM checkpoints WHERE run_id = ? ORDER BY step ASC",
            (run_id,),
        )
        return cursor.fetchall()

    async def delete(self, run_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete, run_id)

    def _delete(self, run_id: str) -> None:
        conn = self._connection()
        conn.execute("DELETE FROM checkpoints WHERE run_id = ?", (run_id,))
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class RedisLike(Protocol):
    """redis 客户端的最小协议面（redis.asyncio 的 get/set/delete 与之兼容）。"""

    async def get(self, key: str) -> str | bytes | None: ...

    async def set(self, key: str, value: str) -> Any: ...

    async def delete(self, key: str) -> Any: ...


class RedisCheckpointStore(CheckpointStore):
    """Redis 后端：一个 run 一个 key，值为记录数组 JSON（读-改-写 + 实例锁）。

    取舍见 ADR-02：牺牲跨实例并发写入的最优性，换取零 schema、零依赖的可用性。
    """

    def __init__(
        self,
        client: RedisLike,
        *,
        prefix: str = "stableagent:ckpt",
        history_limit: int = 20,
    ) -> None:
        self._client = client
        self._prefix = prefix
        self._history_limit = history_limit
        self._lock = asyncio.Lock()

    def _key(self, run_id: str) -> str:
        return f"{self._prefix}:{run_id}"

    async def _read(self, run_id: str) -> dict[int, CheckpointRecord]:
        raw = await self._client.get(self._key(run_id))
        if raw is None:
            return {}
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        entries: Mapping[str, Any] = json.loads(text)
        return {int(step): CheckpointRecord(**item) for step, item in entries.items()}

    async def _write(self, run_id: str, steps: dict[int, CheckpointRecord]) -> None:
        kept = sorted(steps)[-self._history_limit :] if self._history_limit > 0 else sorted(steps)
        payload = json.dumps(
            {str(step): steps[step].model_dump() for step in kept}, ensure_ascii=False
        )
        await self._client.set(self._key(run_id), payload)

    async def save(self, record: CheckpointRecord) -> None:
        async with self._lock:
            steps = await self._read(record.run_id)
            steps[record.step] = record
            await self._write(record.run_id, steps)

    async def load(self, run_id: str) -> CheckpointRecord | None:
        records = await self.records(run_id)
        return records[-1] if records else None

    async def records(self, run_id: str) -> list[CheckpointRecord]:
        async with self._lock:
            steps = await self._read(run_id)
        return [steps[step] for step in sorted(steps)]

    async def delete(self, run_id: str) -> None:
        async with self._lock:
            await self._client.delete(self._key(run_id))


def build_store(
    config: CheckpointConfig,
    *,
    injected: CheckpointStore | None = None,
    redis_client: RedisLike | None = None,
) -> CheckpointStore | None:
    """按配置选后端；未启用持久化时返回 None（保持 P1 默认行为不变）。"""
    if injected is not None:
        return injected
    if config.auto_save_every <= 0 and config.recovery_strategy == "none":
        return None
    if config.backend == "memory":
        return MemoryCheckpointStore(history_limit=config.history_limit)
    if config.backend == "sqlite":
        if not config.sqlite_path:
            raise ValueError(
                "backend='sqlite' 需设置 CheckpointConfig.sqlite_path，"
                "或注入 checkpoint_store=SqliteCheckpointStore(path)"
            )
        return SqliteCheckpointStore(config.sqlite_path, history_limit=config.history_limit)
    if redis_client is not None:
        return RedisCheckpointStore(
            redis_client,
            prefix=config.redis_key_prefix,
            history_limit=config.history_limit,
        )
    raise ValueError(
        "backend='redis' 需注入 checkpoint_store=RedisCheckpointStore(client)"
        "（或 build_store(config, redis_client=...)），本框架不硬依赖 redis 客户端"
    )
