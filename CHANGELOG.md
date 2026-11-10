# CHANGELOG

本文件遵循语义化版本；公共契约的版本与冻结规则见 `docs/design-decisions/00-contracts.md`。

## [Unreleased]

### 文档（工程收口口径，2026-11-10）
- 维护者裁定推广：后续**全部**阶段/里程碑/任务的工程收口只看代码完成与离线可靠性；
  真实仓库试跑、observe 标注、生产 canary 等数据回填一律不作 DoD
- 写入 [Roadmap「工程收口口径」](docs/Roadmap.md) 与
  [文档维护规范](docs/documentation-standards.md)；ADR-06 决策 11 之数据条款降级为
  可选运维建议；P7 Spec 开始/退出条件与最终验收改为离线证据优先
- P6 按该口径工程收口，当前主线切至 P7；真实 GitHub HTTP 客户端仍为已知遗留

### 实现（P6 M6 Beta 演练，2026-11-10）
- `stableagent/supervisor/store.py`：`BetaSnapshot` 与 `beta_snapshot()` 只读聚合 11
  字段（triggers/decisions/decision_reasons/would_accept/tickets/recovered_assignments/
  approvals/notification_counts/effects/retried_effects/source_health）；reason_codes
  用 `json_each` 展开，GROUP BY + ORDER BY 保证确定性；不返回任何 payload
- `stableagent/supervisor/views.py`：`BetaReport` 冻结模型与 `beta_report(store)`，
  Beta 数据报告是只读视图而非新命令面
- `examples/06_superagent_beta.py`：离线演练 run/report 两 phase——多 Issue/PR/CI
  Trigger、两个并发 Ticket、FAILED/WAITING/READY→COMPLETED 后评论经 operator resume
  恢复；断言外部评论恰好一次、同 Thread 不重复建票、bot 自事件过滤；report 只读同一
  `beta.db` 输出 `BETA_REPORT <json>`
- 新增 [Beta runbook](docs/guides/supervisor-beta.md)：声明真实 GitHub HTTP 客户端前置
  缺口，单仓库/专用 bot/allowlist/observe/最小 token、record-only→explicit 两阶段、
  人工数据记录表（PR 重复数必须为 0）、回滚与发布门禁；真实 Beta 由维护者执行并回填
- ADR-06 决策 11：离线先行，`observe` 数据是 P7 允许任何规则进入 `auto` 的唯一依据；
  真实 Beta 数据回填前 P6 出口第 7 项保持未勾
- 门禁：全量 `878 passed, 6 skipped`，覆盖 98.54%，`store.py` 100% / `views.py` 95%，
  FOCUS 仍 35 文件，3.10/3.11/3.12 一致；L1 与 `orchestration/service.py` 零 diff

### 实现（P6 M5 Console，2026-11-09）
- `stableagent/supervisor/views.py`：注意力排序固定为 approval 0 → failed_effect 1 →
  waiting_ticket 2 → ready_ticket 3；`StatusSections` 汇总队列、source_health 与
  AttentionItem；`TicketDetail` 带 mandate/assignment/subscription/effects/reports 与
  时间线（`TimelineEvent.object_id`）
- `stableagent/supervisor/notifications.py`：`NotificationKind`（accepted/result/
  attention/failure）与固定模板；正文只取 Ticket 标题、对应 Report `summary` 和稳定
  评论 URL，`max_chars` 截断补 `…`；`NotificationDrafter.draft_due` 只对
  `origin_kind="github"` 且 mandate 含 `comment_issue` 的 Ticket 草拟，按 digest 去重，
  拒绝后该 Ticket 永久静默
- `stableagent/control/approval.py`、`stableagent/supervisor/service.py`：批准与 effect
  入队同事务；stop 在同一事务撤销待批并取消 Assignment；`retry_effect` 把 FAILED 重置
  为 PENDING 且沿用原 idempotency key（at-least-once）
- `stableagent/supervisor/runtime.py`：每轮顺序为心跳 → poll → 暂停检查 → 路由 →
  `Scheduler.tick` → 草拟 → 外部派发；暂停轮不草拟；外部恢复先补路由再草拟；
  `read_runtime_status` 供只读视图复用
- `stableagent/supervisor/console.py`、`stableagent/supervisor/cli.py`：stdlib argparse
  控制台，`status`/`ticket`/`thread` 只读，写命令一律要求 `--confirm <target-id>`；
  退出码 0 成功、1 未知或状态不符、2 确认缺失/不匹配、3 草稿已变、4 非 operator；
  不读凭据、不发起网络
- 新增 [监管控制台指南](docs/guides/supervisor-console.md)
- CI 心脏覆盖率闸门 33→35（新增 `supervisor/notifications.py`、`supervisor/console.py`）；
  全量 `874 passed, 6 skipped`，覆盖 98.53%，两个新文件均 96%，3.10/3.11/3.12 一致；
  L1 与 `orchestration/service.py` 零 diff

### 实现（P6 M4 Runtime，2026-11-09）
- `stableagent/supervisor/store.py`：schema v2 新增 `runtime_lock` 与 `runtime_control`
  单例行；锁获取/心跳/释放、暂停位与递增 generation、未路由 Trigger 查询与队列计数
  （均在单个 `BEGIN IMMEDIATE` 事务内）
