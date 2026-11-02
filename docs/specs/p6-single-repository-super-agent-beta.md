# P6 详细实施规格：单仓库 Super-Agent Beta

> **阶段定位**：将 P4/P5 已验证的 Ticket、授权、受控执行和 GitHub effect 组合为一个面向**单一 GitHub 仓库**的常驻 Router + 多 Worker 系统。系统可持续接收 Issue、PR、评论与 CI 事件，将其路由到正确 Ticket，并只在需要人行动或成果可用时沟通。
>
> **开始条件**：P5 全部退出条件满足。特别是 Policy/Mandate、GitHub 写入幂等、worktree 隔离、outbox 恢复与显式任务闭环已有可复现证据。
>
> **完成条件**：GitHub 事件可稳定去重、归属、审计和恢复；系统默认只处理 allowlist 的显式请求，所有推断型候选在 `observe` 中仅记录不执行；一个真实测试仓库能安全完成 Beta 试运行。

## 1. 产品目标与边界

### 目标

1. 常驻运行一个 Router，接收并标准化单仓库 GitHub 事件。
2. 将事件建模为不可变 Trigger，将同一 Issue、PR 或评论会话建模为 Thread。
3. 将被授权的显式请求创建为 Ticket；将同一 Ticket 的后续讨论、PR 和 CI 信息作为 Subscription 输入。
4. 将不明确、超出 scope 或来自主动规则的事件创建为可审计 Decision，而不是盲目执行。
5. 使用 P4 Scheduler 并发管理多个 Ticket Worker，使用 P5 的 worktree、Policy、Mandate 和 outbox 执行副作用。
6. 提供最小监管入口，优先展示需要人工决策、审批、失败恢复和可用结果，避免健康状态和过程消息打扰团队。

### 非目标

- 不支持多仓库、多组织、飞书/Slack/Jira、跨 Source 身份映射或多租户。
- 不在 P6 自动 merge、自动修改仓库默认分支、自动接受任何推断型任务。
- 不把评论文本、CI 日志或 Worker output 当作授权来源。
- 不实现全面企业控制台、流式 Web UI、长时 Memory 或全局知识图谱。
- 不绕开 P4 Outbox、P5 Policy 或 RepositoryWorkerExecutor 直接调用 GitHub / Worker。

### 注意力预算原则

Router 的通信规则是产品契约：

| 情况 | 对外行为 |
|---|---|
| 已接受的显式任务 | 原始 Thread 中一次简短确认，包含 Ticket/范围摘要 |
| Worker 正常执行、常规进度、普通 CI 更新 | 默认静默，记录在监管入口 |
| 需要审批、范围/设计决策、人工输入 | 一次结构化问题，说明所需动作和影响 |
| 可审查结果（PR、完成结论） | 在原始 Thread 发送结果及稳定链接 |
| 无法自动恢复的失败 | 简短失败通知，含 Ticket 链接和下一步 |
| observe 推断候选 | 默认不在外部 Thread 发消息，仅在监管入口记录 |

禁止用大量过程评论、重复确认、失败重试噪声或“AI 正在思考”刷屏替代可操作信息。

## 2. 领域模型与架构

新增 `stableagent/supervisor/`，它是 P6 Router 控制平面；不得将其写入 `orchestration/` 的 Store 实现或 `core/`。

```text
stableagent/supervisor/
  model.py          # Trigger、Thread、Subscription、Decision、SourceCursor、Channel
  source.py         # Source / Channel 协议、GitHubSource、去重与 cursor
  router.py         # Intake、归属、准入、评估和命令
  assessor.py       # CandidateAssessor 协议、确定性/LLM 实现适配
  service.py        # RouterService：唯一 domain mutation 入口
  views.py          # 人工监管的只读查询
  runtime.py        # 生命周期、单实例锁、poll/tick、恢复与停止
```

### 2.1 标准事件模型

| 模型 | 必填语义 |
|---|---|
| `Trigger` | `id`、`source="github"`、`external_id`、`kind`、`actor`、`thread_keys`、`occurred_at`、`received_at`、不可变 `payload`、`digest`。同一外部事件重复接收必须只存一条。 |
| `Thread` | `key`、`source`、`repository`、`kind`、`external_ref`、`last_trigger_at`、`status`。Issue、PR、review conversation 由 GitHubSource 映射为稳定 key。 |
| `Channel` | 可写目的地：Issue comment、PR comment、review reply。它不授予权限，只供已授权 effect 投递。 |
| `Subscription` | `ticket_id`、`thread_key`、`mode`（`own` 或 `context`）、`created_at`、`ended_at?`。同一 Ticket/Thread/mode 幂等。 |
| `Decision` | `id`、`trigger_ids`、`outcome`、`strength`、`reason_codes`、`policy_version`、`mandate_id?`、`created_at`。outcome 为 `accept`、`observe`、`ignore`、`attach`、`stop`、`unavailable`。 |
| `SourceCursor` | Source 实例的读取位置、上次成功读取时间、连续失败数、健康状态。cursor 与 Trigger 写入需要原子推进。 |

