"""worktree 的创建由 outbox effect 驱动（规格 2.3）：崩溃重放必须命中同一 worktree。

幂等有两层，缺一不可：账上——`UNIQUE(kind, idempotency_key)` 让同一个 `prepare:{assignment_id}`
只有一行；现场——`GitWorktreeManager.prepare` 的复用四要素让第二次执行既不新建目录也不新建分支
（C2）。handler 是 `async def`（P4 的 `EffectHandler` 类型如此），内部只调同步 git：
`EffectDispatcher` 已断言 handler 不在事务内，L3 不受影响。
"""

from __future__ import annotations

from stableagent.execution.worktree import GitWorktreeManager
from stableagent.orchestration.model import Effect
from stableagent.orchestration.outbox import EffectHandler
from stableagent.orchestration.store import Store

PREPARE_KEY_PREFIX = "prepare"


def prepare_idempotency_key(assignment_id: str) -> str:
    """键形状与 P4 的 `launch:{assignment.id}:{attempt}` 同源：外部稳定 id 打头。

    prepare 的键**不带 attempt**——一个 assignment 只有一个现场，重试同一次 assignment 必须复用
    同一目录与同一分支（规格 2.3），所以重试不能换键。
    """
    return f"{PREPARE_KEY_PREFIX}:{assignment_id}"


def prepare_worktree_handler(manager: GitWorktreeManager, store: Store) -> EffectHandler:
    """payload 形状：`{ticket_id, assignment_id, repository, base_ref}`（不含凭据，L7）。"""

    async def handle(effect: Effect) -> None:
        payload = effect.payload
        ticket = store.get_ticket(payload["ticket_id"])
        if ticket is None:
            raise ValueError(f"effect {effect.id} 的 Ticket {payload['ticket_id']!r} 不在库里")
        manager.prepare(ticket, repository=payload["repository"], base_ref=payload["base_ref"])

    return handle