- `stableagent/supervisor/runtime.py`：`Runtime.start/step/status/serve/stop`——启动固定为
  锁 → `recover()` → 读暂停 → 补路由；每轮为心跳 → poll → 路由 → `Scheduler.tick` →
  外部 `EffectDispatcher.dispatch_due`；`SourceError` 只空转本轮；`RuntimeController`
  提供 status/pause/resume/retry/stop_ticket/reevaluate，全部校验 operator
- `stableagent/orchestration/scheduler.py`：`wait_idle()` 只等待进行中的 Worker，宽限期
  到点返回 False，绝不取消任务
- 暂停跨重启保留；恢复时立即补路由暂停期间记录的 Trigger；优雅停止不删除 worktree、
  Ticket、Effect、Input、Report 或 checkpoint
- 新增 [Runtime 运维指南](docs/guides/supervisor-runtime.md)：单实例锁、启动顺序、每轮
  推进、暂停语义、优雅停止、五个崩溃恢复窗口与命令安全边界
- 五个崩溃窗口集成证明（`tests/integration/test_superagent_recovery.py` 5 passed）：
  Trigger 未路由、Ticket 已提交未派发、comment effect 不丢不重放、Worker 中断走 recover、
  cursor 失败整批回滚
- CI 心脏覆盖率闸门 32→33（新增 `supervisor/runtime.py`）；全量 `833 passed, 6 skipped`，
  覆盖 98.80%，`runtime.py` 99%，3.10/3.11/3.12 一致；L1 与 `orchestration/service.py`
  零 diff

### 实现（P6 M3 Router Intake，2026-11-09）
- `stableagent/supervisor/model.py`：新增 `AutonomyMode`（`off`/`observe`/`auto`）；
  `auto` 在 `RouterService` 构造期拒绝（P6 Beta 锁定 `observe`）
- `stableagent/supervisor/assessor.py`：`CandidateAssessor` 窄协议与确定性
  `ObserveOnlyAssessor`（ci_context_only / thread_not_open / existing_ticket_context /
  already_owned / context_only / inferred_candidate），只评估不执行
- `stableagent/supervisor/router.py`：`RouterService.receive` 成为外部事件唯一入口——
  按既有 Decision 去重、live Subscription 扇出（每 Ticket 一条 Input + deliver effect）、
  explicit 五件原子写（Mandate/Ticket/Input/Assignment/launch effect + own Subscription +
  ACCEPT Decision）、六种 outcome（accept/observe/ignore/attach/stop/unavailable）；
  `reevaluate` 追加递增 attempt 且不重复扇出；评估器异常只记 `unavailable`
- `stableagent/supervisor/service.py`：运维命令层 attach/subscribe/unsubscribe/stop/
  handback/resume/record_pull_request；全部校验 operator（大小写不敏感）；stop 保留 own
  护栏；resume 仅 COMPLETED/FAILED，要求显式准入与 `revalidate(when="launch")` 并写审计；
  PR 仅在 Worker 报告的 `pull_number` 上由 context 升 own
- `stableagent/supervisor/source.py`：带正文事件补 `payload["request"]`（text/mentioned/
  url），mention 大小写不敏感；`SupervisorStore` 新增 `next_decision_attempt` 与
  `find_accepted_decision`
- 新增 [Router 运维指南](docs/guides/supervisor-router.md)：唯一入口、去重顺序、三档自治、
  outcome 表、命令权限、订阅生命周期与安全边界
- CI 心脏覆盖率闸门 29→32（新增 assessor/router/service）；三文件覆盖率
  `100.00%` / `97.45%` / `97.09%`；全量 `804 passed, 6 skipped`，3.10/3.11/3.12 一致

### 实现（P6 M2 GitHub Source，2026-11-09）
- `stableagent/supervisor/source.py`：`Source` / `ChannelWriter` 协议、`SourceEvent` /
  `PollBatch` 记录与 `source.transient` / `auth` / `gone` / `schema` 四分类；`GitHubSource`
  把 Issue、PR、issue comment、review comment、review、CI status 标准化为 Thread 与
  Trigger，稳定 id 由 sha256 派生，bot 身份大小写无关过滤
- `GitHubSource.poll`：cursor 分页（`max_pages` 默认 10，未读完的 cursor 持久化）、
  批内与库内 `UNIQUE(source, external_id)` 双重去重、按 `(occurred_at, external_id)`
  排序、删除事件落 `closed`；Trigger/Thread/Channel/cursor 在同一事务写，失败整批回滚
- `integrations/github.py`：只读 `GitHubReadKind` / `GitHubReadItem` / `GitHubReadPage` /
  `GitHubReadClient` 协议与确定性 `FakeGitHubReadClient`；integrations 不 import supervisor
- 错误健康：`source.transient` 记 `degraded`，其余记 `failed`，均在抛错前写
  `source_cursors`；监管视图的 `source_health` 可直接看到
- 新增 [GitHub Source 部署与 cursor 契约](docs/guides/supervisor-github-source.md)：
  只读权限、polling 主路径、事件映射、去重、错误健康与 webhook 后续接法
- CI 心脏覆盖率闸门由 28 扩到 29 文件（新增 `supervisor/source.py`）
- 测试：`749 passed, 6 skipped, 0 failed`、覆盖率 98.89%；`supervisor/source.py`
  行覆盖率 96.27%；Python 3.10.20 / 3.11.16 / 3.12.14 一致；`stableagent/core/` 与
  `orchestration/service.py` 对 `234f620` 均 0 行 diff
