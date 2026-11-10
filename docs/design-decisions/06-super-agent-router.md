# ADR-06：单仓库 Super-Agent Router（P6）

- 状态：Accepted（2026-11-08 经维护者确认）
- 日期：2026-11-08
- 关联：[公共契约](00-contracts.md)、[ADR-04 Ticket Orchestration](04-ticket-orchestration.md)、[ADR-05 受控执行](05-guarded-execution.md)、[P6 实施规格](../specs/p6-single-repository-super-agent-beta.md)、[P6 验收账本](../acceptance/p6-supervisor-beta.md)
- 代码入口：`stableagent/supervisor/`（本 ADR 批准的新包）

## 背景

P5 收口后（DoD 46/46、run 42433929377），系统已具备持久化编排、结构化授权、隔离执行与
显式 GitHub 写入。P6 要把这些能力组合成一个**常驻控制平面**：持续接收单仓库事件，把它们
去重、归属到 Thread，只在明确授权时创建 Ticket，并把后续评论、PR 与 CI 变化作为上下文
输入。规格第 2 节给出模型与流程，第 3 节给出 6 个工作包；若干实现口径留白，本 ADR 逐条
定死，作为 [P6 验收账本](../acceptance/p6-supervisor-beta.md) 的决策来源。

## 约束

1. **旧层语义不变**：`orchestration/` 的 Scheduler、Store 既有表、Effect 协议与
   `OrchestrationService` 十命令不得因 P6 改签名或改行为；`stableagent/core/` 零 diff。
2. **方向锁**：依赖方向为 `supervisor → integrations → execution → control →
   orchestration → core`。旧包禁止 import `stableagent.supervisor`；反过来不成立。
3. **默认不执行**：P6 Beta 锁定 `observe`，`auto` 只保留接口并标记未启用；merge 与
   auto-merge 继续结构性缺席（L6）。
4. **离线门禁**：单元与集成测试不得依赖网络、真实 GitHub 或 Docker；Beta 演练另行显式
   授权（沿用 L8 口径）。
5. **外部文本不是授权**：Issue/PR 正文、评论、CI 日志与 Worker 输出只能进入 digest、
   上下文与 Report，不能成为 Mandate 或 Policy 的判定依据（L4）。

## 决策

### 1. 包边界：新增 supervisor，单向依赖，不顶层重导出

```text
supervisor → integrations → execution → control → orchestration → core
```

`supervisor/` 承载 Trigger/Thread/Subscription/Decision/Router/Runtime/Views；
`OrchestrationService` 仍是 Ticket 状态机的唯一入口。`supervisor` 的类型不在
`stableagent` 顶层重导出——P6 期间记录结构仍不承诺稳定（沿用 ADR-04 决策 1）。

### 2. 持久化分层：`SupervisorStore(Store)` 子类，同连接同事务

- supervisor 的表、索引、marker 与 CRUD 全部在 `supervisor/store.py`；`orchestration/store.py`
  零改动，避免 `orchestration → supervisor` 的反向依赖。
- `SupervisorStore.__init__` 先调用 `Store.__init__`，再在独立事务内建表并写入
  `p6-supervisor-v1` marker；`ORCHESTRATION_SCHEMA_VERSION` 保持 `0.1.0`。
- 子类复用父类的 `_conn` 与 `transaction()`，因此 Trigger/Decision/Subscription 与
  Ticket/Assignment/Effect **可以在同一个 `BEGIN IMMEDIATE` 事务内落库**（规格 2.1）。
  事务体内仍禁止 `await`（L3）。
- 迁移是纯增量的：P5 库打开后旧行不变，`p5-control-v1` 与 `p6-supervisor-v1` 两个
  marker 并存；重复打开幂等。

### 3. Trigger：不可变、规范化 digest、外部 ID 去重

- `Trigger` 为 `frozen=True, extra="forbid"` 的 Pydantic 记录；`payload` 一经构造不再由
  本包提供任何修改入口，数据库也没有 UPDATE 方法。
- `digest = sha256(canonical_json(payload))`，canonical JSON 固定为
  `sort_keys=True, separators=(",", ":"), ensure_ascii=False`。构造时校验 digest，不一致
  直接拒绝。
- 去重落在数据库：`UNIQUE(source, external_id)` + `INSERT OR IGNORE`；`insert_trigger()`
  返回 `True`（新事件）或 `False`（重复）。不得只靠内存集合去重。

### 4. Thread 与 Channel：稳定映射，Channel 不授予权限

- Thread 的稳定键由 Source 决定，数据库同时钉住
  `UNIQUE(source, repository, kind, external_ref)`；路由关系必须显式记录，禁止按文本猜测。
- Channel 是**可写目的地**（Issue comment / PR comment / review reply），只描述投递位置；
  它不携带 actor、scope 或 action，不能作为授权来源。写入必须走 P5 outbox effect。
- `Channel` 以 `UNIQUE(thread_key, kind, external_ref)` 保证同一目的地只有一行。

### 5. Subscription：own / context 两种模式，context 不扩大授权

- `own` 表示 Ticket 的原始 Thread；`context` 表示为提供上下文而订阅的其他 Thread。
- live 订阅以部分唯一索引
  `(ticket_id, thread_key, mode) WHERE ended_at IS NULL` 保证幂等；结束后可重新订阅。
- 后续评论、PR 与 CI 输入可以扇出给活跃 Ticket，但**不扩大 Mandate**，也不能仅凭自身
  创建执行任务（规格 2.2 第 1 条）。

### 6. Decision：六种 outcome、三种 strength、observe 默认