Trigger、Decision、Subscription 与 Ticket/Assignment 都必须保存在 P4 Store 扩展的事务中。所有外部 ID 作为唯一键的一部分；不得仅依靠内存去重。

### 2.2 Router 处理流程

```text
GitHubSource
  → Trigger（去重、Thread/Channel 映射、Source cursor）
  → RouterService.receive()
      ├─ 将匹配 Subscription 的 Trigger 作为 TicketInput 扇出
      ├─ 若是已归属 Ticket 的人类后续消息，交给 Ticket scope 路由
      ├─ 否则 Policy gate 判断 explicit / inferred / ignored
      ├─ explicit：Policy 创建/验证 Mandate → accept Ticket
      └─ inferred：CandidateAssessor → observe Decision（不创建可执行 Ticket）
  → Outbox（确认、结果、attention 或失败通知）
  → P4 Scheduler → P5 RepositoryWorkerExecutor
```

关键规则：

1. Subscription **提供上下文，不提供授权**。CI、bot、后续评论可以扇出给活跃 Ticket，但不能扩大 Mandate，也不能因自身自动创建执行任务。
2. 人类在某 Ticket 的 own Thread 中提出的后续请求，由该 Ticket 的 Worker/Router scope 判定；scope 内转为 TicketInput，scope 外创建 `handback`/candidate 记录供 Router 重新评估。
3. 一个 Trigger 可关联多个 Thread，但只拥有一个 primary Thread；路由关系必须显式记录，禁止按文本猜测。
4. 同一 Thread 可以有多个 Ticket；Router 不得以“一个 Thread 只允许一个 Ticket”为简化条件。重复任务由 Decision 的已存在 Ticket 信息和 request digest 判断。
5. 已结束 Ticket 仍保留 own Subscription。allowlist 人的后续明确消息可在 Mandate revalidation 后恢复该 Ticket；被人工 `stop` 的 Ticket 不自动接收或恢复。
6. 每个 Thread 同时最多一个 Eval/assessment；同一 Thread 在评估期间到达的 Trigger 合并为下一个 batch，保证上下文顺序。

### 2.3 Policy 与分级自治

P6 扩展 P5 Policy，但不削弱默认权限：

| 触发强度 | 进入方式 | `off` | `observe`（默认） | `auto` |
|---|---|---|---|---|
| explicit | allowlist 人员在受支持范围明确 @agent | 接受 | 接受 | 接受 |
| inferred | 规则、允许 bot、未 @ 的候选或 CI 结果 | 忽略/记录 | 评估并记录 would-accept | 仅满足低风险规则时可接受 |
| not admitted | 未授权 actor、未知 bot、范围外来源 | 忽略并审计 | 忽略并审计 | 忽略并审计 |

P6 Beta 的发布配置必须锁为 `observe`，并将 `auto` 接口标记为未启用。只有 P7 在完成规则误报率、授权和回滚评估后，才能允许部署显式设置 `auto`。

`CandidateAssessor` 是窄协议，输入为 Trigger batch、Thread 摘要、已有 Ticket/Decision、当前 Policy，输出带结构化 reason code 的候选判断。其失败只能记录 `unavailable` 并允许重新评估，不能默认接受。

### 2.4 运行时和恢复

`SupervisorRuntime` 负责：获取单实例锁、打开 Store、加载 Source cursor、恢复 pending effects、启动 Scheduler、启动 GitHubSource poll/tick、优雅停止。

- 必须通过 SQLite/process lock 防止两个 Router 写同一 state directory。
- Source cursor 推进、Trigger 去重、Thread 更新、Subscription 扇出、Decision/Ticket/effect 创建须在合理的原子 transaction 边界内完成。
- Router 重启后恢复 P4 running Assignment、pending effect 和 Source cursor；不得通过重新扫描全量 GitHub 历史来“猜”缺失状态。
- Source 临时失败使用独立重试和健康计数；连续失败达到阈值后在监管入口显示，但不为每次 poll 外部发消息。
- `pause_processing` 停止新 Eval、Ticket dispatch、外部 reply effect 和超时推进；Source 仍可记录 Trigger。正在执行的 Worker 是否中断必须由显式策略决定，默认不硬杀。