- 状态口径：P6 M2 完成 8/8（里程碑 2/6），P6 仍为 Active，**不宣布阶段交付**

### 实现（P6 M1 基础层，2026-11-08）
- `stableagent/supervisor/`：`model.py` 的 `Trigger`/`Thread`/`Channel`/`Subscription`/`Decision`/
  `SourceCursor` 六个不可变记录、canonical JSON 与 payload digest；`store.py` 的
  `SupervisorStore(Store)` 复用 P4 连接与 `BEGIN IMMEDIATE`，追加 triggers/threads/channels/
  subscriptions/decisions/decision_triggers/source_cursors 七张表与 `p6-supervisor-v1` marker；
  `views.py` 六个只读视图（`needs_attention` 按审批→失败 effect→waiting→ready 排序，
  timeline 不返回 payload）
- Trigger 以 `UNIQUE(source, external_id)` 去重；live Subscription 用部分唯一索引；
  Decision 以 `UNIQUE(request_digest, attempt)` 允许 `unavailable` 重评估
- CI 心脏覆盖率闸门由 25 扩到 28 文件（新增 `supervisor/model.py`、`store.py`、`views.py`）
- 测试：`715 passed, 6 skipped, 0 failed`、覆盖率 98.99%；三个 supervisor 文件
  98.25% / 98.23% / 98.59%；Python 3.10.20 / 3.11.16 / 3.12.14 一致；
  `stableagent/core/` 与 `orchestration/service.py` 对 `234f620` 均 0 行 diff
- 状态口径：P6 M1 完成 8/8（里程碑 1/6），P6 仍为 Active，**不宣布阶段交付**

### 规划与决策（P6 启动，2026-11-08）
- 新增 [ADR-06](docs/design-decisions/06-super-agent-router.md)（Accepted）：`supervisor` 单向依赖、
  `SupervisorStore` 子类同事务、Trigger 不可变与数据库去重、Thread/Channel 稳定映射且 Channel
  不授予权限、Subscription 的 context 不扩大 Mandate、Decision 六 outcome / 三 strength 与
  observe 默认、单次 `receive()` 原子边界、Source 只产 Trigger、注意力预算、无损迁移与降级
- 新建 [P6 验收账本](docs/acceptance/p6-supervisor-beta.md)：出口不变量 I1~I6、M1~M6 里程碑与
  M1 的 8 Task / 47 Step TDD 计划
- 新增 [supervisor 迁移指南](docs/guides/supervisor-migration.md)：P5→P6 备份、marker 校验与
  P6→P5 降级步骤
- `README.md`、`docs/README.md`、`docs/acceptance/README.md`、`docs/Roadmap.md` 的阶段状态同步

### 实现（P5 受控执行与仓库集成，2026-11-08 远端 CI 双绿收口）
- `stableagent/control/`：`StaticPolicy` 四道受理门与 Mandate 构造、审批服务/审批门/守卫式取消、
  `AuditWriter` 四类审计行；`stableagent/execution/`：worktree 创建/复用/清理、命名收口、
  命令边界（argv allowlist、cwd 收口、shell 语法拒绝、输出预算）与 Docker 档参数契约；
  `stableagent/integrations/`：GitHub 显式请求入口（五件同事务原子写）、`RepositoryWorkerExecutor`
  六时机复检、三个 GitHub 写 effect handler、GitHub 错误三分类、MCP stdio transport/session/
  tool adapter 与 `mcp_call` 调用守卫
- M3 分支已本地快进 `main`（`f41ea35..76b8d95`，其后随 M4 出口一并推送）：M4 的 4.1~4.6 与
  Task 10 本地出口于 2026-11-07 本地收口：`examples/05_guarded_github_task.py` 的
  run/reject 双 phase、五类结构化拒绝、CI 心脏闸门 18→25 文件、Python 3.10/3.11/3.12 等价矩阵、
  ADR-05 实测证据与七条安全声明、README/Roadmap/文档索引与规格退出清单同步；L1~L8、A4 与 B2
  的出口复跑一并回填
- 补账本 2.6：`EffectKind` 加 `RUN_COMMAND`，`execution/effects.py` 的 `run_command_handler`
  经 outbox 执行受限命令并把 `CommandResult.summary()` 与结构化字段写进 Assignment Report
  （拒绝 BLOCKED、其余 PROGRESS；stdout/stderr 与凭据不进 Report）；示例 05 的 run phase 三条
  git 命令改走 effect
- 测试：M4 出口读数 `677 passed, 6 skipped, 0 failed`、覆盖率 99.04%；补 2.6 后全仓
  `689 passed, 6 skipped, 0 failed`、覆盖率 99.05%；25 个心脏文件逐个 ≥90%
  （最低 `core/loop.py` 96.8%）；三套解释器（3.10.20 / 3.11.16 / 3.12.14）同为
  `689 passed, 6 skipped`
