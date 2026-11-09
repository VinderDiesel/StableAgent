# Supervisor Router 运维指南（P6 Beta）

本指南对应当前 Beta 的 `observe` 锁定：Router 只接受显式任务，推断候选只记录不执行。

## 1. 唯一入口

`RouterService.receive(trigger)` 是外部事件进入领域的唯一命令。它在一个
`BEGIN IMMEDIATE` 内完成 Trigger 去重、Subscription 扇出与 Decision；任一步失败整体
回滚。Source 已持久化的 Trigger 仍可交给 `receive`：路由层用"是否已有 Decision"判断
是否重复，存储层用 `UNIQUE(source, external_id)` 兜底。

## 2. 去重与顺序

- 同一 `external_id` 只存一行 Trigger；重复投递返回 `duplicate=True`，不新增 Decision、
  Ticket 或 effect。
- 一个 Trigger 的同一 Ticket 至多扇出一条 Input；`sequence` 按 Ticket 递增，投递走
  `deliver_input` effect。
- `reevaluate` 是运维重评估，不重复扇出，只追加一条递增 `attempt` 的 Decision。

## 3. 三档自治

| mode | explicit | inferred |
|---|---|---|
| `off` | 接受 | IGNORE，记录 `autonomy_off`，不调评估器 |
| `observe` | 接受 | OBSERVE，只记录 would-accept 与 reason code |
| `auto` | 构造期拒绝 | 未启用 |

P6 Beta 不允许部署 `auto`；该枚举值只为 P7 保留。

## 4. outcome 与 reason code

| outcome | 触发条件 | 典型 reason |
|---|---|---|
| `accept` | 显式、未准入问题不存在、无既有 owner/重复 | `admitted` / `resumed` |
| `observe` | 推断候选；已结束 Ticket 的后续；多 owner | `inferred_candidate` / `resume_requires_operator` / `ambiguous_owner` |
| `ignore` | 未准入、正文畸形、无 Thread、`off` | `actor_not_allowlisted` / `malformed_request` / `thread_unknown` / `autonomy_off` |
| `attach` | 已有 Ticket 的 own 后续或重复请求 | `owned_thread_followup` / `duplicate_request` |
| `stop` | 已 stop Ticket 的后续 | `stopped_ignored` |
| `unavailable` | 评估器异常 | `assessor_error` |

## 5. 命令与权限

所有命令的 `actor` 必须在 `RouterConfig.operators` 中（大小写不敏感）：

- `attach(ticket, thread)`：相关 Thread 升为 context 订阅，幂等。
- `subscribe(ticket, trigger)`：按 Trigger 主 Thread 执行 attach。
- `unsubscribe(ticket, thread)`：结束该 Thread 的 live 订阅，返回结束行数。
- `stop(ticket)`：取消未结束 Ticket、入队 cancel effect、结束 context 订阅；own 订阅保留
  作为"已停"护栏，后续明确消息记 `stop` 并丢弃。
- `handback(ticket, thread, trigger)`：own 降为 context，之后的明确消息可创建新 Ticket。
- `resume(ticket, trigger)`：只允许 `completed` / `failed`；要求显式请求通过准入，并用
  `when="launch"` 复检 Mandate；`cancelled` 永久拒绝。
- `record_pull_request(ticket, report)`：Worker 报告里带 `pull_number` 才能把 PR Thread
  升为 own。

## 6. 订阅生命周期

- 创建 Ticket 时绑定 origin Thread 为 own。
- Worker 报告 PR 后由 `record_pull_request` 添加 own PR 订阅；同一 Thread 的 context
  订阅被结束，避免重复投递。
- 其他 Thread 只能是 context；context 只提供上下文，不扩大 Mandate、不创建 Ticket。
- Ticket 完成后 own 订阅保留；后续明确消息存为 pending Input 并记
  `resume_requires_operator`，由 operator 决定是否 `resume`。
- 人工 `stop` 后 own 订阅也保留但不接收：后续明确消息记 `stopped_ignored`。

## 7. 安全边界

- 外部正文只进 digest、TicketInput 与 reason 记录，不决定 action 集合（L4）。
- Router 事务体内无 `await`（L3）；Ticket/Assignment/Effect 与 Decision 同事务。
- Channel 不授予权限；`merge` / `auto_merge` 结构性缺席（L6）。
- 全部测试离线；真实 GitHub 演练与推送需维护者显式授权（L8）。