## 3. 实施工作包

### WP-6.1：Supervisor 模型、Store 迁移与只读视图

**文件**：`supervisor/model.py`、P4 `orchestration/store.py` 扩展、`supervisor/views.py`、测试。

1. 定义 Trigger/Thread/Channel/Subscription/Decision/SourceCursor 和枚举；全部以 immutable Pydantic record 表示。
2. 为外部 ID、Source cursor、Subscription、Decision request digest 添加 SQLite 唯一约束和索引。
3. 实现只读 View：`needs_attention`、`open_tickets`、`failed_effects`、`source_health`、`thread_timeline`、`ticket_timeline`。
4. View 显示当前可行动事实和链接，不暴露不需要的内部 ID 或其他 Ticket 的敏感输入。
5. migration 必须从 P5 Store 无损升级；降级路径、备份与 schema version 写入运维文档。

### WP-6.2：GitHubSource、Trigger 去重与 Thread 映射

**文件**：`supervisor/source.py`、`integrations/github.py` 扩展、`tests/unit/test_github_source.py`。

1. 定义 `Source` / `ChannelWriter` 协议，GitHubSource 是首个实现。
2. 支持 Issue、PR、issue comment、review comment、review、workflow/CI 状态的读取和标准化；每个 kind 的 external ID、actor、Thread key 和 Channel 映射必须有 fixture。
3. 支持 webhook 或 polling 的任一主路径；若两者均支持，统一在 Trigger 层去重。文档必须说明部署所需权限和选定路径。
4. 读取 cursor、分页、API 临时错误、403/404、删除事件、乱序和重复事件均有确定性 fake-client 测试。
5. 过滤 agent 自己的 bot identity；不把自身评论再次作为候选或输入。

### WP-6.3：Router Intake、Subscription 与 Decision

**文件**：`supervisor/router.py`、`service.py`、`assessor.py`、测试。

1. `receive(trigger)` 是外部事件进入领域的唯一命令；重复 Trigger 返回 duplicate，不能重新创建 Ticket/effect。
2. 先将 Trigger 扇出到所有匹配 live Subscription；输入按 Thread 时间和 sequence 持久化。
3. 执行 Policy gate：未准入记录 ignore；explicit 创建 Decision、Mandate、Ticket、Assignment/effect；inferred 在 observe 只记录 Decision。
4. 实现 attach、handback、stop、reevaluate、subscribe、unsubscribe 和 resume 命令；命令必须检查 Ticket 状态、Mandate 和 actor 权限。
5. 创建 Ticket 时绑定 origin Thread 为 own Subscription；Worker 创建 PR 后通过 P5 result/report 添加 own PR Subscription；相关 Thread 只能作为 context Subscription。
6. 结束 Ticket、人工 stop、Policy 变更、Subscription 取消的后续消息语义必须有明确记录；不得丢失未读人类 follow-up。

### WP-6.4：常驻调度、恢复与运行控制

**文件**：`supervisor/runtime.py`、P4 `scheduler.py` 适配、测试/集成测试。

1. Runtime 按配置创建 Source、Router、Scheduler、Outbox 和 GitHub client；组成根只注入依赖，不存业务规则。
2. 启动先完成 Store schema/lock/Policy/source setup，再允许 effect 或 worker dispatch。
3. 停止先暂停 intake/effect，等待可控期限，保存 cursor 和 pending 工作；不得删除 worktree、Ticket、Effect 或 checkpoint。
4. 恢复测试至少覆盖：Router 在 Trigger 持久化后崩溃、Decision/Ticket transaction 后崩溃、GitHub comment effect 前崩溃、Worker run 中崩溃、cursor 更新失败。
5. 运行控制仅提供 `pause_processing`、`resume_processing`、`status`、`retry_ticket`、`stop_ticket`、`reevaluate` 等显式命令。

### WP-6.5：最小监管面与注意力协议

**文件**：先实现 `supervisor/views.py` + CLI；可选只读 FastAPI 路由；`tests/unit/test_supervisor_views.py`。