- 状态口径：2026-11-08 维护者授权后已推送 `main`（`f41ea35..d700ffb`），远端 CI run
  `42427823663` 的 `quality (3.10)` / `quality (3.12)` 双 job `success`，H3/4.7 关闭；
  2.6 补齐后 DoD 46/46、里程碑 40/40。收口提交 `380e440` 的远端 CI run `42433929377`
  在 3.10/3.12 双 job 全绿，**P5 正式收口**

### 规划与决策（P5 启动，2026-11-03）
- 新增 [ADR-05](docs/design-decisions/05-guarded-execution.md)（Accepted）：P5 的 10 项决策——
  `control/execution/integrations` 三新包与依赖方向矩阵（四旧包禁止 import 新包）、Mandate 在
  P4 原表兼容加 10 列（`p4-local-v1` 旧行不回写）、审批与审计经 `schema_migrations`
  `p5-control-v1` 同库两新表、Policy 判定在执行层 `RepositoryWorkerExecutor`+`PolicyToolFilter`
  （Core 零 diff，`permission_hook` 明示为非安全字段）、EffectKind 加 4 类外部写而
  merge/auto-merge 结构性不进枚举、worktree 命名/复用四要素/环境白名单硬口径、
  `LocalRestrictedRunner` 文案禁用 "sandbox"（Docker 为唯一安全边界且测试双条件跳过）、
  MCP stdio 先行且发现≠授权、GitHub 仅显式 API/CLI 请求且幂等键=外部稳定 id+digest、
  `CONTRACT_VERSION` 保持 `1.1.0`，并记录 8 项被否决方案
- 新建 [P5 验收账本](docs/acceptance/p5-guarded-execution.md)：心脏边界 L1~L8 + 分组 A~H 共
  **46 项 DoD**，四里程碑 40 步（M1 授权 9 / M2 隔离执行 12 / M3 MCP+GitHub 12 / M4 收口 7），
  当前 **0/46、0/40**，状态 Active；任务级 TDD 计划沿用 P4 约定按里程碑追加（M1 开工时建第 5 节）
- `docs/README.md`、`docs/Roadmap.md`、`docs/acceptance/README.md`、根 README 的阶段表同步：
  P4 行为已完成收口、P5 行为 Active；**P5 能力在 M4 收口前不宣布交付**

### 实现（P4 编排内核落地并收口，2026-11-03；远端 run 41596839282 双绿）
- `stableagent/orchestration/` 全量落地：`Ticket`/`Mandate`/`Assignment`/`TicketInput`/`Report`/
  `Dependency`/`Effect` 记录与三个纯转换状态机；stdlib `sqlite3` Store（8 张表 + `schema_migrations`）；
  Durable Outbox（`launch_assignment`/`deliver_input`/`cancel_assignment` 三类 effect，唯一键幂等、
  注入式 backoff、孤儿回收）；`Scheduler`（信号量并发、DAG `block`/`continue`、三维优先级、取消、
  恢复、外层 deadline）；`AgentExecutor`（Event→Report 映射、`stream_recover` 续跑、驻留投递）；
  `OrchestrationService` 十命令与稳定 `OrchestrationError.code`
- 唯一被批准的 Core 兼容扩展已实现：`Agent.resume()/aresume(extra_input=None)` 在暂停边界追加
  user 消息；`core/loop.py` 相对 `46441f0` 零 diff，`extra_input=None` 默认路径与 P2 事件流逐字节相等
  （L4 金标准复验通过）
- 新增 `examples/04_ticket_orchestration.py`：父/子 Ticket + 依赖 + 两个脚本 Worker 的离线时间线，
  `--phase selftest` 自带 SIGKILL→`recover()` 闭环；`benchmarks/orchestration_benchmark.py` 记录
  50 Ticket / 100 Assignment 固定规模的 `tick`/`recover`/`drain` 耗时（证据见 `docs/evidence/`）
- 测试：编排层单测 128 项（model 25 / store 19 / outbox 9 / executor 16 / scheduler 32 / service 27）、
  边界与 CI/基准 15 项、集成 3 项；全仓 434 项（433 passed, 1 skipped），覆盖率 99.02%；
  CI 心脏闸门从 12 扩到 18 文件（新增 orchestration 六模块，各 ≥90%），
  Python 3.10/3.11/3.12 本地等价验证通过
- 公开契约口径：`ORCHESTRATION_SCHEMA_VERSION = "0.1.0"` 只在编排包内定义，顶层
  `stableagent/__init__.py` 不重导出 Ticket/Assignment/Report——**P6 Beta 之前不承诺编排层记录结构
  稳定**，`CONTRACT_VERSION` 仍为 `1.1.0`（Core 公共 payload 未变）

### 规划与决策（P4 启动，2026-11-02）
- 新增 [ADR-04](docs/design-decisions/04-ticket-orchestration.md)（Accepted）：P4 的驱动模型（单一 asyncio
  事件循环 + `asyncio.Semaphore`）、事务不变式（`Store.transaction()` 内禁止 `await`，提交前不得启动
  Worker）、恢复所有权（`_tasks` 只作取消句柄，单个 `worker_run_id` 同时最多一个驱动者）、
  Event→Report 映射的默认噪音口径（`progress` 默认只在 `TOOL_RESULT` 落一条）、编排层独立版本
  `ORCHESTRATION_SCHEMA_VERSION = "0.1.0"` 且不进顶层重导出（`CONTRACT_VERSION` 仍为 `1.1.0`），
  并记录 10 项被否决方案
