# Supervisor 监管控制台（M5）

## 定位

`python -m stableagent.supervisor.cli` 是单仓库 Beta 的最小人工监管面：只读首屏优先
展示待人工事项，写命令必须显式确认。它不发起任何外部网络请求，也不读取凭据。

## 启动

```bash
python -m stableagent.supervisor.cli \
  --db state.db --config console.json --operator alice status
```

## 配置

```json
{
  "repository": "owner/repo",
  "operators": ["alice"],
  "actors": ["alice"],
  "actions": ["read_repository", "comment_issue"],
  "default_validity_ms": 86400000,
  "notifications": {"enabled": true, "max_chars": 400, "since_ms": 0}
}
```

`operators` 是能执行写命令的人；`actors` 是 GitHub 上允许显式请求的人；两者可以不同。
`notifications` 必须与 Runtime 的 `NotificationConfig` 一致，否则 `approve` 重算的
digest 不同，会按退出码 3 拒绝。配置 `extra="forbid"`，不要写入 token。

## 只读命令

| 命令 | 作用 |
|---|---|
| `status` | 注意力（审批 → 失败 effect → waiting → ready）、Source 健康、队列与运行态 |
| `ticket <id>` | Ticket 状态、Mandate、Assignment、订阅与完整时间线 |
| `thread <key>` | Thread 的 Trigger / Decision 时间线（`object_id` 可作 `reevaluate` 目标） |

## 写命令与确认

| 命令 | `--confirm` 必须等于 |
|---|---|
| `approve <approval-id>` | approval id |
| `deny <approval-id>` | approval id |
| `stop <ticket-id>` | ticket id |
| `retry <effect-or-ticket-id>` | 同一个 id |
| `reevaluate <trigger-id>` | trigger id |
| `pause` | 字面量 `pause` |
| `resume` | 字面量 `resume` |

退出码：`0` 成功；`1` 未知对象或状态不允许；`2` 确认缺失或不匹配；`3` 通知草稿已
变化；`4` 非 operator。operator 校验先于确认校验。

## 通知生命周期

1. Runtime 在每轮 tick 后、外部 effect 派发前，按 Ticket 状态草拟
   accepted / attention / result / failure 四类通知，只写 pending 审批，不发消息。
2. 人工 `approve` 时 CLI 重算正文与 digest；一致才在**同一事务**内批准并入队
   `GITHUB_COMMENT` effect。下一步由 outbox 经 `ApprovalGate` 发送。
3. `deny` 后该 Ticket 的所有后续通知都不再草拟（拒绝即静默）；要停止任务请用 `stop`，
   它会在同一事务里撤销该 Ticket 的 pending 审批。

## retry 的 at-least-once

`retry <effect-id>` 只把 `FAILED` effect 改回 `PENDING`，沿用原幂等键与 payload。
如果失败发生在 GitHub 已收到评论但本地未记成功之后，重试可能重复一条评论；这是
M5 明确接受的 at-least-once 语义。`retry <ticket-id>` 只接受 `FAILED` Ticket。

## 安全边界

- 不读凭据；配置无 token 字段。
- 不发起网络请求；发送只经 Runtime 的 effect 与审批门。
- 首屏与详情不返回 `task`、`Trigger.payload`、`TicketInput.payload` 或日志正文。
- 所有写命令校验 operator；`--confirm` 防止误触。
