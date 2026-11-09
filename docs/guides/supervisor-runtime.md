# Supervisor Runtime 运维指南（P6 Beta）

本指南对应当前 Beta 的常驻运行面：单实例锁、启动恢复、暂停与优雅停止。Beta 仍锁定
`observe`，Runtime 不执行任何推断任务。

## 1. 单实例锁

- 锁是 `runtime_lock` 单例行（`id = 1`），字段为 `owner` / `acquired_at` /
  `heartbeat_at` / `expires_at`，随 SQLite 库走，可审计。
- 启动时 `acquire_runtime_lock(owner, now, ttl_ms)`：无行则插入；其他 owner 未过期则
  拒绝并抛 `RuntimeLockError`；同 owner 可重入刷新租约。
- 每轮 `step()` 先心跳；心跳失败（owner 变更或租约过期）立即抛 `RuntimeLockError`，
  不继续写库。正常情况下进程死亡只等 `expires_at` 到点即可被接管。
- 不要手工删 `runtime_lock` 行来"解锁"：先确认旧进程已退出，或等租约自然过期。

## 2. 启动顺序

`Runtime.start()` 固定四步，任何 effect/worker 派发都不在此之前发生：

1. 获取单实例锁（失败即退出，不做恢复）。
2. `OrchestrationService.recover()`：RUNNING Assignment → INTERRUPTED，入队
   `:relaunch` effect，并重置在途 effect。
3. 读 `runtime_control.paused`；暂停则不补路由。
4. 补路由 `list_unrouted_triggers()`：Trigger 已落库但没有 Decision 的崩溃窗口，
   逐条 `RouterService.receive`（按其既有去重语义安全重放）。

## 3. 每轮推进

`Runtime.step()` 的顺序固定：

1. 心跳续租。
2. `Source.poll()`：Trigger/Thread/Channel/cursor 由 Source 在同一事务落库；
   `SourceError` 只让本轮空转（健康已记入 `source_health`），不杀调度。
3. 暂停检查：暂停时只记录 Trigger，不路由、不 tick、不派发外部 effect。
4. 对每个新事件 `RouterService.receive`；duplicate 不重复建票。
5. `Scheduler.tick()`：从 Store 派生可运行工作并派发 worker effect。
6. 外部 `EffectDispatcher.dispatch_due()`：GitHub 写与其它 P5 effect。

`Runtime.serve(max_steps=None)` 只是循环执行 `step()`；`max_steps` 仅供演练与测试。

## 4. 暂停语义

- `pause_processing` 把暂停位落进 `runtime_control`（`paused=1`），跨重启保留。
- 暂停时：Source 仍记录 Trigger；新 Eval、Ticket dispatch、外部 reply effect 与超时推进
  全部停止；不硬杀正在执行的 Worker。
- `resume_processing` 清暂停位；Runtime 已启动时立即补路由暂停期间记录的 Trigger，
  下一轮 `step()` 再派发。
- 每次暂停/恢复写 `actor`、`reason`、`changed_at` 与递增的 `generation`。

## 5. 优雅停止

- `Runtime.stop()` 先置停标志（不再进入下一轮 step），再 `Scheduler.wait_idle(timeout_ms)`
  等宽限期（默认 5s）。
- 宽限期到点仍不取消 Worker：返回 `RuntimeStop(drained=False, running_assignments=N)`，
  随后释放锁。重启后由 P4 `recover()` 接管中断的 Assignment。
- 停止不删除 worktree、Ticket、Effect、Input、Report 或 checkpoint；历史全部保留。
- `stop()` 幂等：未启动或重复调用都返回 `drained=True`。

## 6. 崩溃恢复窗口

| 窗口 | 重启后的行为 |
|---|---|
| Trigger 落库后、路由前 | `start()` 用 `list_unrouted_triggers` 补路由 |
| Decision/Ticket 事务后、launch 派发前 | 事务内 launch effect 仍在 outbox，下一轮 tick 派发一次 |
| GitHub comment effect 前 | effect 仍 PENDING，重启后只执行一次；已成功的不重放 |
| Worker run 中 | `recover()` 把 RUNNING 收成 INTERRUPTED 并入队 `:relaunch`，走 `recover` 不冷启动 |
| cursor 更新失败 | Trigger/Thread/cursor 同事务回滚；cursor 未推进，下一次 poll 重新取到事件 |

## 7. 运行控制命令与安全边界

`RuntimeController` 提供：`status`、`pause_processing`、`resume_processing`、
`retry_ticket`（仅 FAILED）、`stop_ticket`、`reevaluate`。

- 所有命令只认 `RouterConfig.operators`，大小写不敏感；非 operator 抛
  `RouterPermissionError`。
- `status` 只返回运行态摘要（锁/暂停/Source 健康/队列/effect/审批计数），不返回外部正文；
  待人工首屏与注意力排序属于 M5。
- `stop_ticket` / `reevaluate` 复用既有 `SupervisorService`；`retry_ticket` 复用
  `OrchestrationService`，均不新开业务路径。
- Runtime 不 import `execution` / `integrations`；GitHub 写只经 effect handler 注入。