- 唯一被批准的 Core 兼容扩展：`Agent.resume()/aresume()` 增加 `extra_input: str | None = None`，用于在
  暂停边界投递 `TicketInput`。`core/loop.py` 仍保持零 diff，默认 `None` 时要求 L4 金标准重新 dump
  逐字节相等。（2026-11-03 已实现并通过 L4 复验，见上）
- 新建 [P4 验收账本](docs/acceptance/p4-ticket-orchestration.md)：按维护者确认，实施计划（M1 走骨架 /
  M2 宽度 / M3 收口共 30 个步骤与验证命令）与 DoD（心脏边界 L1~L8 + 分组 A~H，共 60 项，含
  里程碑归属表）同处一文件。2026-11-03 收口：DoD **60/60**、里程碑 **30/30**，
  远端 CI run 41596839282 在 Python 3.10 / 3.12 双绿
- 处置 [ADR-02 后果 4](docs/design-decisions/02-checkpoint.md) 交给"P4 预算治理"的 deadline 遗留项：
  外层 Assignment deadline（超时 → Ticket `blocked`，实现为 `expire_assignment()` 直接落态，
  见 ADR-04）归 P4（账本 E9）；
  内层 Provider 单次调用硬超时因需要改 Core 调用点、与 P4 的"唯一 Core 扩展"约束冲突，改归
  P7 [WP-7.2](docs/specs/p7-production-hardening-ecosystem.md)。ADR-02 原文只追加日期说明、不重写结论；
  [P4 规格](docs/specs/p4-ticket-orchestration-kernel.md) 与
  [P7 规格](docs/specs/p7-production-hardening-ecosystem.md) 同步该归属
- [P4 规格](docs/specs/p4-ticket-orchestration-kernel.md) 增加第 6 节"决策补充（2026-11-02，ADR-04）"，
  以追加方式消解八处留白（驱动模型、事务不变式、暂停中投递输入的实现口径、公开契约与包名、
  调度优先级、deadline 分层归属、`worker_run_id` 注册时机、错误基类不继承 Core 的 `UserError`
  ——Core 全库无此类，编排层改用 `OrchestrationError(ValueError)` 加稳定 `.code`），
  不改写原文要求；首屏状态标签改为 Active
- [P4 验收账本](docs/acceptance/p4-ticket-orchestration.md) 追加第 6 节"M1 任务级实施计划"：
  10 个 Task / 50 个 TDD Step，每个 Task 写明精确文件、测试名、红绿命令与提交信息，并给出
  Task ↔ 里程碑步骤 ↔ DoD 三项归属对照（第 1.4 节 M1 的 30 项在任务级全部有落点）。
  M2/M3 的同粒度计划在各自里程碑开工前追加为第 7、8 节。M1 自 Task 1（包骨架 + 稳定错误码 +
  依赖方向边界测试）起在分支 `feat/p4-m1-skeleton` 上推进，M2/M3 在 `feat/p4-m2-width` 上完成，
  逐项证据写进账本对应条目；**P4 能力在远端 CI 收口前不宣布交付**
- `docs/README.md`、`docs/Roadmap.md`、`docs/acceptance/README.md`、根 README 阶段表的 P4 行在
  2026-11-03 同步为已收口（远端 run 41596839282 双绿）

### 契约扩展（P3 已收口，run 41384598912；全部非破坏性，`CONTRACT_VERSION` 仍为 1.1.0）
- `ContextConfig` 新增 5 个带默认值字段：`keep_recent_messages=8` / `max_summary_chars=1200` /
  `tool_result_excerpt_chars=200` / `max_visible_tools=8`；`on_budget_exceeded` 的
  `compress` / `degrade` / `reject` 三个取值从"声明未实现"变为真实语义（见下方引擎条目）。
  既有 `Config()` 的 `compression="none"` + `capability_filter="static"` +
  `max_context_tokens=None` 组合下这些字段不参与任何代码路径
- **`AgentState` 未新增字段**：本阶段一度加过 `context_summary: str = ""`，D 节收口时删除。
  摘要的唯一载体是 `messages` 里那条 `role="system"` + `name="context_summary"` 标记消息，
  再加一个字段就是会与它漂移的第二份事实源；好处是旧快照兼容面更小（无字段可补）
- 未新增 `EventType` 成员：上下文决策走只读属性（`agent.context_reports`），
  沿用 P2"静默落盘 + 只读属性"的先例，也避免动终止事件序列（F4）
- `ContextConfig` 的四个既有占位项（`assembly` / `compression` / `capability_filter` /
  `max_context_tokens`）现在都有消费方；`ControlConfig.max_total_tokens` 仍为声明未实现
  （账单口径累计上限，属后续成本控制阶段，与 P3 的单次请求口径不同）
- 新模块 `stableagent/context/{tokens,compression,capability}.py`：三张注册表
  （`ASSEMBLERS` / `COMPRESSIONS` / `CAPABILITY_FILTERS`）+ `resolve_*` 工厂，
  与 P1 的组装位点同构；未知名一律 `KeyError` 带出已注册清单
- `resolve_assembler(config, summariser=None)`：新增第二个可选参数（位置参数量不变，
  旧的单参调用形状完全兼容）。`compression != "none"` 时返回 `CompressedAssembler` 包装，
  `== "none"` 时返回的对象与类型都与 P2 一致