1. `status` 首屏顺序固定：待人工问题/审批 → 失败或不可恢复 effect → ready 等 review 的 Ticket → Source 健康 → 队列/Worker load。
2. 每个待人工项目显示“谁需要做什么、为什么、对哪个 Ticket、允许哪些动作”，并提供稳定 Ticket/Thread 链接。
3. 提供确认、结果、attention、失败消息的消息模板；单条文本限制，防止模型输出长报告淹没协作 Thread。
4. 不制作过程事件瀑布流；完整 Event/Report 历史放在 Ticket detail 查询中。
5. 所有写操作（stop、retry、approval、reevaluate）必须二次确认或明确命令参数，并走已有 service/Policy/Outbox。

### WP-6.6：Beta 演练、数据收集与发布门禁

**文件**：`examples/06_superagent_beta.py`、`docs/design-decisions/06-super-agent-router.md`、`docs/guides/`、README、CHANGELOG。

1. 离线集成场景：多个 Issue/PR/CI Trigger，两个并发 Ticket，一个失败、一个等待人工、一个结束后被评论恢复；断言归属、外部消息数和恢复语义。
2. 受控真实仓库 Beta：单仓库、专用 bot、allowlist、`observe`、无 auto merge、最小 GitHub token。先完成 dry-run/record-only，再允许 explicit 请求创建 PR。
3. 记录 Beta 数据：候选数、would-accept 数、人工接受/拒绝原因、误报/漏报、每 Ticket 通知数、恢复次数、PR 重复数（必须为 0）。
4. ADR-06 记录：Thread/Subscription 语义、Source/Tool 边界、observe 模式、Router 事务边界、cursor/去重、注意力预算和拒绝的设计方案。

## 4. 测试与退出条件

### 必测矩阵

| 范畴 | 必须证明 |
|---|---|
| Trigger | 去重、乱序、分页、cursor、agent 自身事件过滤、Source restart |
| Router | explicit/observe/ignore、重复检测、scope handback、stop、resume、Policy revalidation |
| Subscription | own/context 区分、后续评论扇出、CI 仅提供上下文、结束/停止后的语义 |
| Outbox | 确认/结果/attention/失败消息幂等、按 Thread 保序、恢复不重复 |
| Runtime | 单实例锁、启动/停止、pause、pending work 恢复、Source/Worker 独立失败 |
| Views | 优先级正确、仅显示可行动项、动作经确认和 service 入口 |
| e2e | Fake GitHub + SQLite + P4/P5 worker 的多 Ticket crash recovery |
| Beta | observe 不执行副作用；显式请求模式不自动 merge，且每项外部写入可审计 |

### 阶段验证命令

```bash
.venv/bin/pytest -q tests/unit/test_supervisor_model.py tests/unit/test_github_source.py
.venv/bin/pytest -q tests/unit/test_router.py tests/unit/test_supervisor_runtime.py tests/unit/test_supervisor_views.py
.venv/bin/pytest -q tests/integration/test_superagent_recovery.py tests/integration/test_superagent_observe.py
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
```

### P6 退出清单

- [ ] GitHub Source 将所有支持事件确定性映射为 Trigger/Thread/Channel，并持久化 cursor/去重状态。
- [ ] Router 是 Trigger、Decision、Subscription 与 Ticket 创建的唯一写入口。
- [ ] 所有显式任务经过 P5 Policy/Mandate；推断候选在 `observe` 只记录、不执行。
- [ ] Ticket 后续评论、PR 和 CI 输入正确归属，且不会因此扩大授权。
- [ ] Router、Source、Outbox、Scheduler 和 Worker 的崩溃恢复不丢失或重复语义副作用。
- [ ] 监管入口以待人工事项为首要视图，所有外部消息符合注意力预算。
- [ ] 单仓库 Beta 有 dry-run 记录、明确风险边界、回滚步骤和可审计数据。
- [ ] 所有离线门禁通过；新增 supervisor 核心模块的 CI 覆盖率 ≥90%。

## 5. 阶段交接：P7 的输入

P7 只能在不改写 P6 单仓库语义的前提下扩展：

- 新 Source 必须实现 Trigger/Thread/Channel 协议，不能直接创建 Ticket 或调 Worker。
- 多 Provider、Memory、MCP server、transport 和多租户都观察/适配已有 Ticket、Assignment、Effect、Report、Audit 记录。
- P6 `observe` 数据是 P7 决定是否允许某条低风险规则 `auto` 的唯一依据；没有数据不得开启 auto。
- P6 的 Source cursor、outbox idempotency、Mandate revalidation 和人工注意力协议是不可回退基线。