- `outcome ∈ {accept, observe, ignore, attach, stop, unavailable}`；
  `strength ∈ {explicit, inferred, not_admitted}`。
- Beta 发布配置锁定 `observe`：推断候选只记录 would-accept，不创建可执行 Ticket；
  `auto` 接口保留但未启用，只有 P7 完成误报率与回滚评估后才能部署显式开启。
- 幂等：`UNIQUE(request_digest, attempt)`。`unavailable` 允许以递增 `attempt` 重新评估；
  同一 attempt 不得写第二条。

### 7. 事务边界：一次 `receive()` 内的状态推进原子

同一事务内完成：Trigger 去重落库 → Thread 更新 → Source cursor 推进 → Subscription 扇出
→ Decision（以及 explicit 情况下的 Mandate/Ticket/Assignment/effect）创建。任一步失败整体
回滚；cursor 不得先于 Trigger 推进。Router 之外的组件不得直接写这些表。

### 8. Source 与 Channel 协议：只产 Trigger，不建 Ticket

- Source 的职责是把外部事件标准化为 Trigger 并维护 cursor/健康；它不能创建 Ticket、
  不能调 Worker、不能扩大 Mandate。
- Channel 只供已授权 effect 投递消息；消息内容受注意力预算约束（决策 9）。
- 具体选择 polling 还是 webhook 由 WP-6.2 决定并在文档中写明部署权限；两者都支持时
  统一在 Trigger 层去重。

### 9. 注意力预算：监管面只读、外部消息克制

- `views.py` 只提供只读查询，排序固定：待人工问题/审批 → 失败 effect → ready 待 review
  的 Ticket → Source 健康 → 队列负载。
- 已接受任务的确认、结果、attention 与失败通知各最多一条结构化消息；普通进度与 CI
  更新默认静默，只留在监管入口。
- 视图不返回 `Trigger.payload`、`TicketInput.payload` 或原始日志正文。

### 10. 迁移与兼容：无损升级，降级路径写文档

- P6 只新增表与索引；不回写 P4/P5 记录。
- 降级需要旧二进制忽略新表；由于没有列变更，P5 二进制可直接打开 P6 库读取旧表。
- 运维文档必须写明备份、marker 检查与降级步骤（WP-6.1 交付，随 Beta 文档落盘）。

### 11. Beta 演练与发布门禁：离线先行，observe 数据是 auto 的唯一依据

- M6 交付可复现的离线演练（`examples/06_superagent_beta.py`）与只读 `BetaReport`
  （`views.beta_report` ← `store.beta_snapshot`）；真实 GitHub HTTP 客户端不在 M6 实现；
  真实仓库试跑步骤见 [Beta runbook](../guides/supervisor-beta.md)。
- `BetaReport` 字段固定：triggers/decisions/decision_reasons/would_accept/tickets/
  recovered_assignments/approvals/notification_counts/effects/retried_effects/source_health；
  只读、不返回 payload。误报/漏报与人工接受/拒绝原因只进 runbook 人工记录表。
- **默认安全**：`auto` 默认关闭；代码与离线测试须证明可开可关、可审计、可回滚；PR
  重复数在离线演练中必须为 0。
- **补充 A（2026-11-10）**：P6 出口第 7 项按工程口径豁免；见
  [P6 验收账本「开发收口裁定」](../acceptance/p6-supervisor-beta.md)。
- **补充 B（2026-11-10，覆盖后续全部任务）**：工程收口**一律**只看代码完成与离线可靠
  性，不以真实 observe / 生产试跑数据为 DoD。原「observe 数据是进入 auto 的唯一依据」
  降级为**可选运维建议**（guide/runbook），不再阻塞任何阶段或里程碑收口，也不阻塞
  实现/测试 `auto` 代码路径。生产环境是否打开 `auto` 由运维自行决定。总口径见
  [Roadmap「工程收口口径」](../Roadmap.md)。

## 被否决方案

- **独立 supervisor SQLite**：包边界更干净，但 Trigger/Decision 与 Ticket/Assignment
  无法同事务，直接违背规格 2.1 的原子推进要求。
- **直接扩 `orchestration/store.py`**：符合规格字面，但需要 `orchestration` 在函数内
  late import supervisor 模型（反向依赖）或改用无类型 dict 签名；两者都削弱 L2 或类型。
- **P6 直接开放 auto**：没有 observe 数据就自动接受推断任务，违反规格 2.3 与 Roadmap
  的发布门禁。
- **以 webhook 单路径开工**：需要公网回调与额外权限，离线门禁无法覆盖；polling 先行的
  决定留给 WP-6.2，本 ADR 只钉住 Trigger 层去重。

## 后果与代价

- 新增一个持久化子类与七张表；`Store` 的连接与事务实现成为跨阶段共享内核，后续改动需
  同时考虑 supervisor 使用方。
- Trigger payload 与视图的"不泄露"要靠测试钉住，而不是类型系统。
- `observe` 默认意味着 Beta 初期不会自动处理推断任务；这是产品选择，不是能力缺失。

## 测试与证据

M1 的实测门禁、迁移与只读视图证据回填在 [P6 验收账本](../acceptance/p6-supervisor-beta.md)
第 1 节 M1 条目；本 ADR 不复制会随时间腐坏的读数。

## 阶段交接

P7 只能在不改写 P6 单仓库语义的前提下扩展：新 Source 必须实现 Trigger/Thread/Channel
协议；多 Provider、Memory、MCP server、transport 与多租户都观察既有记录；`observe`
数据是评估 `auto` 的唯一依据；cursor、outbox 幂等、Mandate revalidation 与注意力协议
是不可回退基线。