- 新模块 `stableagent/context/budget.py`：`ContextReport`（每步一条：`step` / `action` /
  `limit` / `before` / `after` / `levels` / `levels_tried` / `tools_total` / `tools_kept` /
  `tools_dropped_tokens` / `orphans` / `within_budget` / `exhausted` /
  `estimated_prompt_tokens` / `observed_prompt_tokens`）+ `BudgetDecision`（入场判定）+
  `count_orphan_tool_calls`（"没拆散 `tool_call`/`tool_result` 配对"的自检，E4）
- 新模块 `stableagent/context/engine.py`：`ContextEngine`（压缩 → 能力视图 → 预算判定的组合，
  `plan` / `estimate` / `fold` / `assemble` / `select` / `preflight` / `observe`）+
  `Plan(keep_recent, max_visible_tools)`（frozen，可当适配器缓存的键）+
  `EngineAssembler`（把引擎的组装能力包成 loop 认识的适配器，L1 因此零改 loop）
- `Agent.__init__` 新增两个可选关键字注入项 `token_counter=` / `summariser=`（缺省用内置启发式
  计数与离线抽取式摘要；位置参数量与旧调用形状不变）
- `Agent` 新增只读属性 `context_reports`（返回副本，外部改不动引擎的事实源）；
  `capability_report` 改由引擎提供，**未启用动态视图时为 `None`**；构造时若显式传入
  `tool_selector`，引擎的配置副本强制 `capability_filter="static"` 且 `capability_report`
  为 `None`——显式选择器优先，不报一份"其实没生效"的结果
- **移除** `Agent` 内部的第二条能力视图通路（C 节留下的 `as_tool_selector` 装配路径与
  `Agent._capability`）：D 节把动态视图收进引擎后它已无调用方，覆盖率报告指为死代码。
  `stableagent.context.capability.as_tool_selector` 仍是公开 API，未删除
- 运行时依赖仍为 3 个（`pydantic` / `anyio` / `httpx`）：token 估算为内置启发式，
  未引入 `tiktoken`

### Added（P3 D 节：预算治理）
- `ContextEngine` 一个实例同时服务 loop 位点与 Agent 外层：外层 `preflight()` 判的量与
  位点实际发出的量必然一致（`test_d1_*` 直接比对象身份），杜绝"外层以为没超、发出去爆了"
- 预算判据 `max_context_tokens` 钉死为"将要发出的组装后消息 + 实际注入的工具规格"
  （不是历史全长），边界语义为"等于上限不触发动作"
- 阶梯（`_ladder()`，`KEEP_FLOOR=1` / `TOOLS_FLOOR=1` / `LADDER_STEP=2`）：
  `compress` 先按 B3 顺序收缩历史再动工具视图，`degrade` 先加严能力视图再压历史，
  `reject` 在入场直接拒且**不 escalate、不动计划**；`degrade` 之后才升级，
  阶梯顺序与长度都有测试逐档写出
- **摘要进快照**（L5）：`ContextEngine.fold(state)` 在每段入口（`_pipeline` 开头）折叠一次并
  写回 `messages`，崩溃恢复不需要重算——`test_l5_recovered_stream_with_compression_is_event_identical`
  除逐条比对事件流外还断言恢复后首次请求的上下文与不中断那次完全相同
- 运行中压到地板仍超限 → 走 F3 的步边界暂停（`reason="budget_exceeded"`），语义与 P2 的
  `needs_human` 同形；恢复进同样紧的预算则直接 `FAILED`，不再重复暂停（防"暂停↔恢复"循环）；
  `_pipeline` 的 `finally` 复位 `pause_requested`，避免跨任务泄漏
- A4 事后校准：`ContextEngine.observe(state)` 把 Provider 回报的 `prompt_tokens` **增量**
  与估算成对记进报告（按 `step` 去重、段入口重置基线）；**只报告不回灌**，
  校准不参与任何决策（Provider 谎报 usage 时决策序列逐条不变）
- L4 跨版本等价证据：`tests/l4_default_stream_dump.py` +
  `tests/fixtures/l4_p2_default_stream.json`（P2 收口提交 `46441f0` 工作树产出的金标准：
  事件流 / 每次发给 Provider 的 messages+tools / 终态快照 / `CONTRACT_VERSION` /
  `EventType` 全集），`test_p3_default_config_stream_identical_to_p2` 断言当前代码逐字节复现
- 测试 +45（合计 265 passed, 1 skipped）：`test_context_engine.py` 42 条（含 E4 的阶梯孤儿扫描
  4 条）+ L4 跨版本等价 2 条 + B7 拆分 1 条；`stableagent/context/` 六个模块全部 100%，
  全库 98.84%；`core/loop.py` 相对 P2 收口提交零修改

### Added（P3 E/F：Evidence 与可复现证据）
- `stableagent/tools/evidence.py`：`Evidence` 的 canonical 定义；`tools/registry.py` 和顶层包
  保留同一个类对象的重导出，旧导入路径不变。P3 保持 Evidence 既有序列化字段，不新增默认 trace 字段，
  以维持 P2 默认 `tool_result` 事件金标准；`step` / `origin` / `parent_call_id` 延后至 P4 的
  Ticket/Assignment 领域记录。模型强制 `ok == (error is None)`；失败缺少错误说明时补齐
  确定性错误文本，避免 tool message 退化成字符串 `"None"`
