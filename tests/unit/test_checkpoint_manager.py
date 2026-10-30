"""B5 + 触发判据：CheckpointManager 的落盘时机、去重与故障降级。"""

from __future__ import annotations

from stableagent.core.checkpoint.manager import CheckpointManager
from stableagent.core.checkpoint.storage import CheckpointRecord, MemoryCheckpointStore
from stableagent.core.state import AgentStatus

from .test_checkpoint_storage import make_state


class BrokenStore(MemoryCheckpointStore):
    """存储故障替身：写入必抛，验证 P2 的"落盘失败不打断运行"。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def save(self, record: CheckpointRecord) -> None:
        self.calls += 1
        raise OSError("磁盘满了")


def manager(*, auto_save_every: int = 1, store: MemoryCheckpointStore | None = None):
    return CheckpointManager(
        store or MemoryCheckpointStore(),
        auto_save_every=auto_save_every,
        clock=lambda: 100.0,
    )


def test_should_save_by_step_modulo() -> None:
    mgr = manager()
    assert mgr.should_save(make_state(step=2))
    assert not mgr.should_save(make_state(step=0))
    assert not CheckpointManager(MemoryCheckpointStore()).should_save(make_state(step=3))


def test_should_save_terminal_and_paused_always() -> None:
    mgr = manager(auto_save_every=0)
    for status in (AgentStatus.PAUSED, AgentStatus.SUCCEEDED, AgentStatus.FAILED):
        assert mgr.should_save(make_state(step=7, status=status))


async def test_same_step_saves_once_unless_forced() -> None:
    mgr = manager()
    first = await mgr.save(make_state(step=1))
    assert first is not None and first.saved_at == 100.0
    assert await mgr.save(make_state(step=1)) is None  # 同步去重
    forced = await mgr.save(make_state(step=1, status=AgentStatus.PAUSED), force=True)
    assert forced is not None and forced.step == 1


async def test_b5_store_failure_degrades_without_breaking_run() -> None:
    store = BrokenStore()
    mgr = CheckpointManager(store, auto_save_every=1)
    assert await mgr.save(make_state(step=1)) is None
    assert mgr.failures == 1
    assert store.calls == 1
    assert mgr.last_record is None


async def test_load_and_history_delegate_to_store() -> None:
    mgr = manager()
    await mgr.save(make_state(step=1))
    await mgr.save(make_state(step=2))
    assert [record.step for record in await mgr.history("r1")] == [1, 2]
    assert (await mgr.load("r1")).step == 2
    assert await mgr.load("missing") is None


async def test_untriggered_save_writes_nothing() -> None:
    store = MemoryCheckpointStore()
    mgr = CheckpointManager(store, auto_save_every=5)
    assert await mgr.save(make_state(step=3)) is None
    assert await store.records("r1") == []
