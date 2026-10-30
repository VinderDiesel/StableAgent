"""B 节：checkpoint 存储后端一致性（Memory / SQLite / Redis 三套共享断言）。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from stableagent.core.checkpoint.storage import (
    CheckpointRecord,
    CheckpointStore,
    MemoryCheckpointStore,
    RedisCheckpointStore,
    SqliteCheckpointStore,
    build_store,
)
from stableagent.core.state import AgentState, AgentStatus, CheckpointConfig, Message
from tests.support import FakeRedisClient

BACKENDS = ("memory", "sqlite", "redis")


def make_state(
    run_id: str = "r1",
    step: int = 1,
    status: AgentStatus = AgentStatus.RUNNING,
) -> AgentState:
    return AgentState(
        run_id=run_id,
        agent_name="t",
        status=status,
        step=step,
        messages=[
            Message(role="user", content="q"),
            Message(role="assistant", content="a"),
        ],
        usage={"prompt_tokens": 10 * step},
    )


async def build(backend: str, tmp_path: Path, *, history_limit: int = 5) -> CheckpointStore:
    if backend == "memory":
        return MemoryCheckpointStore(history_limit=history_limit)
    if backend == "sqlite":
        return SqliteCheckpointStore(
            tmp_path / f"ckpt-{backend}.sqlite", history_limit=history_limit
        )
    return RedisCheckpointStore(FakeRedisClient(), history_limit=history_limit)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_b4_save_load_roundtrip_is_lossless(backend: str, tmp_path: Path) -> None:
    store = await build(backend, tmp_path)
    state = make_state(step=3)
    await store.save(CheckpointRecord.from_state(state))
    record = await store.load(state.run_id)
    assert record is not None
    assert AgentState.loads(record.payload) == state
    assert record.contract_version == state.contract_version


@pytest.mark.parametrize("backend", BACKENDS)
async def test_b4_records_sorted_and_run_isolated(backend: str, tmp_path: Path) -> None:
    store = await build(backend, tmp_path)
    for step in (2, 1, 3):
        await store.save(CheckpointRecord.from_state(make_state(step=step)))
    await store.save(CheckpointRecord.from_state(make_state(run_id="other", step=9)))

    assert [record.step for record in await store.records("r1")] == [1, 2, 3]
    assert (await store.load("r1")).step == 3  # load 取最近（step 最大）
    assert [record.step for record in await store.records("other")] == [9]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_b4_history_limit_keeps_newest(backend: str, tmp_path: Path) -> None:
    store = await build(backend, tmp_path, history_limit=2)
    for step in (1, 2, 3, 4):
        await store.save(CheckpointRecord.from_state(make_state(step=step)))
    assert [record.step for record in await store.records("r1")] == [3, 4]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_b4_same_step_overwrites(backend: str, tmp_path: Path) -> None:
    store = await build(backend, tmp_path)
    await store.save(CheckpointRecord.from_state(make_state(step=2, status=AgentStatus.RUNNING)))
    await store.save(CheckpointRecord.from_state(make_state(step=2, status=AgentStatus.PAUSED)))
    records = await store.records("r1")
    assert len(records) == 1
    assert AgentState.loads(records[0].payload).status is AgentStatus.PAUSED


@pytest.mark.parametrize("backend", BACKENDS)
async def test_b4_delete_clears_run(backend: str, tmp_path: Path) -> None:
    store = await build(backend, tmp_path)
    await store.save(CheckpointRecord.from_state(make_state()))
    await store.delete("r1")
    assert await store.load("r1") is None
    assert await store.records("r1") == []


async def test_b1_memory_backend_dies_with_process(tmp_path: Path) -> None:
    store = await build("memory", tmp_path)
    await store.save(CheckpointRecord.from_state(make_state()))
    assert await store.load("r1") is not None
    assert await MemoryCheckpointStore().load("r1") is None  # 新实例读不到


async def test_b2_sqlite_backend_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "durable.sqlite"
    writer = SqliteCheckpointStore(path)
    await writer.save(CheckpointRecord.from_state(make_state(step=4)))
    writer.close()

    reader = SqliteCheckpointStore(path)
    record = await reader.load("r1")
    assert record is not None and record.step == 4
    reader.close()


async def test_b3_redis_backend_uses_injected_client_only() -> None:
    client = FakeRedisClient()
    store = RedisCheckpointStore(client, prefix="sa:ckpt")
    await store.save(CheckpointRecord.from_state(make_state(step=1)))
    assert list(client.data) == ["sa:ckpt:r1"]  # key 规则可预测，不污染他人空间
    assert (await store.load("r1")).step == 1


async def test_b2_sqlite_concurrent_writes_do_not_error(tmp_path: Path) -> None:
    """同进程并发落盘（回看落盘与强制落盘交错）：锁 + 幂等主键必须吃掉竞态。"""
    store = SqliteCheckpointStore(tmp_path / "race.sqlite")
    await asyncio.gather(
        *(store.save(CheckpointRecord.from_state(make_state(step=s))) for s in range(1, 9))
    )
    assert [record.step for record in await store.records("r1")] == list(range(1, 9))
    store.close()


def test_backend_selection_by_config(tmp_path: Path) -> None:
    """配置 -> 后端 的唯一决策点（含"未启用即 None"，保证 P1 默认零 I/O）。"""
    assert build_store(CheckpointConfig()) is None  # auto_save_every=0 且 strategy=none
    assert build_store(CheckpointConfig(auto_save_every=1)) is not None
    assert isinstance(
        build_store(CheckpointConfig(auto_save_every=1, backend="sqlite", sqlite_path="a.db")),
        SqliteCheckpointStore,
    )
    with pytest.raises(ValueError, match="sqlite_path"):
        build_store(CheckpointConfig(auto_save_every=1, backend="sqlite"))
    with pytest.raises(ValueError, match="redis"):
        build_store(CheckpointConfig(auto_save_every=1, backend="redis"))
    redis = build_store(
        CheckpointConfig(auto_save_every=1, backend="redis", redis_key_prefix="x"),
        redis_client=FakeRedisClient(),
    )
    assert isinstance(redis, RedisCheckpointStore)
    sentinel = MemoryCheckpointStore()
    assert build_store(CheckpointConfig(), injected=sentinel) is sentinel  # 注入优先


@pytest.mark.skipif(
    not os.environ.get("STABLEAGENT_REDIS_URL"),
    reason="未设置 STABLEAGENT_REDIS_URL：真实 Redis 用例跳过（CI 离线不 fail）",
)
async def test_b3_real_redis_when_configured() -> None:
    """真实 Redis 走同一组断言：客户端注入面与 fake 完全一致，无额外适配代码。"""
    import redis.asyncio as aioredis  # 延迟导入：未安装 redis 也能 import 本包

    client = aioredis.from_url(os.environ["STABLEAGENT_REDIS_URL"])
    store = RedisCheckpointStore(client, prefix="stableagent:test")
    try:
        await store.save(CheckpointRecord.from_state(make_state(step=2)))
        record = await store.load("r1")
        assert record is not None and record.step == 2
        assert AgentState.loads(record.payload).usage == {"prompt_tokens": 20}
    finally:
        await store.delete("r1")
        await client.aclose()