- `benchmarks/context_compression_benchmark.py`：固定离线语料输出压缩 token/事实保留/tool pair、
  独立 reference Provider usage 校准、200+ 工具 resident 护栏和第 3 步 SQLite checkpoint 恢复影响
- `docs/evidence/p3-context-benchmark-2026-11-02.md`：记录命令、环境、固定变量与本机结果；
  `hybrid` 在该语料中节省 51.16% 估算 token 且保留全部事实，200 工具动态视图节省 98.59% 工具 token，
  summary 恢复段的最新 snapshot payload 从 4,056 降至 2,594 字节。reference usage 是离线模拟口径，
  不作为真实模型 tokenizer 精度声明
- `tests/unit/test_evidence.py` 与 `tests/unit/test_context_benchmark.py`：覆盖 Evidence 兼容/不变式/追溯字段，
  以及 benchmark 的质量、校准、resident 护栏和 checkpoint 输出形状

### P3 收口
- DoD **52/52**：最后一项 G6（CI 3.10/3.12 矩阵 + 心脏闸门）由 run 41384598912 关闭（head SHA `707dff2`），
  两个 job 均为 `283 passed, 1 skipped` / 覆盖率 98.99% / 12 个 FOCUS 文件 96.8–100%，
  与推送前的本地预跑数值逐条一致；证据记在 `docs/acceptance/p3-context-engineering.md`
  的 G6 条目与“远端 CI 收口记录”
- 文档按职责重组（不改任何运行时代码语义）：Roadmap 只留范围与退出条件摘要，P3–P7 实施合同进
  `docs/specs/`，各阶段 DoD 与实测输出进 `docs/acceptance/`，benchmark 口径进 `docs/evidence/`，
  原始构想进 `docs/archive/`；新增 `docs/documentation-standards.md` 定义状态标签与冲突裁决优先级。
  `docs/p1-dod-checklist.md` 保留为跳转存根，因为冻结的 `core/loop.py:12` 仍引用该路径
- CI 心脏模块 FOCUS 从 5 个文件扩到 12 个：新增 `context/{assembly,tokens,compression,capability,budget,engine}.py`
  与 `tools/evidence.py`，每文件单独要求 ≥90% 行覆盖

### P2 收口
- DoD **34/34**：最后一项 F5（CI 3.10/3.12 矩阵全绿）由 run 41032998523 关闭，
  两个 job 均为 `126 passed, 1 skipped` / 覆盖率 98.20% / 心脏模块 96.8–100%，
  与本地预跑数值逐条一致；证据记在 `docs/acceptance/p2-checkpoint-recovery.md` 的 F5 条目与复核记录
- 崩溃恢复用例的击杀时序竞态修复（详见 `02-checkpoint.md` 后果 5）：只在编排侧改步数，
  运行时代码零改动

### 许可与打包
- 采用 **Apache License 2.0**：新增 `LICENSE`（apache.org 原文，未修改），README 增补 License 段
  与 badge；此前仓库无许可证文件
- `pyproject.toml` 按 PEP 639 声明 `license = "Apache-2.0"` + `license-files = ["LICENSE"]`，
  并补齐 Python 3.10/3.11/3.12 等分类符；已验证 wheel 内含
  `stableagent-0.1.0.dist-info/licenses/LICENSE` 且 `License-Expression: Apache-2.0`
- 未加 `py.typed`（`Typing :: Typed` 分类符同样未声明）：留待 P6 mypy strict 收口时一并处理

### 契约扩展 v1.1.0（P2，全部非破坏性，`CONTRACT_VERSION` 不抬版本）
- `EventType.RETRY` 新增成员（兼容扩展）：失败自愈的重试通知；消费方按字符串解析
- `CheckpointConfig` 新增 7 个带默认值字段（旧 `Config()` 构造行为完全不变）：
  - `sqlite_path` / `redis_key_prefix` / `history_limit`：后端参数
  - `retry_max_attempts` / `retry_base_delay_seconds` / `retry_backoff_factor` /
    `retry_max_delay_seconds`：指数退避预算
- `Agent.__init__` 新增关键字参数 `redis_client`（注入项，缺省 `None`）；
  `checkpoint_store` / `fallback` / `sleep` 亦为 P2 新增的关键字注入项
- `OpenAIProvider.from_config(ProviderConfig, ...)`：新增构造入口，把
  `request_timeout_seconds` / `api_base` / `model` 贯通到 httpx 客户端（验收 C5）
- **未动**：`Event` / `Message` / `AgentState` / `Provider` / `Tool` / `Evidence`
  的既有字段与不变式；`core/loop.py` 零修改（见 `docs/acceptance/p2-checkpoint-recovery.md` F1 的 diff 证明）

### Added（P2）
- `core/checkpoint/storage.py`：`CheckpointStore` 统一异步协议（`save/load/records/delete`）
  + `MemoryCheckpointStore` / `SqliteCheckpointStore` / `RedisCheckpointStore`
  （Redis 客户端为注入项，本包不硬依赖 redis 库）+ `build_store` 按配置选后端
- `core/checkpoint/manager.py`：`CheckpointManager`——步边界触发判据、同 run 写锁、
  `(run_id, step)` 幂等覆盖写、历史裁剪；存储故障降级为计数不打断运行（验收 B5）
