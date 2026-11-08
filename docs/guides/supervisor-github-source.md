# GitHub Source 部署与 cursor 契约（P6 M2）

本指南说明 P6 Beta 的 GitHub 事件接入：只有 polling 一条主路径，只读、单仓库、
`observe` 模式。接入只产生 Trigger/Thread/Channel 候选，**不产生任何授权**；建票与
执行仍由 P5 Policy/Mandate 决定。

## 1. 只读权限

读 token 与 P5 的写 token 分离：

| 权限 | 级别 | 用途 |
|---|---|---|
| Metadata | Read | 仓库可见性 |
| Issues | Read | Issue 与 issue comment |
| Pull requests | Read | PR、review、review comment |
| Actions / Checks | Read | CI status |

读 token **不得**包含 Issues/Pull requests 写权限，也不得授予 `contents: write`。
凭据只存在于真实客户端内部（L7）：不进 prompt、日志、Report、Ticket、AgentState、
worktree 元数据或 effect payload。

## 2. polling 主路径

每一轮调用 `GitHubSource.poll(limit=100, max_pages=10)`：

1. 从 `source_cursors` 读出 `source='github'` 的 cursor；首次为空。
2. 最多读 `max_pages` 页；未读完时把 `next_cursor` 持久化，下一轮从该 cursor 继续。
3. 整批标准化后按 `(occurred_at, external_id)` 升序输出。
4. Trigger、Thread、Channel 与 cursor 在**同一个 `BEGIN IMMEDIATE` 事务**内写；
   任一步失败整批回滚，不留下半个事实。

cursor 对调用方是不透明字符串。真实客户端必须在内部编码每种资源的分页位置并保证
前进；`FakeGitHubReadClient` 用 `offset:<n>` 模拟。若客户端返回与输入相同的 cursor，
Source 以 `source.schema` 失败，避免死循环。

## 3. 事件映射

| kind | external_id | Thread key | Channel |
|---|---|---|---|
| issue | `github:{repo}:issue:{n}` | `issue:{repo}#{n}` | 无 |
| pull_request | `github:{repo}:pull_request:{n}` | `pr:{repo}#{n}` | 无 |
| issue_comment | `github:{repo}:issue_comment:{id}` | Issue 或 PR thread | `#n`（ISSUE_COMMENT / PULL_REQUEST_COMMENT） |
| review_comment | `github:{repo}:review_comment:{id}` | `review:{repo}#{pull}:{review_id}` | `review:{review_id}`（REVIEW_REPLY） |
| review | `github:{repo}:review:{id}` | `review:{repo}#{pull}:{review_id}` | `review:{review_id}`（REVIEW_REPLY） |
| ci_status | `github:{repo}:ci_status:{id}` | 有 PR 用 `pr:{repo}#{n}`，否则 `repo:{repo}` | 无 |

Thread 状态：Issue/PR `state == "closed"`、review `state == "dismissed"` 或删除事件
一律落 `closed`；`upsert_thread` 只允许状态单调前进（`open → closed → stopped`）。

## 4. 去重

- 数据库：`triggers` 以 `UNIQUE(source, external_id)` 兜底；重复插入返回 false。
- 记录：Trigger id 由 `sha256(external_id)` 派生，重启后仍是同一个 id。
- 批内：同一 `external_id` 只保留首次出现，重复计入 `PollBatch.duplicates`。
- 自身事件：`actor` 与配置的 `bot_login` 大小写无关地比较，命中即丢弃，不进入候选。

## 5. 错误与健康

| 错误 | code | cursor health | 处理 |
|---|---|---|---|
| 网络 / 限流 / 5xx | `source.transient` | `degraded` | 下一轮重试，`consecutive_failures` 递增 |
| 403 | `source.auth` | `failed` | 人工检查 token 与可见性 |
| 404 | `source.gone` | `failed` | 仓库不可见或事件已删除，停止重试 |
| 载荷不符合契约 | `source.schema` | `failed` | 修复映射或 fixture 后再启动 |

失败时整批不落库；健康记录在抛错前写入，监管视图的 `source_health` 因此能看到原因。

## 6. webhook 的后续接法

P6 Beta 不启用 webhook。后续若接入：

- webhook 只负责把同一份 GitHub 资源转换成 `GitHubReadItem`，走同一个 `normalize`。
- 去重仍只在 Trigger 层做（`UNIQUE(source, external_id)`），不新增第二条写路径。
- 在 M6 之前不改变 `observe` 锁定，不因 webhook 到达而自动建票。

## 7. 安全边界

- 外部文本（Issue/PR 正文、评论、CI 日志）只进 Trigger payload 与 digest，永不参与准入（L4）。
- Channel 只是回写目的地，不携带授权（ADR-06 决策 4）。
- 读权限不扩大 Mandate；Subscription 的 `context` 模式同样不扩大（I3）。