- `core/checkpoint/recovery.py`：`MIGRATIONS` 版本迁移链 + `SnapshotVersionError`
  （高于/未知版本显式报错）+ `RetryPolicy` 指数退避（`sleep` 可注入）+
  `RecoveryDirector` 重试/转人工/认失败决策 + `repair_for_retry` / `pause_for_human`
- `core/errors.py`：`ErrorClass` 三分类与 `classify_error` / `classify_error_detail`
  （判据优先级：状态码 > 类型名 > 消息特征 > 默认不可重试）
- `Agent` 可靠性外层：`checkpoint`/`retry` 事件注入（终止态静默落盘以保 F4）、
  `recover/arecover/stream_recover`、`checkpoint_history`、`last_state`/`last_checkpoint`
- `examples/03_checkpoint_resume.py`：kill → 重启 → 续跑 的离线确定性演示（含 `--phase selftest`）
- `docs/design-decisions/02-checkpoint.md`：ADR-02（一致性/并发/迁移/后端取舍/重试边界）
- 测试 +77（合计 126 passed, 1 skipped）：`test_crash_recovery.py`（subprocess + SIGKILL
  的进程级崩溃恢复）、三种后端共享一致性断言、版本迁移、退避序列、失败自愈各分类路径、
  D4 恢复流与不中断流逐条等价；全库覆盖率 98%，`core/checkpoint/` 99–100%

### Fixed（P2）
- `build_store`：`backend="redis"` 时 `CheckpointConfig.redis_key_prefix` 此前是死配置
  （只有直接构造 `RedisCheckpointStore` 才生效）；改经 `redis_client` 注入路径统一读取
- `examples/03_checkpoint_resume.py` + `test_crash_recovery.py`：3 步设计的击杀点与终止事件
  在同一次注入里到达，外部 `SIGKILL` 与子进程自然退出竞速，Python 3.10 预跑 CI 矩阵时偶发
  "kill 没杀死"。演示改为 4 步（击杀点之后保留一整轮 provider 往返），断言按 `TOTAL_STEPS`
  推导；可击杀窗口从十几毫秒放大到 `delay` 的线性量级（ADR-02 后果 5）。**框架代码零改动**，
  这是编排侧的时序设计问题，不是恢复语义问题

### 契约修订 v1.0.0 -> v1.1.0（P1，全部非破坏性）
- `EventType` 新增成员 `paused`（兼容性扩展，消费方按字符串解析）
- `Message` 新增字段 `tool_calls: list[ToolCall] = []`
- `AgentState` 新增字段 `usage: dict[str, int] = {}`
- `ControlConfig` 新增字段 `max_steps: int = 10`
- `ToolCall` 定义移入 `core/state.py`（解除 Message 循环引用），
  `providers/base.py` 保留重导出，**旧导入路径不变**
- P1 语义决策 F1~F5（详见 `docs/acceptance/p1-core-runtime.md` 第 0 节）：
  advance 纯转移接口 / max_steps→error+FAILED / pause 步边界生效、非法态 no-op /
  终止事件恰一且居尾 / 暂停表达=新枚举成员 PAUSED

### Added（P1）
- `core/loop.py`：AgentLoop——`advance(state) -> (state, events)` 纯转移 +
  `drive()` 事件引擎（可快照、可重入，P2/P3 接入点已按 DoD 预留）
- `context/assembly.py`：上下文组装位点（默认 passthrough，注册表式扩展）
- `providers/openai.py`：sync / async / SSE 三形态 + `ToolCallAssembler`
  流式 function calling 重组
- `Agent.run/arun/stream` + `pause()/resume()` 最小实现（内存态，P2 换持久化）
- `examples/01_basic_research_agent.py`（真实 OpenAI，需 OPENAI_API_KEY）、
  `examples/02_loop_offline_demo.py`（零依赖离线演示）
- 测试 49 个（总覆盖率 99%，`core/loop.py` 100%），含 C2 断点重放等价参数化测试

### Added
- **契约基线 v1.0.0（非破坏性：首次定义，无历史版本）**
  - `Event` / `Message` / `AgentState` / `Config`（`stableagent/core/state.py`）
  - `Provider` 三调用形态抽象（`stableagent/providers/base.py`）
  - `Tool` / `ToolRegistry` / `Evidence` 失败归一化（`stableagent/tools/registry.py`）
- 包骨架：core / context / planning / tools / sandbox / memory / control / providers / transport
  （空模块标注各自交付阶段）
- `docs/design-decisions/00-contracts.md`：契约规范、版本化规则、P2 起冻结范围
- CI（GitHub Actions）：ruff + mypy + pytest + coverage
- 示例 `examples/00_agent_skeleton.py`：可导入、可构造、不执行

### Fixed
- `OpenAIProvider._parse_response`：网关 `usage` 含嵌套 `*_details` 对象时 `int()` 崩溃，
  改为只累计数值字段（真实网关端到端 A1 验证时发现）

### 修订窗口约定
- P0~P1：契约允许修订，必须在本文件记录并标注破坏性/非破坏性。
- P2 起：契约冻结，只允许向后兼容扩展（新增带默认值的可选字段 / 新增枚举成员 / 新增配置节）。
