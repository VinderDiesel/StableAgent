# P6 单仓库 Super-Agent Beta 验收账本与实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单仓库内建立常驻 Router 与多 Worker 控制平面：事件去重、Thread 归属、
显式授权建票、上下文订阅、最小监管面，并以 `observe` 模式完成 Beta 演练。

**Architecture:** 新增 `stableagent/supervisor/`，依赖方向为
`supervisor → integrations → execution → control → orchestration → core`。
持久化用 `SupervisorStore(Store)` 子类共享同一 SQLite 连接与 `BEGIN IMMEDIATE` 事务；
Trigger/Decision/Subscription 与 Ticket/Assignment/Effect 可同事务落库。旧包禁止 import
supervisor。

**Tech Stack:** Python 3.10+、Pydantic v2、SQLite（JSON1）、pytest / mypy / ruff。

**Spec:** [P6 实施规格](../specs/p6-single-repository-super-agent-beta.md)

## Global Constraints

- Python 下限 `>=3.10`；行宽 100；`ruff check` 与 `ruff format --check` 全绿。
- `stableagent/core/` 对 P4 收口提交 `234f620` 零 diff（L1）；`orchestration/service.py` 一字不动。
- 依赖方向只有 `supervisor → integrations → execution → control → orchestration → core`；
  旧包 import supervisor 即失败。
- Beta 锁定 `observe`；`auto` 未启用；merge / auto-merge 继续结构性缺席（L6）。
- 外部文本（Issue/PR 正文、评论、CI 日志、Worker 输出）只能是 digest、上下文或 Report，
  不能成为授权来源（L4）。
- 事务体内禁止 `await`（L3）；外部 I/O 在事务外。
- 全量测试与 CI 不依赖网络、真实 GitHub 或 Docker（L8）。
- 门禁命令统一带 `env -u NO_PROXY -u no_proxy`；读数先落 `/tmp/p6-*.txt` 再读回。

---

## 0. P6 出口不变量（违反任意一条即不收口）

| 编号 | 不变量 |
|---|---|
| I1 | Trigger 以 `UNIQUE(source, external_id)` 在数据库去重，payload 不可变 |
| I2 | Thread/Channel 显式映射；Channel 只是目的地，不携带授权 |
| I3 | Subscription 的 `context` 模式不扩大 Mandate，后续输入只提供上下文 |
| I4 | Decision 六种 outcome / 三种 strength；Beta 只记录 observe，不执行推断任务 |
| I5 | Trigger 去重、Thread 更新、cursor 推进、扇出与建票在同一事务内 |
| I6 | 视图只读且不返回 payload / 原始日志正文 |

## 1. 里程碑

### M1：Supervisor 基础层（规格 WP-6.1；ADR-06 决策 1/2/3/4/5/6/7/10）

> **状态：M1 收口（Task 1~8 全部完成，2026-11-08）。** 证据提交：`1281566`（文档）、
> `e7de113`（模型）、`ef03c60`（Trigger/Thread）、`43f15d0`（Channel/Subscription）、
> `cf6af4a`（Decision/SourceCursor）、`c3f9801`（视图）、`2e04a1f`（CI FOCUS）、本收口提交。

- [x] **M1-1** ADR-06 落盘并 Accepted，账本与本计划建立。
      `docs/design-decisions/06-super-agent-router.md` 共 10 项决策、5 个被否决方案；
      `docs/guides/supervisor-migration.md` 写明备份、marker 校验与降级步骤；提交 `1281566`。
- [x] **M1-2** `supervisor/` 包骨架与 `model.py`：六个记录、枚举、canonical digest。
      `tests/unit/test_supervisor_model.py` 5 passed；提交 `e7de113`。
- [x] **M1-3** `SupervisorStore`：七张表、索引、`p6-supervisor-v1` marker、P5 库无损迁移、
      Trigger/Thread CRUD。`tests/unit/test_supervisor_store.py` 首段 8 passed（含
      "supervisor 与 Ticket 同事务回滚"）；提交 `ef03c60`。
- [x] **M1-4** Channel 与 Subscription CRUD：目的地唯一、live 订阅部分唯一、结束可重订。
      累计 11 passed；提交 `43f15d0`。
- [x] **M1-5** Decision（含 trigger 联结与 attempt 重评估）与 SourceCursor CRUD。
      累计 15 passed；提交 `cf6af4a`。
- [x] **M1-6** `views.py`：`needs_attention`、`open_tickets`、`failed_effects`、
      `source_health`、`thread_timeline`、`ticket_timeline`。
      `tests/unit/test_supervisor_views.py` 6 passed；提交 `c3f9801`。
- [x] **M1-7** CI FOCUS 由 25 扩到 28（新增 supervisor model/store/views），契约测试同步。
      `tests/unit/test_ci_focus_gate.py` 1 passed；coverage.xml 实测三文件
      `98.25% / 98.23% / 98.59%`（均 ≥90%）；提交 `2e04a1f`。
- [x] **M1-8** M1 出口：全量门禁、三解释器、L1/约束 1 复核、账本与状态面回填。
      本地读数（落盘 `/tmp/p6-m1-*.txt` 后读回）：`715 passed, 6 skipped, 0 failed`
      （19.10s，覆盖 98.99%）；`ruff check` `All checks passed!`；`ruff format --check`
      `143 files already formatted`；`mypy` `Success: no issues found in 64 source files`；
      3.11.16 与 3.12.14 同为 `715 passed, 6 skipped`；
      `git diff 234f620..HEAD --stat -- stableagent/core/` 与
      `... -- stableagent/orchestration/service.py` 均为 0 行；`git diff --check` 无输出。
      本提交完成账本与状态面回填。

### M2：GitHubSource 与 Trigger 接入（规格 WP-6.2）

> **状态：M2 收口（Task 1~7 全部完成）。** 主路径为 polling；读客户端沿用 P5 的
> "协议 + 确定性 fake" 形态，读权限与写权限分离。任务级计划见 §7。

- [x] **M2-1** `Source` / `ChannelWriter` 协议、Source 错误四分类与 `SourceEvent`/`PollBatch` 记录。
      `tests/unit/test_github_source.py` 起步用例通过；提交 `0c8b9a4`。
- [x] **M2-2** Issue / PR 标准化：稳定 Thread key、`#number` external_ref、bot 身份过滤。
      新增 Issue/PR 与 bot 过滤用例；提交 `082c7d4`。
- [x] **M2-3** comment / review 映射：issue comment、PR comment、review 会话与 reply Channel。
      新增评论/评审与 Channel 用例；提交 `88c2faa`。
- [x] **M2-4** CI status：有 PR 挂 PR Thread，无 PR 用 repository Thread。
      新增 CI 映射用例；提交 `86f8dcb`。
- [x] **M2-5** `poll`：cursor 分页、乱序排序、批内去重、删除事件、临时/403/404/schema 错误。
      `tests/unit/test_github_source.py` 累计 32 passed；提交 `8f5a6b9`。
- [x] **M2-6** 部署文档：polling 路径、只读权限、cursor 契约与 webhook 后续接法。
      `docs/guides/supervisor-github-source.md` 7 节全量落盘。
- [x] **M2-7** CI FOCUS 由 28 扩到 29（新增 `supervisor/source.py`），契约测试同步。
      `tests/unit/test_ci_focus_gate.py` 1 passed；coverage.xml 实测 `supervisor/source.py`
      行覆盖率 `96.27%`（≥90%）；提交 `04d6317`。
- [x] **M2-8** M2 出口：全量门禁、三解释器、L1/约束 1 复核、账本与状态面回填。
      本地读数（落盘 `/tmp/p6-m2-*.txt` 后读回）：`749 passed, 6 skipped, 0 failed`
      （19.73s，覆盖 98.89%）；`ruff check` `All checks passed!`；`ruff format --check`
      `145 files already formatted`；`mypy` `Success: no issues found in 65 source files`；
      3.10 / 3.11.16 / 3.12.14 均为 `749 passed, 6 skipped`；
      `git diff 234f620..HEAD --stat -- stableagent/core/` 与
      `... -- stableagent/orchestration/service.py` 均为 0 行；
      下层包真实 import `stableagent.supervisor` 的 grep 命中 0 行；`git diff --check` 无输出。
      本提交完成账本与状态面回填。

### M3：Router Intake、Subscription 与 Decision（规格 WP-6.3）

> **状态：M3 收口（Task 1~7 全部完成）。** `receive(trigger)`
> 是外部事件进入领域的唯一命令；`observe` 锁定、Channel 不授予权限、context 订阅不扩大
> Mandate。任务级计划见 §9，出口读数见 §9 末尾。

- [x] **M3-1** `AutonomyMode`（`off`/`observe`/`auto`，`auto` 构造期拒绝）与
      `CandidateAssessor` 窄协议；`ObserveOnlyAssessor` 的确定性规则。
      `tests/unit/test_supervisor_assessor.py` 6 passed（六条规则各一例；`auto` 在
      `RouterService.__init__` 抛 `RouterModeError`）；提交 `bbb7342`。
- [x] **M3-2** GitHubSource 为带正文事件补 `payload["request"]`（`text`/`mentioned`/`url`）；
      `SupervisorStore` 增加 `next_decision_attempt` 与 `find_accepted_decision`。
      `tests/unit/test_github_source.py` 新增 2 例（mention 大小写不敏感、CI 事件无 request
      键）；`tests/unit/test_supervisor_store.py` 新增 2 例（按 digest 递增 attempt、跳过
      OBSERVE 只认 ACCEPT）；提交 `6d98962`。
- [x] **M3-3** `receive()`：Trigger 去重、Thread 解析、live Subscription 扇出（每 Ticket
      每事件一条 Input + deliver effect）、`off` 忽略与 `observe` 只记录。
      `tests/unit/test_supervisor_router.py` 前 20 例（duplicate 不新增 Decision/Ticket、
      扇出与 sequence 递增、`off` 不调评估器）；提交 `7fa4f79`。
- [x] **M3-4** explicit 准入：未准入 IGNORE；准入后 Mandate/Ticket/Input/Assignment/
      launch effect/own Subscription 与 Decision 同事务；malformed 与 repository 不匹配稳定拒绝。
      同文件覆盖五件原子写与两类稳定拒绝；提交 `7fa4f79`。
- [x] **M3-5** 归属语义：own Thread 后续消息 ATTACH、重复 `request_digest` ATTACH、
      已 stop 的 STOP、已结束的 OBSERVE 且保留 Input、多个 owner 的 `ambiguous_owner`。
      同文件覆盖五种归属分支（含终态订阅不扇出）；提交 `7fa4f79`。
- [x] **M3-6** `unavailable` 与 `reevaluate`：评估器异常只记 `unavailable`；重评估不重复扇出。
      `test_supervisor_router.py` 后 3 例（异常转 `unavailable`/`assessor_error`、
      `reevaluate` 追加 attempt 且不新增 Trigger/Input、未知 trigger 抛
      `RouterNotFoundError`）；提交 `9e2f1c3`。
- [x] **M3-7** 命令层：attach/subscribe/unsubscribe/stop（Task 5）与 handback/resume/
      `record_pull_request`（Task 6）；全部检查 operator、Ticket 状态与 Mandate 复检。
      `tests/unit/test_supervisor_service.py` 22 passed（stop 保留 own 护栏、handback 降级
      可开新 Ticket、resume 仅 COMPLETED/FAILED 且 `revalidate(when="launch")` + 审计、
      PR 报告升 own）；提交 `d5093f9`、`d9aabc3`。
- [x] **M3-8** CI FOCUS 29→32、`docs/guides/supervisor-router.md`、全量门禁与账本回填。
      `tests/unit/test_ci_focus_gate.py` 1 passed（32 文件、无通配兜底）；出口读数见 §9。

### M4：常驻调度、恢复与运行控制（规格 WP-6.4）

> **状态：M4 收口（Task 1~7 全部完成）。** 单实例锁、启动恢复、暂停与优雅停止都落在
> SQLite 单库内；每轮 `step()` 固定为心跳 → poll → 路由 → tick → 外部 effect。
> 任务级计划见 §11，出口读数见 §11 末尾。

- [x] **M4-1** `runtime_lock` / `runtime_control` 单例行与 schema v2 种子；锁获取/心跳/
      释放、暂停位与 generation、未路由查询、队列计数。`tests/unit/test_supervisor_store.py`
      23 passed（新增 6 例：schema 种子、租约过期、心跳、暂停跨重开、未路由、计数）；
      提交 `e525674`。
- [x] **M4-2** `Runtime.start()/step()`：锁失败不恢复、`recover()` 先行、按暂停位补路由、
      每轮心跳与 Source 错误空转；`build_runtime` 组成根。`tests/unit/test_supervisor_runtime.py`
      前 7 例（启动路由与锁、第二 owner 拒绝、step 派发、暂停 step、租约丢失、Source 错误、
      status）；提交 `329d622`。
- [x] **M4-3** 暂停/恢复与 `RuntimeController`：`pause_processing`/`resume_processing` 落库，
      恢复时补路由；operator 大小写不敏感。同文件 10 passed；提交 `798ae81`。
- [x] **M4-4** 优雅停止与 `serve`：`Scheduler.wait_idle` 只等待不取消，宽限期后释放锁并
      返回未完成 Assignment 数；`serve` 退出时调用 `stop()`。同文件 13 passed，
      `tests/unit/test_scheduler.py` 34 passed（新增 2 例）；提交 `40d146b`。
- [x] **M4-5** `retry_ticket`（仅 FAILED）/ `stop_ticket` / `reevaluate` 三条命令全部过
      operator 门禁；同文件 16 passed；提交 `f2ba104`。
- [x] **M4-6** 五个崩溃窗口集成证明：Trigger 未路由、Ticket 已提交未派发、comment effect
      不丢不重放、Worker 中断走 recover、cursor 失败整批回滚。
      `tests/integration/test_superagent_recovery.py` 5 passed；提交 `eec5fc0`。
- [x] **M4-7** CI FOCUS 32→33（`supervisor/runtime.py`）、`docs/guides/supervisor-runtime.md`、
      全量门禁与账本回填。`tests/unit/test_ci_focus_gate.py` 1 passed（33 文件、无通配兜底）；
      出口读数见 §11。

### M5：最小监管面与注意力协议（规格 WP-6.5）

> **状态：M5 收口（Task 1~8 全部完成）。** CLI-only 监管面：首屏按注意力排序，写命令
> 必须 `--confirm`；生命周期通知先草拟成 `COMMENT_ISSUE` 审批，人工批准后在同一事务
> 入队 effect，拒绝即静默。任务级计划见 §12，自检与出口读数见 §12 末尾。

- [x] **M5-1** 首屏段落、注意力目标与稳定链接：`AttentionItem` 增 `target_id/needs/
      why/actions/ticket_link/thread_link`，`QueueCounts` 与 `StatusSections` 汇总队列与
      Source 健康。`tests/unit/test_supervisor_views.py` 10 passed（新增 4 例）；提交
      `c402158`。
- [x] **M5-2** Ticket 详情与时间线 `object_id`：`TicketDetail` 聚合 Assignment、订阅与
      已批准摘要；`thread_timeline` / `ticket_timeline` 的事件带对象 id，不返回 payload。
      同文件 13 passed（新增 3 例）；提交 `094a5d1`。
- [x] **M5-3** 通知模板、限长与纯函数配置：四类固定模板、`max_chars` 截断与稳定 URL。
      `tests/unit/test_supervisor_notifications.py` 5 passed；提交 `6819cd8`。
- [x] **M5-4** 通知草拟器、digest 与审批去重：按 Ticket 状态草拟；mandate 必须含
      `comment_issue`；同 digest 只开一条 pending；已拒绝即静默。同文件 11 passed
      （新增 6 例）；提交 `c4c9934`。
- [x] **M5-5** 批准与 effect 同事务、失败 effect 重试、stop 撤销：`ApprovalService.decide`
      增加可选 `effect`，拒绝使运行中 Assignment 失败；`stop` 在同一事务撤销 pending。
      `tests/unit/test_approval_service.py` 12 passed（新增 3 例）、
      `tests/unit/test_supervisor_service.py` 24 passed（新增 2 例）；提交 `96c675b`。
- [x] **M5-6** Runtime 草拟接线与暂停恢复补路由：每轮 tick 后、外部 effect 派发前草拟；
      外部 resume 先补路由再草拟。`tests/unit/test_supervisor_runtime.py` 20 passed
      （新增 4 例）、`tests/integration/test_superagent_notifications.py` 3 passed；
      提交 `b3af2e2`。
- [x] **M5-7** Console 命令面与 stdlib CLI：只读 `status/ticket/thread`，写命令统一
      operator 门禁 + `--confirm`；退出码 0/1/2/3/4。`tests/unit/test_supervisor_console.py`
      11 passed；`service + runtime` 回归 44 passed；提交 `b356a13`。
- [x] **M5-8** CI FOCUS 33→35（`supervisor/notifications.py`、`supervisor/console.py`）、
      `docs/guides/supervisor-console.md`、全量门禁与账本回填。
      `tests/unit/test_ci_focus_gate.py` 1 passed（35 文件、无通配兜底）；出口读数见 §12。

### M6：Beta 演练与发布门禁（规格 WP-6.6）

> **状态：M6 收口（Task 1~6 全部完成）。** 离线演练示例 06（run/report 两 phase）+
> 只读 `BetaReport`（`views.beta_report` ← `store.beta_snapshot`）+ 真实仓库 runbook +
> ADR-06 决策 11。真实 GitHub HTTP 客户端不在 M6 实现；真实 Beta 数据由维护者按 runbook
> 回填前，P6 出口第 7 项保持未勾。任务级计划见 §13，自检与出口读数见 §13 末尾。

- [x] **M6-1** BetaSnapshot 与 BetaReport 只读聚合：`store.beta_snapshot()` 11 字段
      SQL 聚合（json_each 展开 reason_codes），`views.beta_report()` 冻结模型不返回
      payload。`tests/unit/test_supervisor_views.py` 15 passed（新增 2 例）、
      `tests/unit/test_supervisor_store.py` 23 passed 回归；提交 `1bb4ba8`。
- [x] **M6-2** 示例 06 run phase 离线演练：多 Issue/PR/CI Trigger、两个并发 Ticket、
      FAILED/WAITING/READY→COMPLETED 后评论经 operator resume 恢复；断言外部评论恰好
      一次、同 Thread 不重复建票、bot 自事件过滤。
      `tests/integration/test_superagent_beta.py` 1 passed；提交 `ac5b164`。
- [x] **M6-3** 示例 06 report phase：只读同一 `beta.db` 输出 `BETA_REPORT <json>`，
      不写业务数据、不发外部消息。同文件 2 passed（新增 1 例）；提交 `8b47ab8`。
- [x] **M6-4** 真实仓库 Beta runbook：`docs/guides/supervisor-beta.md` 声明真实 HTTP
      客户端前置缺口、安全边界、record-only→explicit 两阶段、人工数据记录表（PR 重复数
      必须为 0）、回滚与发布门禁；相对链接均指向既有文件。提交 `f9e58f7`。
- [x] **M6-5** ADR-06 决策 11 与状态面收口：离线先行、observe 数据是 auto 唯一依据；
      账本 §1/§2/§3/§4 与 README/docs/CHANGELOG 同步到 M6 完成。
- [x] **M6-6** 全量门禁、三解释器矩阵与出口回填：FOCUS 35 不变（store/views 已在列），
      出口读数见 §13。

## 2. P6 退出清单（规格 §4）

- [x] GitHub Source 将所有支持事件确定性映射为 Trigger/Thread/Channel，并持久化
      cursor/去重状态。
- [x] Router 是 Trigger、Decision、Subscription 与 Ticket 创建的唯一写入口。
- [x] 所有显式任务经过 P5 Policy/Mandate；推断候选在 `observe` 只记录、不执行。
- [x] Ticket 后续评论、PR 和 CI 输入正确归属，且不会因此扩大授权。
- [x] Router、Source、Outbox、Scheduler 和 Worker 的崩溃恢复不丢失或重复语义副作用。
- [x] 监管入口以待人工事项为首要视图，所有外部消息符合注意力预算。
- [ ] 单仓库 Beta 有 dry-run 记录、明确风险边界、回滚步骤和可审计数据。
      （真实 Beta 数据由维护者按 runbook 回填前不勾，见 §13 决议 1）
- [x] 所有离线门禁通过；新增 supervisor 核心模块的 CI 覆盖率 ≥90%。

## 3. 阶段验证命令（收口前须在 CI 复现）

```bash
.venv/bin/pytest -q tests/unit/test_supervisor_model.py tests/unit/test_supervisor_store.py
.venv/bin/pytest -q tests/unit/test_supervisor_views.py \
  tests/unit/test_supervisor_notifications.py tests/unit/test_supervisor_console.py \
  tests/unit/test_ci_focus_gate.py
.venv/bin/pytest -q tests/unit/test_supervisor_assessor.py tests/unit/test_supervisor_router.py \
  tests/unit/test_supervisor_service.py
.venv/bin/pytest -q tests/unit/test_supervisor_runtime.py \
  tests/integration/test_superagent_recovery.py
.venv/bin/pytest -q tests/integration/test_superagent_beta.py
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
git diff 234f620..HEAD --stat -- stableagent/core/   # 必须 0 行（L1）
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py   # 必须 0 行
```

## 4. 任务级实施计划说明

M1 展开为 **8 个 Task / 47 个 Step**，M2 展开为 **7 个 Task / 43 个 Step**，M3 展开为
**7 个 Task / 38 个 Step**，M4 展开为 **7 个 Task / 39 个 Step**，M5 展开为
**8 个 Task / 44 个 Step**，M6 展开为 **6 个 Task / 27 个 Step**。每个 Task 独立可验收、
独立提交；Task 之间的接口在各自
`Interfaces` 块写清。任务级 Step 不计入里程碑步骤。

## 5. M1 任务级实施计划（writing-plans 产出，2026-11-08）

### Task 1：ADR-06 与 P6 账本落盘（M1-1）

**Files:**
- Create: `docs/design-decisions/06-super-agent-router.md`
- Create: `docs/acceptance/p6-supervisor-beta.md`
- Create: `docs/guides/supervisor-migration.md`

**Interfaces:**
- Consumes: P6 spec §2/§3、ADR-04/05 的决策风格。
- Produces: 后续 Task 引用的决策编号（ADR-06 决策 1~10）、验收条目（M1-1~M1-8）
  与运维迁移口径（备份、marker 检查、降级）。

- [ ] **Step 1: 落 ADR-06**

内容以本仓库 ADR-05 的章节顺序组织：背景、约束、决策、被否决方案、后果与代价、
测试与证据、阶段交接。决策必须覆盖持久化分层、Trigger 去重、Thread/Channel、
Subscription、Decision、事务边界、Source/Channel 协议、注意力预算、迁移降级。

- [ ] **Step 2: 落本账本与计划**

写入第 0~5 节；本文件就是 P6 的验收账本与 M1 计划，不再另建计划文件。

- [ ] **Step 3: 落迁移运维文档（规格 WP-6.1 第 5 条）**

`docs/guides/supervisor-migration.md` 必须写明：

```markdown
# Supervisor 迁移与降级

## 升级（P5 → P6）

1. 停止进程，备份 SQLite 文件：`cp state.db state.db.bak-$(date +%Y%m%d%H%M%S)`。
2. 用 P6 二进制启动一次；`SupervisorStore` 只新增表、索引与 `p6-supervisor-v1` marker。
3. 校验 marker：`SELECT version FROM schema_migrations ORDER BY applied_at, rowid;`
   应同时包含 `p5-control-v1` 与 `p6-supervisor-v1`。

## 降级（P6 → P5）

1. 停止进程并再次备份。
2. P5 二进制直接打开同一文件即可读取原有表；新增的 supervisor 表被忽略。
3. 不得删除 `schema_migrations` 中的 `p6-supervisor-v1`；再次升级必须幂等。
4. 若需回到升级前状态，用第 1 步的备份整库恢复，不做逐表删除。
```

- [ ] **Step 4: 校验链接与占位符**

Run: `grep -n 'TBD\|TODO\|待补' docs/design-decisions/06-super-agent-router.md docs/acceptance/p6-supervisor-beta.md docs/guides/supervisor-migration.md`
Expected: 只命中本命令行自身那一行；其余三份文档无占位符。

- [ ] **Step 5: 提交**

```bash
git add docs/design-decisions/06-super-agent-router.md docs/acceptance/p6-supervisor-beta.md docs/guides/supervisor-migration.md
git commit -m "docs: accept adr-06 and open the p6 m1 plan"
```

---

### Task 2：supervisor 包骨架与领域模型（M1-2）

**Files:**
- Create: `stableagent/supervisor/__init__.py`
- Create: `stableagent/supervisor/model.py`
- Test: `tests/unit/test_supervisor_model.py`

**Interfaces:**
- Consumes: Pydantic v2（仓库已依赖 `pydantic>=2.7`）。
- Produces:
  - `SUPERVISOR_SCHEMA_VERSION = "p6-supervisor-v1"`
  - `canonical_json(value: Any) -> str`
  - `payload_digest(payload: dict[str, Any]) -> str`
  - `TriggerKind`、`ThreadKind`、`ThreadStatus`、`ChannelKind`、`SubscriptionMode`、
    `DecisionOutcome`、`DecisionStrength`、`SourceHealth`
  - `Trigger`、`Thread`、`Channel`、`Subscription`、`Decision`、`SourceCursor`

- [ ] **Step 1: 写失败测试**

```python
"""P6 领域记录：digest 规范化、Trigger 不可变、枚举值钉死。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
    canonical_json,
    payload_digest,
)


def _trigger(**overrides: object) -> Trigger:
    data: dict[str, object] = {
        "id": "tg-0001",
        "external_id": "issue:1",
        "kind": TriggerKind.ISSUE,
        "actor": "alice",
        "thread_keys": ["issue:acme/widgets#1"],
        "occurred_at": 1_700_000_000_000,
        "received_at": 1_700_000_000_001,
        "payload": {"number": 1, "title": "add rate limiter"},
    }
    data.update(overrides)
    return Trigger(**data)


def test_canonical_json_and_digest_are_order_independent() -> None:
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert payload_digest({"b": 2, "a": 1}) == payload_digest({"a": 1, "b": 2})
    assert payload_digest({"a": 1}) != payload_digest({"a": 2})


def test_trigger_fills_digest_and_rejects_mismatch() -> None:
    trigger = _trigger()
    assert trigger.digest == payload_digest({"number": 1, "title": "add rate limiter"})
    with pytest.raises(ValidationError):
        _trigger(digest="0" * 64)


def test_trigger_is_frozen_and_forbids_extra_fields() -> None:
    trigger = _trigger()
    with pytest.raises(ValidationError):
        trigger.actor = "mallory"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _trigger(unexpected=True)


def test_enum_values_are_pinned() -> None:
    assert {k.value for k in TriggerKind} == {
        "issue",
        "pull_request",
        "issue_comment",
        "review_comment",
        "review",
        "ci_status",
    }
    assert {k.value for k in DecisionOutcome} == {
        "accept",
        "observe",
        "ignore",
        "attach",
        "stop",
        "unavailable",
    }
    assert {k.value for k in DecisionStrength} == {"explicit", "inferred", "not_admitted"}
    assert {k.value for k in SubscriptionMode} == {"own", "context"}
    assert {k.value for k in SourceHealth} == {"ok", "degraded", "failed"}


def test_records_have_safe_defaults() -> None:
    thread = Thread(
        key="issue:acme/widgets#1",
        repository="acme/widgets",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=1,
    )
    assert thread.status is ThreadStatus.OPEN
    channel = Channel(
        id="ch-1", thread_key=thread.key, kind=ChannelKind.ISSUE_COMMENT, external_ref="#1"
    )
    assert channel.kind is ChannelKind.ISSUE_COMMENT
    subscription = Subscription(
        id="sb-1",
        ticket_id="tk-1",
        thread_key=thread.key,
        mode=SubscriptionMode.OWN,
        created_at=1,
    )
    assert subscription.ended_at is None
    decision = Decision(
        id="dc-1",
        trigger_ids=["tg-0001"],
        outcome=DecisionOutcome.OBSERVE,
        strength=DecisionStrength.INFERRED,
        policy_version="p6-v1",
        request_digest="d",
        created_at=1,
    )
    assert decision.reason_codes == [] and decision.mandate_id is None and decision.attempt == 1
    cursor = SourceCursor(source="github")
    assert cursor.health is SourceHealth.OK and cursor.consecutive_failures == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_model.py --no-cov`
Expected: FAIL，`ModuleNotFoundError: No module named 'stableagent.supervisor'`。

- [ ] **Step 3: 实现包与模型**

`stableagent/supervisor/__init__.py`：

```python
"""P6 控制平面：常驻 Router、Trigger/Thread/Subscription 与只读监管视图。

方向锁：supervisor → integrations → execution → control → orchestration → core；
旧包禁止 import 本包。记录结构在 P6 Beta 前不承诺稳定（ADR-06 决策 1）。
"""

from __future__ import annotations

SUPERVISOR_SCHEMA_VERSION = "p6-supervisor-v1"

__all__ = ["SUPERVISOR_SCHEMA_VERSION"]
```

`stableagent/supervisor/model.py`：

```python
"""P6 领域记录。Trigger 不可变；时间用毫秒整数；ID 由调用方注入。"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def canonical_json(value: Any) -> str:
    """跨进程稳定的 JSON 写法：键有序、无多余空白、非 ASCII 原样保留。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class TriggerKind(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    ISSUE_COMMENT = "issue_comment"
    REVIEW_COMMENT = "review_comment"
    REVIEW = "review"
    CI_STATUS = "ci_status"


class ThreadKind(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    REVIEW = "review"


class ThreadStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    STOPPED = "stopped"


class ChannelKind(str, Enum):
    ISSUE_COMMENT = "issue_comment"
    PULL_REQUEST_COMMENT = "pull_request_comment"
    REVIEW_REPLY = "review_reply"


class SubscriptionMode(str, Enum):
    OWN = "own"
    CONTEXT = "context"


class DecisionOutcome(str, Enum):
    ACCEPT = "accept"
    OBSERVE = "observe"
    IGNORE = "ignore"
    ATTACH = "attach"
    STOP = "stop"
    UNAVAILABLE = "unavailable"


class DecisionStrength(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    NOT_ADMITTED = "not_admitted"


class SourceHealth(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class Trigger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source: Literal["github"] = "github"
    external_id: str
    kind: TriggerKind
    actor: str
    thread_keys: list[str] = Field(default_factory=list)
    occurred_at: int
    received_at: int
    payload: dict[str, Any]
    digest: str = ""

    @model_validator(mode="before")
    @classmethod
    def _seal_digest(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return data
        expected = payload_digest(payload)
        supplied = data.get("digest") or expected
        if supplied != expected:
            raise ValueError("trigger digest does not match payload")
        return {**data, "digest": supplied}


class Thread(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    source: Literal["github"] = "github"
    repository: str
    kind: ThreadKind
    external_ref: str
    last_trigger_at: int
    status: ThreadStatus = ThreadStatus.OPEN


class Channel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    thread_key: str
    kind: ChannelKind
    external_ref: str


class Subscription(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    ticket_id: str
    thread_key: str
    mode: SubscriptionMode
    created_at: int
    ended_at: int | None = None


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    trigger_ids: list[str]
    outcome: DecisionOutcome
    strength: DecisionStrength
    reason_codes: list[str] = Field(default_factory=list)
    policy_version: str
    mandate_id: str | None = None
    request_digest: str
    attempt: int = Field(default=1, ge=1)
    created_at: int


class SourceCursor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    cursor: str | None = None
    last_success_at: int | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    health: SourceHealth = SourceHealth.OK
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_model.py --no-cov`
Expected: PASS（5 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_model.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_model.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor tests/unit/test_supervisor_model.py
git commit -m "feat: add supervisor records with canonical trigger digests"
```

---

### Task 3：SupervisorStore、迁移与 Trigger/Thread 仓储（M1-3）

**Files:**
- Create: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_supervisor_store.py`

**Interfaces:**
- Consumes: `Store`（`stableagent.orchestration.store`）、Task 2 的全部记录类型。
- Produces:
  - `SupervisorStore(Store)`；marker `p6-supervisor-v1`
  - `insert_trigger(trigger: Trigger) -> bool`
  - `get_trigger(trigger_id: str) -> Trigger | None`
  - `get_trigger_by_external(source: str, external_id: str) -> Trigger | None`
  - `list_triggers(*, thread_key: str | None = None, limit: int = 100) -> list[Trigger]`
  - `upsert_thread(thread: Thread) -> None`
  - `get_thread(key: str) -> Thread | None`
  - `list_threads(*, limit: int = 100) -> list[Thread]`

- [ ] **Step 1: 写失败测试**

```python
"""SupervisorStore：P5 库无损扩展、Trigger 去重、Thread 单调状态。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stableagent.orchestration.model import Mandate, Ticket
from stableagent.orchestration.store import Store
from stableagent.supervisor import SUPERVISOR_SCHEMA_VERSION
from stableagent.supervisor.model import (
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock


def _trigger(external_id: str = "issue:1", *, id: str = "tg-0001") -> Trigger:
    return Trigger(
        id=id,
        external_id=external_id,
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"number": 1, "title": "add rate limiter"},
    )


def _thread(*, at: int = 10, status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    return Thread(
        key="issue:acme/widgets#1",
        repository="acme/widgets",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=at,
        status=status,
    )


@pytest.fixture
def store(tmp_path: Path) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=FixedClock())


def test_supervisor_schema_extends_a_p5_store_without_touching_it(tmp_path: Path) -> None:
    path = tmp_path / "p6.db"
    clock = FixedClock()
    p5 = Store(path, clock=clock)
    p5.insert_mandate(Mandate(id="md-1", granted_at=clock.now_ms()))
    p5.insert_ticket(
        Ticket(id="tk-1", title="t", task="t", created_at=clock.now_ms(), mandate_id="md-1")
    )
    p5.close()

    store = SupervisorStore(path, clock=clock)
    try:
        assert store.schema_version() == SUPERVISOR_SCHEMA_VERSION
        assert store.get_ticket("tk-1") is not None
        versions = {
            row[0]
            for row in store._conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert {"p5-control-v1", SUPERVISOR_SCHEMA_VERSION} <= versions
    finally:
        store.close()


def test_supervisor_schema_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "p6.db"
    first = SupervisorStore(path, clock=FixedClock())
    first.close()
    second = SupervisorStore(path, clock=FixedClock())
    try:
        assert second.schema_version() == SUPERVISOR_SCHEMA_VERSION
        assert second.list_triggers() == []
    finally:
        second.close()


def test_trigger_insert_is_idempotent_by_external_id(store: SupervisorStore) -> None:
    assert store.insert_trigger(_trigger()) is True
    assert store.insert_trigger(_trigger(id="tg-9999")) is False
    assert [t.id for t in store.list_triggers()] == ["tg-0001"]


def test_trigger_round_trip_preserves_payload_and_digest(store: SupervisorStore) -> None:
    trigger = _trigger()
    store.insert_trigger(trigger)
    loaded = store.get_trigger("tg-0001")
    assert loaded == trigger
    assert store.get_trigger_by_external("github", "issue:1") == trigger
    assert store.get_trigger_by_external("github", "issue:404") is None


def test_list_triggers_filters_by_thread_key(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    other = _trigger(external_id="issue:2", id="tg-0002").model_copy(
        update={"thread_keys": ["issue:acme/widgets#2"]}
    )
    store.insert_trigger(other)
    assert [t.id for t in store.list_triggers(thread_key="issue:acme/widgets#1")] == ["tg-0001"]


def test_thread_upsert_is_monotonic_for_time_and_status(store: SupervisorStore) -> None:
    store.upsert_thread(_thread(at=10))
    store.upsert_thread(_thread(at=20, status=ThreadStatus.CLOSED))
    store.upsert_thread(_thread(at=15, status=ThreadStatus.OPEN))
    loaded = store.get_thread("issue:acme/widgets#1")
    assert loaded is not None
    assert loaded.last_trigger_at == 20
    assert loaded.status is ThreadStatus.CLOSED


def test_thread_external_ref_is_unique_per_source_and_repository(store: SupervisorStore) -> None:
    store.upsert_thread(_thread())
    duplicate = _thread().model_copy(update={"key": "issue:acme/widgets#1-dup"})
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_thread(duplicate)


def test_supervisor_and_ticket_writes_share_one_transaction(store: SupervisorStore) -> None:
    """ADR-06 决策 7：Trigger 去重与 Ticket 创建必须同生共死。"""
    with pytest.raises(RuntimeError), store.transaction():
        store.insert_trigger(_trigger())
        store.insert_ticket(
            Ticket(id="tk-tx", title="t", task="t", created_at=1, mandate_id="md-none")
        )
        raise RuntimeError("rollback")
    assert store.get_trigger("tg-0001") is None
    assert store.get_ticket("tk-tx") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py --no-cov`
Expected: FAIL，`ModuleNotFoundError: No module named 'stableagent.supervisor.store'`。

- [ ] **Step 3: 实现 Store 子类、建表与 Trigger/Thread CRUD**

`stableagent/supervisor/store.py` 的首段（本 Task 只实现 Trigger/Thread；Channel 等在
Task 4/5 追加到同一文件）：

```python
"""Supervisor 持久化。表、索引与 CRUD 全部在本包，orchestration 不知道 supervisor。

与 P4 Store 共用连接与 BEGIN IMMEDIATE，因此 Trigger/Decision/Subscription 和
Ticket/Assignment/Effect 可以在同一事务内落库（规格 2.1、ADR-06 决策 2/7）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from stableagent.orchestration.model import Clock
from stableagent.orchestration.store import Store
from stableagent.supervisor import SUPERVISOR_SCHEMA_VERSION
from stableagent.supervisor.model import (
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)

_SUPERVISOR_SCHEMA: dict[str, str] = {
    "triggers": (
        "CREATE TABLE IF NOT EXISTS triggers ("
        " id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, actor TEXT NOT NULL,"
        " thread_keys TEXT NOT NULL DEFAULT '[]',"
        " occurred_at INTEGER NOT NULL, received_at INTEGER NOT NULL,"
        " payload TEXT NOT NULL, digest TEXT NOT NULL,"
        " UNIQUE (source, external_id))"
    ),
    "threads": (
        "CREATE TABLE IF NOT EXISTS threads ("
        " key TEXT PRIMARY KEY, source TEXT NOT NULL, repository TEXT NOT NULL,"
        " kind TEXT NOT NULL, external_ref TEXT NOT NULL,"
        " last_trigger_at INTEGER NOT NULL, status TEXT NOT NULL,"
        " UNIQUE (source, repository, kind, external_ref))"
    ),
}

_SUPERVISOR_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS triggers_received ON triggers (received_at, id)",
    "CREATE INDEX IF NOT EXISTS threads_last_trigger ON threads (last_trigger_at, key)",
)


def _row_to_trigger(row: sqlite3.Row) -> Trigger:
    return Trigger(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        kind=TriggerKind(row["kind"]),
        actor=row["actor"],
        thread_keys=json.loads(row["thread_keys"]),
        occurred_at=row["occurred_at"],
        received_at=row["received_at"],
        payload=json.loads(row["payload"]),
        digest=row["digest"],
    )


def _row_to_thread(row: sqlite3.Row) -> Thread:
    return Thread(
        key=row["key"],
        source=row["source"],
        repository=row["repository"],
        kind=ThreadKind(row["kind"]),
        external_ref=row["external_ref"],
        last_trigger_at=row["last_trigger_at"],
        status=ThreadStatus(row["status"]),
    )


class SupervisorStore(Store):
    """P4 Store 的增量扩展：同一连接、同一事务、只加表。"""

    def __init__(self, db_path: str | Path, *, clock: Clock) -> None:
        super().__init__(db_path, clock=clock)
        with self.transaction():
            for ddl in _SUPERVISOR_SCHEMA.values():
                self._conn.execute(ddl)
            for ddl in _SUPERVISOR_INDEXES:
                self._conn.execute(ddl)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (SUPERVISOR_SCHEMA_VERSION, self._clock.now_ms()),
            )

    def insert_trigger(self, trigger: Trigger) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO triggers"
            " (id, source, external_id, kind, actor, thread_keys,"
            " occurred_at, received_at, payload, digest)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trigger.id,
                trigger.source,
                trigger.external_id,
                trigger.kind.value,
                trigger.actor,
                json.dumps(trigger.thread_keys, sort_keys=True, ensure_ascii=False),
                trigger.occurred_at,
                trigger.received_at,
                json.dumps(trigger.payload, sort_keys=True, ensure_ascii=False),
                trigger.digest,
            ),
        )
        return int(cursor.rowcount) == 1

    def get_trigger(self, trigger_id: str) -> Trigger | None:
        row = self._conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        return _row_to_trigger(row) if row else None

    def get_trigger_by_external(self, source: str, external_id: str) -> Trigger | None:
        row = self._conn.execute(
            "SELECT * FROM triggers WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        return _row_to_trigger(row) if row else None

    def list_triggers(self, *, thread_key: str | None = None, limit: int = 100) -> list[Trigger]:
        if thread_key is None:
            rows = self._conn.execute(
                "SELECT * FROM triggers ORDER BY received_at ASC, id ASC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT t.* FROM triggers t"
                " WHERE EXISTS (SELECT 1 FROM json_each(t.thread_keys) WHERE value = ?)"
                " ORDER BY t.received_at ASC, t.id ASC LIMIT ?",
                (thread_key, limit),
            ).fetchall()
        return [_row_to_trigger(row) for row in rows]

    def upsert_thread(self, thread: Thread) -> None:
        self._conn.execute(
            "INSERT INTO threads (key, source, repository, kind, external_ref,"
            " last_trigger_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET"
            " last_trigger_at = MAX(threads.last_trigger_at, excluded.last_trigger_at),"
            " status = CASE"
            "   WHEN threads.status = 'stopped' THEN 'stopped'"
            "   WHEN excluded.status = 'stopped' THEN 'stopped'"
            "   WHEN threads.status = 'closed' AND excluded.status = 'open' THEN 'closed'"
            "   ELSE excluded.status END",
            (
                thread.key,
                thread.source,
                thread.repository,
                thread.kind.value,
                thread.external_ref,
                thread.last_trigger_at,
                thread.status.value,
            ),
        )

    def get_thread(self, key: str) -> Thread | None:
        row = self._conn.execute("SELECT * FROM threads WHERE key = ?", (key,)).fetchone()
        return _row_to_thread(row) if row else None

    def list_threads(self, *, limit: int = 100) -> list[Thread]:
        rows = self._conn.execute(
            "SELECT * FROM threads ORDER BY last_trigger_at DESC, key ASC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_thread(row) for row in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py --no-cov`
Expected: PASS（8 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/store.py tests/unit/test_supervisor_store.py
git commit -m "feat: persist supervisor triggers and threads on the p4 store"
```

---

### Task 4：Channel 与 Subscription 仓储（M1-4）

**Files:**
- Modify: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_supervisor_store.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `SupervisorStore`。
- Produces:
  - `insert_channel(channel: Channel) -> bool`
  - `list_channels(thread_key: str) -> list[Channel]`
  - `insert_subscription(subscription: Subscription) -> bool`
  - `end_subscription(subscription_id: str, *, ended_at: int) -> int`
  - `list_subscriptions(*, ticket_id: str | None = None, thread_key: str | None = None, live_only: bool = False) -> list[Subscription]`

- [ ] **Step 1: 追加失败测试**

```python
def test_channel_is_unique_per_destination(store: SupervisorStore) -> None:
    channel = Channel(
        id="ch-1",
        thread_key="issue:acme/widgets#1",
        kind=ChannelKind.ISSUE_COMMENT,
        external_ref="#1",
    )
    assert store.insert_channel(channel) is True
    duplicate = channel.model_copy(update={"id": "ch-2"})
    assert store.insert_channel(duplicate) is False
    assert [c.id for c in store.list_channels(channel.thread_key)] == ["ch-1"]


def test_live_subscription_is_idempotent_and_can_be_resubscribed(
    store: SupervisorStore,
) -> None:
    live = Subscription(
        id="sb-1",
        ticket_id="tk-1",
        thread_key="issue:acme/widgets#1",
        mode=SubscriptionMode.OWN,
        created_at=10,
    )
    assert store.insert_subscription(live) is True
    assert store.insert_subscription(live.model_copy(update={"id": "sb-2"})) is False
    assert store.end_subscription("sb-1", ended_at=20) == 1
    assert store.end_subscription("sb-1", ended_at=21) == 0
    assert store.insert_subscription(live.model_copy(update={"id": "sb-3"})) is True
    live_rows = store.list_subscriptions(ticket_id="tk-1", live_only=True)
    assert [s.id for s in live_rows] == ["sb-3"]


def test_subscription_list_filters_by_thread_and_mode(store: SupervisorStore) -> None:
    for index, mode in enumerate((SubscriptionMode.OWN, SubscriptionMode.CONTEXT), start=1):
        store.insert_subscription(
            Subscription(
                id=f"sb-{index}",
                ticket_id="tk-1",
                thread_key="issue:acme/widgets#1",
                mode=mode,
                created_at=index,
            )
        )
    assert len(store.list_subscriptions(thread_key="issue:acme/widgets#1")) == 2
    assert [s.mode for s in store.list_subscriptions(ticket_id="tk-1")] == [
        SubscriptionMode.OWN,
        SubscriptionMode.CONTEXT,
    ]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py -k 'channel or subscription' --no-cov`
Expected: FAIL，`AttributeError: 'SupervisorStore' object has no attribute 'insert_channel'`。

- [ ] **Step 3: 追加表、索引与 CRUD**

在 `_SUPERVISOR_SCHEMA` 中追加：

```python
    "channels": (
        "CREATE TABLE IF NOT EXISTS channels ("
        " id TEXT PRIMARY KEY, thread_key TEXT NOT NULL, kind TEXT NOT NULL,"
        " external_ref TEXT NOT NULL,"
        " UNIQUE (thread_key, kind, external_ref))"
    ),
    "subscriptions": (
        "CREATE TABLE IF NOT EXISTS subscriptions ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, thread_key TEXT NOT NULL,"
        " mode TEXT NOT NULL, created_at INTEGER NOT NULL, ended_at INTEGER)"
    ),
```

在 `_SUPERVISOR_INDEXES` 中追加：

```python
    "CREATE INDEX IF NOT EXISTS subscriptions_thread ON subscriptions (thread_key, ended_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_live"
    " ON subscriptions (ticket_id, thread_key, mode) WHERE ended_at IS NULL",
```

在模块级追加行转换器：

```python
def _row_to_channel(row: sqlite3.Row) -> Channel:
    return Channel(
        id=row["id"],
        thread_key=row["thread_key"],
        kind=ChannelKind(row["kind"]),
        external_ref=row["external_ref"],
    )


def _row_to_subscription(row: sqlite3.Row) -> Subscription:
    return Subscription(
        id=row["id"],
        ticket_id=row["ticket_id"],
        thread_key=row["thread_key"],
        mode=SubscriptionMode(row["mode"]),
        created_at=row["created_at"],
        ended_at=row["ended_at"],
    )
```

在 `SupervisorStore` 末尾追加：

```python
    def insert_channel(self, channel: Channel) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO channels (id, thread_key, kind, external_ref)"
            " VALUES (?, ?, ?, ?)",
            (channel.id, channel.thread_key, channel.kind.value, channel.external_ref),
        )
        return int(cursor.rowcount) == 1

    def list_channels(self, thread_key: str) -> list[Channel]:
        rows = self._conn.execute(
            "SELECT * FROM channels WHERE thread_key = ? ORDER BY id ASC", (thread_key,)
        ).fetchall()
        return [_row_to_channel(row) for row in rows]

    def insert_subscription(self, subscription: Subscription) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO subscriptions"
            " (id, ticket_id, thread_key, mode, created_at, ended_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                subscription.id,
                subscription.ticket_id,
                subscription.thread_key,
                subscription.mode.value,
                subscription.created_at,
                subscription.ended_at,
            ),
        )
        return int(cursor.rowcount) == 1

    def end_subscription(self, subscription_id: str, *, ended_at: int) -> int:
        cursor = self._conn.execute(
            "UPDATE subscriptions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (ended_at, subscription_id),
        )
        return int(cursor.rowcount)

    def list_subscriptions(
        self,
        *,
        ticket_id: str | None = None,
        thread_key: str | None = None,
        live_only: bool = False,
    ) -> list[Subscription]:
        clauses, params = [], []
        if ticket_id is not None:
            clauses.append("ticket_id = ?")
            params.append(ticket_id)
        if thread_key is not None:
            clauses.append("thread_key = ?")
            params.append(thread_key)
        if live_only:
            clauses.append("ended_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM subscriptions {where} ORDER BY created_at ASC, id ASC", params
        ).fetchall()
        return [_row_to_subscription(row) for row in rows]
```

同时在文件顶部 import 中补 `Channel`、`ChannelKind`、`Subscription`、`SubscriptionMode`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py --no-cov`
Expected: PASS（11 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/store.py tests/unit/test_supervisor_store.py
git commit -m "feat: persist supervisor channels and subscriptions"
```

---

### Task 5：Decision 与 SourceCursor 仓储（M1-5）

**Files:**
- Modify: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_supervisor_store.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `SupervisorStore`。
- Produces:
  - `insert_decision(decision: Decision) -> None`
  - `get_decision(decision_id: str) -> Decision | None`
  - `list_decisions(*, trigger_id: str | None = None, mandate_id: str | None = None, limit: int = 100) -> list[Decision]`
  - `get_source_cursor(source: str) -> SourceCursor | None`
  - `put_source_cursor(cursor: SourceCursor) -> None`
  - `list_source_cursors() -> list[SourceCursor]`
  - `list_pending_approvals() -> list[dict[str, Any]]`
  - `list_failed_effects() -> list[dict[str, Any]]`

- [ ] **Step 1: 追加失败测试**

```python
def _decision(*, attempt: int = 1, trigger_id: str = "tg-0001") -> Decision:
    return Decision(
        id=f"dc-{attempt}",
        trigger_ids=[trigger_id],
        outcome=DecisionOutcome.OBSERVE,
        strength=DecisionStrength.INFERRED,
        reason_codes=["would_accept"],
        policy_version="p6-v1",
        request_digest="digest-1",
        attempt=attempt,
        created_at=10 * attempt,
    )


def test_decision_round_trip_keeps_trigger_links(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    store.insert_decision(_decision())
    loaded = store.get_decision("dc-1")
    assert loaded is not None
    assert loaded.trigger_ids == ["tg-0001"]
    assert store.list_decisions(trigger_id="tg-0001")[0].id == "dc-1"
    assert store.list_decisions(trigger_id="tg-404") == []


def test_decision_request_digest_is_idempotent_per_attempt(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    store.insert_decision(_decision())
    duplicate = _decision().model_copy(update={"id": "dc-dup"})
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_decision(duplicate)
    store.insert_decision(_decision(attempt=2).model_copy(update={"id": "dc-2"}))
    assert [d.attempt for d in store.list_decisions()] == [1, 2]


def test_decision_filters_by_mandate(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    bound = _decision().model_copy(update={"id": "dc-bound", "mandate_id": "md-1"})
    store.insert_decision(bound)
    assert [d.id for d in store.list_decisions(mandate_id="md-1")] == ["dc-bound"]


def test_source_cursor_upsert_keeps_one_row(store: SupervisorStore) -> None:
    store.put_source_cursor(SourceCursor(source="github", cursor="1"))
    store.put_source_cursor(
        SourceCursor(
            source="github",
            cursor="2",
            last_success_at=20,
            consecutive_failures=3,
            health=SourceHealth.DEGRADED,
        )
    )
    loaded = store.get_source_cursor("github")
    assert loaded is not None
    assert (loaded.cursor, loaded.consecutive_failures, loaded.health) == (
        "2",
        3,
        SourceHealth.DEGRADED,
    )
    assert [c.source for c in store.list_source_cursors()] == ["github"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py -k 'decision or cursor' --no-cov`
Expected: FAIL，`AttributeError: 'SupervisorStore' object has no attribute 'insert_decision'`。

- [ ] **Step 3: 追加表、索引与 CRUD**

在 `_SUPERVISOR_SCHEMA` 中追加：

```python
    "decisions": (
        "CREATE TABLE IF NOT EXISTS decisions ("
        " id TEXT PRIMARY KEY, outcome TEXT NOT NULL, strength TEXT NOT NULL,"
        " reason_codes TEXT NOT NULL DEFAULT '[]', policy_version TEXT NOT NULL,"
        " mandate_id TEXT, request_digest TEXT NOT NULL, attempt INTEGER NOT NULL,"
        " created_at INTEGER NOT NULL, UNIQUE (request_digest, attempt))"
    ),
    "decision_triggers": (
        "CREATE TABLE IF NOT EXISTS decision_triggers ("
        " decision_id TEXT NOT NULL, trigger_id TEXT NOT NULL,"
        " PRIMARY KEY (decision_id, trigger_id))"
    ),
    "source_cursors": (
        "CREATE TABLE IF NOT EXISTS source_cursors ("
        " source TEXT PRIMARY KEY, cursor TEXT, last_success_at INTEGER,"
        " consecutive_failures INTEGER NOT NULL DEFAULT 0,"
        " health TEXT NOT NULL DEFAULT 'ok')"
    ),
```

在 `_SUPERVISOR_INDEXES` 中追加：

```python
    "CREATE INDEX IF NOT EXISTS decisions_created ON decisions (created_at, id)",
    "CREATE INDEX IF NOT EXISTS decisions_mandate ON decisions (mandate_id, created_at)",
    "CREATE INDEX IF NOT EXISTS decision_triggers_trigger ON decision_triggers (trigger_id)",
```

在 `SupervisorStore` 内追加：

```python
    def insert_decision(self, decision: Decision) -> None:
        self._conn.execute(
            "INSERT INTO decisions"
            " (id, outcome, strength, reason_codes, policy_version, mandate_id,"
            " request_digest, attempt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision.id,
                decision.outcome.value,
                decision.strength.value,
                json.dumps(decision.reason_codes, sort_keys=True, ensure_ascii=False),
                decision.policy_version,
                decision.mandate_id,
                decision.request_digest,
                decision.attempt,
                decision.created_at,
            ),
        )
        self._conn.executemany(
            "INSERT INTO decision_triggers (decision_id, trigger_id) VALUES (?, ?)",
            [(decision.id, trigger_id) for trigger_id in decision.trigger_ids],
        )

    def _row_to_decision(self, row: sqlite3.Row) -> Decision:
        trigger_ids = [
            str(r[0])
            for r in self._conn.execute(
                "SELECT trigger_id FROM decision_triggers WHERE decision_id = ?"
                " ORDER BY trigger_id ASC",
                (row["id"],),
            ).fetchall()
        ]
        return Decision(
            id=row["id"],
            trigger_ids=trigger_ids,
            outcome=DecisionOutcome(row["outcome"]),
            strength=DecisionStrength(row["strength"]),
            reason_codes=json.loads(row["reason_codes"]),
            policy_version=row["policy_version"],
            mandate_id=row["mandate_id"],
            request_digest=row["request_digest"],
            attempt=row["attempt"],
            created_at=row["created_at"],
        )

    def get_decision(self, decision_id: str) -> Decision | None:
        row = self._conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        return self._row_to_decision(row) if row else None

    def list_decisions(
        self,
        *,
        trigger_id: str | None = None,
        mandate_id: str | None = None,
        limit: int = 100,
    ) -> list[Decision]:
        clauses, params = [], []
        joins = ""
        if trigger_id is not None:
            joins = " JOIN decision_triggers dt ON dt.decision_id = d.id"
            clauses.append("dt.trigger_id = ?")
            params.append(trigger_id)
        if mandate_id is not None:
            clauses.append("d.mandate_id = ?")
            params.append(mandate_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT DISTINCT d.* FROM decisions d{joins} {where}"
            " ORDER BY d.created_at ASC, d.id ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._row_to_decision(row) for row in rows]

    def get_source_cursor(self, source: str) -> SourceCursor | None:
        row = self._conn.execute(
            "SELECT * FROM source_cursors WHERE source = ?", (source,)
        ).fetchone()
        return _row_to_source_cursor(row) if row else None

    def put_source_cursor(self, cursor: SourceCursor) -> None:
        self._conn.execute(
            "INSERT INTO source_cursors"
            " (source, cursor, last_success_at, consecutive_failures, health)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(source) DO UPDATE SET cursor = excluded.cursor,"
            " last_success_at = excluded.last_success_at,"
            " consecutive_failures = excluded.consecutive_failures,"
            " health = excluded.health",
            (
                cursor.source,
                cursor.cursor,
                cursor.last_success_at,
                cursor.consecutive_failures,
                cursor.health.value,
            ),
        )

    def list_source_cursors(self) -> list[SourceCursor]:
        rows = self._conn.execute("SELECT * FROM source_cursors ORDER BY source ASC").fetchall()
        return [_row_to_source_cursor(row) for row in rows]

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM approval_requests WHERE status = 'pending'"
            " ORDER BY requested_at ASC, id ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_failed_effects(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT e.id AS effect_id, e.kind AS kind, e.last_error AS last_error,"
            " e.due_at AS due_at, a.ticket_id AS ticket_id"
            " FROM effects e"
            " JOIN assignments a ON json_extract(e.payload, '$.assignment_id') = a.id"
            " WHERE e.state = 'failed' ORDER BY e.due_at ASC, e.id ASC"
        ).fetchall()
        return [dict(row) for row in rows]
```

模块级追加：

```python
def _row_to_source_cursor(row: sqlite3.Row) -> SourceCursor:
    return SourceCursor(
        source=row["source"],
        cursor=row["cursor"],
        last_success_at=row["last_success_at"],
        consecutive_failures=row["consecutive_failures"],
        health=SourceHealth(row["health"]),
    )
```

同时在顶部 import 中补 `Decision`、`DecisionOutcome`、`DecisionStrength`、`SourceCursor`、
`SourceHealth`、`Any`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py --no-cov`
Expected: PASS（15 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/store.py tests/unit/test_supervisor_store.py
git commit -m "feat: persist supervisor decisions and source cursors"
```

---

### Task 6：只读监管视图（M1-6）

**Files:**
- Create: `stableagent/supervisor/views.py`
- Test: `tests/unit/test_supervisor_views.py`

**Interfaces:**
- Consumes: `SupervisorStore` 的只读方法、`orchestration.model` 的 Ticket/Report/TicketInput。
- Produces:
  - `AttentionItem`、`TicketSummary`、`FailedEffectView`、`TimelineEvent`
  - `SupervisorViews.needs_attention() -> list[AttentionItem]`
  - `SupervisorViews.open_tickets() -> list[TicketSummary]`
  - `SupervisorViews.failed_effects() -> list[FailedEffectView]`
  - `SupervisorViews.source_health() -> list[SourceCursor]`
  - `SupervisorViews.thread_timeline(thread_key: str) -> list[TimelineEvent]`
  - `SupervisorViews.ticket_timeline(ticket_id: str) -> list[TimelineEvent]`

- [ ] **Step 1: 写失败测试**

```python
"""只读监管视图：优先级顺序、失败 effect 归属、时间线不泄露 payload。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Effect,
    EffectKind,
    EffectState,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketInput,
    TicketStatus,
)
from stableagent.supervisor.model import (
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import SupervisorViews
from tests.support import FixedClock


@pytest.fixture
def store(tmp_path: Path) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=FixedClock())


def _ticket(store: SupervisorStore, ticket_id: str, status: TicketStatus) -> Ticket:
    clock = FixedClock()
    store.insert_mandate(Mandate(id=f"md-{ticket_id}", granted_at=clock.now_ms()))
    ticket = Ticket(
        id=ticket_id,
        title=f"title-{ticket_id}",
        task="SECRET-TASK",
        created_at=clock.now_ms(),
        status=status,
        mandate_id=f"md-{ticket_id}",
    )
    store.insert_ticket(ticket)
    return ticket


def test_needs_attention_is_ordered_by_actionability(store: SupervisorStore) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    failed = _ticket(store, "tk-fail", TicketStatus.RUNNING)
    ready = _ticket(store, "tk-ready", TicketStatus.READY)
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="push",
        resource_digest="d",
        reason="r",
        requested_at=1,
    )
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=failed.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )

    items = SupervisorViews(store).needs_attention()

    assert [(i.priority, i.source, i.ticket_id) for i in items] == [
        (0, "approval", waiting.id),
        (1, "failed_effect", failed.id),
        (2, "waiting_ticket", waiting.id),
        (3, "ready_ticket", ready.id),
    ]


def test_open_tickets_excludes_terminal_statuses(store: SupervisorStore) -> None:
    _ticket(store, "tk-run", TicketStatus.RUNNING)
    _ticket(store, "tk-done", TicketStatus.COMPLETED)
    _ticket(store, "tk-cancel", TicketStatus.CANCELLED)
    assert [t.id for t in SupervisorViews(store).open_tickets()] == ["tk-run"]


def test_failed_effects_carry_ticket_identity(store: SupervisorStore) -> None:
    failed = _ticket(store, "tk-fail", TicketStatus.RUNNING)
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=failed.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )
    rows = SupervisorViews(store).failed_effects()
    assert [(r.ticket_id, r.effect_id) for r in rows] == [(failed.id, "ef-1")]


def test_source_health_lists_persisted_cursors(store: SupervisorStore) -> None:
    store.put_source_cursor(SourceCursor(source="github", cursor="9", health=SourceHealth.DEGRADED))
    assert [c.health for c in SupervisorViews(store).source_health()] == [SourceHealth.DEGRADED]


def test_thread_timeline_includes_triggers_and_decisions_without_payload(
    store: SupervisorStore,
) -> None:
    trigger = Trigger(
        id="tg-1",
        external_id="issue:1",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"body": "DO-NOT-LEAK"},
    )
    store.insert_trigger(trigger)
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=["tg-1"],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            policy_version="p6-v1",
            request_digest="d-1",
            created_at=12,
        )
    )

    events = SupervisorViews(store).thread_timeline("issue:acme/widgets#1")

    assert [e.kind for e in events] == ["trigger", "decision"]
    assert "DO-NOT-LEAK" not in json.dumps([e.model_dump() for e in events])


def test_ticket_timeline_uses_sanctioned_summaries_only(store: SupervisorStore) -> None:
    ticket = _ticket(store, "tk-1", TicketStatus.RUNNING)
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id="as-1",
            kind=ReportKind.PROGRESS,
            summary="local-restricted exit=0 output=intact duration_ms=0",
            created_at=20,
        )
    )
    store.insert_input(
        TicketInput(
            id="in-1",
            ticket_id=ticket.id,
            sequence=1,
            payload="RAW-SECRET-INPUT",
            created_at=21,
        )
    )

    events = SupervisorViews(store).ticket_timeline(ticket.id)

    assert [e.kind for e in events] == ["report", "input"]
    rendered = json.dumps([e.model_dump() for e in events])
    assert "exit=0" in rendered
    assert "RAW-SECRET-INPUT" not in rendered and "SECRET-TASK" not in rendered
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_views.py --no-cov`
Expected: FAIL，`ModuleNotFoundError: No module named 'stableagent.supervisor.views'`。

- [ ] **Step 3: 实现只读视图**

```python
"""人工监管的只读查询。只读：本模块不写库、不建 Ticket、不投递消息。

排序是产品契约的一部分（规格 2 的注意力预算）：待人工审批 → 失败 effect →
waiting Ticket → ready Ticket。时间线只返回已批准的结构化摘要，不返回 payload。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from stableagent.orchestration.model import TicketStatus
from stableagent.supervisor.model import SourceCursor
from stableagent.supervisor.store import SupervisorStore


class AttentionItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    priority: int
    source: Literal["approval", "failed_effect", "waiting_ticket", "ready_ticket"]
    ticket_id: str | None
    detail: str


class TicketSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    status: TicketStatus
    created_at: int


class FailedEffectView(BaseModel):
    model_config = ConfigDict(frozen=True)

    effect_id: str
    kind: str
    ticket_id: str | None
    last_error: str | None
    due_at: int


class TimelineEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: int
    kind: Literal["trigger", "decision", "report", "input"]
    summary: str


_OPEN_STATUSES = (
    TicketStatus.OPEN,
    TicketStatus.QUEUED,
    TicketStatus.RUNNING,
    TicketStatus.WAITING,
    TicketStatus.READY,
    TicketStatus.BLOCKED,
)


class SupervisorViews:
    def __init__(self, store: SupervisorStore) -> None:
        self._store = store

    def needs_attention(self) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        for row in self._store.list_pending_approvals():
            items.append(
                AttentionItem(
                    priority=0,
                    source="approval",
                    ticket_id=row["ticket_id"],
                    detail=f"approval pending: {row['action']}",
                )
            )
        for row in self._store.list_failed_effects():
            items.append(
                AttentionItem(
                    priority=1,
                    source="failed_effect",
                    ticket_id=row["ticket_id"],
                    detail=f"effect failed: {row['kind']}",
                )
            )
        for ticket in self._store.list_tickets(status=TicketStatus.WAITING):
            items.append(
                AttentionItem(
                    priority=2,
                    source="waiting_ticket",
                    ticket_id=ticket.id,
                    detail="ticket waiting for human input",
                )
            )
        for ticket in self._store.list_tickets(status=TicketStatus.READY):
            items.append(
                AttentionItem(
                    priority=3,
                    source="ready_ticket",
                    ticket_id=ticket.id,
                    detail="ticket ready for review",
                )
            )
        return sorted(items, key=lambda item: (item.priority, item.ticket_id or "", item.detail))

    def open_tickets(self) -> list[TicketSummary]:
        rows = []
        for status in _OPEN_STATUSES:
            rows.extend(self._store.list_tickets(status=status))
        return [
            TicketSummary(
                id=ticket.id,
                title=ticket.title,
                status=ticket.status,
                created_at=ticket.created_at,
            )
            for ticket in sorted(rows, key=lambda ticket: (ticket.created_at, ticket.id))
        ]

    def failed_effects(self) -> list[FailedEffectView]:
        return [
            FailedEffectView(
                effect_id=row["effect_id"],
                kind=row["kind"],
                ticket_id=row["ticket_id"],
                last_error=row["last_error"],
                due_at=row["due_at"],
            )
            for row in self._store.list_failed_effects()
        ]

    def source_health(self) -> list[SourceCursor]:
        return self._store.list_source_cursors()

    def thread_timeline(self, thread_key: str) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        decisions: dict[str, TimelineEvent] = {}
        for trigger in self._store.list_triggers(thread_key=thread_key):
            events.append(
                TimelineEvent(
                    at=trigger.occurred_at,
                    kind="trigger",
                    summary=f"{trigger.kind.value}:{trigger.actor}",
                )
            )
            for decision in self._store.list_decisions(trigger_id=trigger.id):
                decisions[decision.id] = TimelineEvent(
                    at=decision.created_at,
                    kind="decision",
                    summary=decision.outcome.value,
                )
        events.extend(decisions.values())
        return sorted(events, key=lambda event: (event.at, event.kind, event.summary))

    def ticket_timeline(self, ticket_id: str) -> list[TimelineEvent]:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        events = [
            TimelineEvent(at=report.created_at, kind="report", summary=report.summary)
            for report in self._store.list_reports(ticket_id)
        ]
        events.extend(
            TimelineEvent(
                at=ticket_input.created_at,
                kind="input",
                summary=f"{ticket_input.kind}:{ticket_input.state}",
            )
            for ticket_input in self._store.list_inputs(ticket_id)
        )
        events.extend(
            TimelineEvent(at=decision.created_at, kind="decision", summary=decision.outcome.value)
            for decision in self._store.list_decisions(mandate_id=ticket.mandate_id)
        )
        return sorted(events, key=lambda event: (event.at, event.kind, event.summary))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_views.py --no-cov`
Expected: PASS（6 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_views.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_views.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/views.py tests/unit/test_supervisor_views.py
git commit -m "feat: expose read-only supervisor views with attention ordering"
```

---

### Task 7：CI FOCUS 25 → 28（M1-7）

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/unit/test_ci_focus_gate.py`

**Interfaces:**
- Consumes: Task 2/3/6 的三个新模块。
- Produces: FOCUS 名单 28 项，新增 `supervisor/model.py`、`supervisor/store.py`、
  `supervisor/views.py`。

- [ ] **Step 1: 改契约测试（先失败）**

把 `FOCUS_FILES` 追加：

```python
    "supervisor/model.py",
    "supervisor/store.py",
    "supervisor/views.py",
```

把测试名与断言改为：

```python
def test_focus_covers_the_twenty_eight_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 28
    assert "28 文件" in text
    assert text.count('".py"') == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_ci_focus_gate.py --no-cov`
Expected: FAIL，`assert len(FOCUS_FILES) == 28` 或 workflow 缺少新文件名。

- [ ] **Step 3: 同步 CI 工作流**

把步骤名里的 `25 文件` 改为 `28 文件`，并在 Python heredoc 的 `FOCUS` 元组追加：

```python
              "supervisor/model.py",
              "supervisor/store.py",
              "supervisor/views.py",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_ci_focus_gate.py --no-cov`
Expected: PASS。

- [ ] **Step 5: 本地重算 FOCUS**

Run:

```bash
.venv/bin/pytest -q --cov=stableagent --cov-report=xml
python - <<'PY'
import xml.etree.ElementTree as ET
files = ("supervisor/model.py", "supervisor/store.py", "supervisor/views.py")
rows = [
    (k.get("filename", ""), 100 * float(k.get("line-rate")))
    for k in ET.parse("coverage.xml").getroot().iter("class")
    if any((k.get("filename") or "").endswith(f) for f in files)
]
print(rows)
assert len(rows) == 3 and all(rate >= 90 for _, rate in rows), rows
PY
```

Expected: 三行均 ≥90%。

- [ ] **Step 6: 提交**

```bash
git add .github/workflows/ci.yml tests/unit/test_ci_focus_gate.py
git commit -m "ci: extend the cardiac coverage gate to supervisor"
```

---

### Task 8：M1 出口（M1-8）

**Files:**
- Modify: `docs/acceptance/p6-supervisor-beta.md`
- Modify: `README.md`、`docs/README.md`、`docs/Roadmap.md`、`docs/acceptance/README.md`、
  `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1~7 的产出与门禁读数。
- Produces: M1 勾选、状态面同步、可推送的收口提交。

- [ ] **Step 1: 跑全量门禁并落盘**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m1-pytest.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-pytest.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff check stableagent tests examples benchmarks > /tmp/p6-m1-ruff.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-ruff.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff format --check stableagent tests examples benchmarks > /tmp/p6-m1-format.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-format.txt
env -u NO_PROXY -u no_proxy .venv/bin/mypy > /tmp/p6-m1-mypy.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-mypy.txt
```

Expected: 四份文件均 `exit=0`；pytest 无 failed。

- [ ] **Step 2: 三解释器等价**

```bash
env -u NO_PROXY -u no_proxy .matrix/py311/bin/python -m pytest -q -p no:cacheprovider > /tmp/p6-m1-py311.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-py311.txt
env -u NO_PROXY -u no_proxy .matrix/py312/bin/python -m pytest -q -p no:cacheprovider > /tmp/p6-m1-py312.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-py312.txt
```

Expected: 两份均 `exit=0`，passed/skip 与 3.10 一致。

- [ ] **Step 3: 边界复核**

```bash
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m1-l1.txt
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py > /tmp/p6-m1-c1.txt
git diff --check
```

Expected: 前两份 0 行；`git diff --check` 无输出。

- [ ] **Step 4: 回填账本**

把第 1 节 M1-1~M1-8 改为 `[x]`，并在每条下追加证据：提交号、测试文件与用例数、
门禁读数、FOCUS 三文件覆盖率、L1/约束 1 的 0 行结论。

- [ ] **Step 5: 同步状态面**

- `README.md` P6 行：`🟡 active — M1 done (8/8); milestones 1/6`。
- `docs/README.md`：当前主线改为 P6 M1 完成，链接本账本、ADR-06 与
  `docs/guides/supervisor-migration.md`；阶段表 P6 行同步。
- `docs/acceptance/README.md`：P6 行标 Active（M1 完成）。
- `docs/Roadmap.md`：P6 段写明 M1 完成与下一步 M2。
- `CHANGELOG.md`：新增 P6 M1 条目。

- [ ] **Step 6: 提交**

```bash
git add docs/acceptance/p6-supervisor-beta.md README.md docs/README.md docs/Roadmap.md \
  docs/acceptance/README.md CHANGELOG.md
git commit -m "docs: close p6 m1 with the local gate evidence"
```

## 6. 自检记录（writing-plans）

- 规格覆盖：WP-6.1 的 5 条分别由 M1-2（模型）、M1-3（唯一约束/迁移）、
  M1-6（六个视图）、M1-1（迁移文档口径）覆盖；FOCUS ≥90% 由 M1-7 覆盖。
- 类型一致性：`Trigger.payload`/`digest`、`SupervisorStore` 方法名、视图类名在 Task
  2~6 的 Interfaces 与代码块中一致。
- 无占位符：所有 Step 都给出可执行命令或完整代码。

## 7. M2 任务级实施计划（writing-plans 产出，2026-11-08）

M2 展开为 **7 个 Task / 43 个 Step**，覆盖 M2-1~M2-8。主路径为 **polling**：读客户端只做
只读列举，写入只发生在 `poll` 的单个事务里；读权限与写权限分离，Channel 只是目的地。

### Task 1：只读客户端接缝与 Source 契约（M2-1）

**Files:**
- Modify: `stableagent/integrations/github.py`
- Create: `stableagent/supervisor/source.py`
- Test: `tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: `stableagent.supervisor.model.{Channel, ChannelKind, Thread, Trigger}`。
- Produces:
  - `GitHubReadKind`（6 值 str 枚举，与 `TriggerKind` 一一对应；方向锁禁止 integrations
    import supervisor，所以两边各有一个枚举，由 `TriggerKind(item.kind.value)` 对齐）
  - `GitHubReadItem(kind, occurred_at, resource, deleted=False)`、
    `GitHubReadPage(items, next_cursor)`
  - `GitHubReadClient.list_events(*, repository, cursor, limit) -> GitHubReadPage`、
    `FakeGitHubReadClient(items, *, page_size=None)`（`calls` / `fail_next`）
  - `SourceError` 四子类：`source.transient` / `source.auth` / `source.gone` / `source.schema`
  - `SourceEvent(trigger, thread, channel=None)`、`PollBatch(events, next_cursor, duplicates)`
  - `Source.poll`、`ChannelWriter.write`、`FakeChannelWriter`
  - `stable_trigger_id`、`stable_channel_id`、`issue_thread_key`、`pull_request_thread_key`、
    `review_thread_key(repository, pull_number, review_id)`、`repository_thread_key`

- [ ] **Step 1: 写失败测试**

```python
"""WP-6.2：只读轮询到 Trigger/Thread/Channel 的确定性映射。"""

from __future__ import annotations

import pytest

from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
    GitHubReadPage,
)
from stableagent.supervisor.model import Channel, ChannelKind
from stableagent.supervisor.source import (
    FakeChannelWriter,
    SourceAuthError,
    SourceGoneError,
    SourceSchemaError,
    SourceTransientError,
    issue_thread_key,
    pull_request_thread_key,
    repository_thread_key,
    review_thread_key,
    stable_channel_id,
    stable_trigger_id,
)


def _item(number: int = 1, *, occurred_at: int = 1) -> GitHubReadItem:
    return GitHubReadItem(
        kind=GitHubReadKind.ISSUE, occurred_at=occurred_at, resource={"number": number}
    )


async def test_read_fake_pages_with_offset_cursors_and_records_calls() -> None:
    client = FakeGitHubReadClient([_item(1), _item(2), _item(3)], page_size=2)
    assert isinstance(client, GitHubReadClient)

    first = await client.list_events(repository="owner/repo", cursor=None, limit=10)
    second = await client.list_events(repository="owner/repo", cursor=first.next_cursor, limit=10)

    assert [item.resource["number"] for item in first.items] == [1, 2]
    assert first.next_cursor == "offset:2"
    assert [item.resource["number"] for item in second.items] == [3]
    assert second.next_cursor is None
    assert client.calls == [
        {"repository": "owner/repo", "cursor": None, "limit": 10},
        {"repository": "owner/repo", "cursor": "offset:2", "limit": 10},
    ]


async def test_read_fake_fail_next_raises_once_then_clears() -> None:
    client = FakeGitHubReadClient([])
    client.fail_next = SourceTransientError("503")
    with pytest.raises(SourceTransientError):
        await client.list_events(repository="owner/repo", cursor=None, limit=1)
    empty = await client.list_events(repository="owner/repo", cursor=None, limit=1)
    assert empty == GitHubReadPage()
    assert client.fail_next is None


def test_source_errors_carry_stable_codes() -> None:
    assert SourceTransientError("503").code == "source.transient"
    assert SourceAuthError("403").code == "source.auth"
    assert SourceGoneError("404").code == "source.gone"
    assert SourceSchemaError("bad").code == "source.schema"


async def test_channel_writer_fake_records_and_fails_once() -> None:
    writer = FakeChannelWriter()
    channel = Channel(
        id="ch-1",
        thread_key="issue:owner/repo#1",
        kind=ChannelKind.ISSUE_COMMENT,
        external_ref="#1",
    )
    assert await writer.write(channel=channel, body="hi") == {"id": "fake-1"}
    writer.fail_next = SourceTransientError("boom")
    with pytest.raises(SourceTransientError):
        await writer.write(channel=channel, body="again")
    assert [body for _, body in writer.calls] == ["hi", "again"]
    assert writer.fail_next is None


def test_thread_keys_and_ids_are_stable() -> None:
    assert issue_thread_key("owner/repo", 7) == "issue:owner/repo#7"
    assert pull_request_thread_key("owner/repo", 7) == "pr:owner/repo#7"
    assert review_thread_key("owner/repo", 7, 42) == "review:owner/repo#7:42"
    assert repository_thread_key("owner/repo") == "repo:owner/repo"
    assert stable_trigger_id("github:owner/repo:issue:7") == stable_trigger_id(
        "github:owner/repo:issue:7"
    )
    assert stable_trigger_id("a") != stable_trigger_id("b")
    assert stable_trigger_id("a").startswith("tg-")
    first = stable_channel_id("issue:owner/repo#7", ChannelKind.ISSUE_COMMENT, "#7")
    second = stable_channel_id("issue:owner/repo#7", ChannelKind.ISSUE_COMMENT, "#7")
    assert first == second and first.startswith("ch-")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py`

Expected: FAIL —— `ModuleNotFoundError: No module named 'stableagent.supervisor.source'`
（`GitHubReadClient` 也尚未存在）。

- [ ] **Step 3: 在 `integrations/github.py` 追加只读接缝**

导入行改为：

```python
from collections.abc import Iterable
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field
```

文件末尾追加：

```python
class GitHubReadKind(str, Enum):
    """只读侧事件分类；与 supervisor 的 TriggerKind 一一对应，方向锁禁止反向 import。"""

    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    ISSUE_COMMENT = "issue_comment"
    REVIEW_COMMENT = "review_comment"
    REVIEW = "review"
    CI_STATUS = "ci_status"


class GitHubReadItem(BaseModel):
    """一条只读事件：`resource` 保持 GitHub 形状，`occurred_at` 已归一到毫秒。"""

    kind: GitHubReadKind
    occurred_at: int
    resource: dict[str, Any]
    deleted: bool = False


class GitHubReadPage(BaseModel):
    items: list[GitHubReadItem] = Field(default_factory=list)
    next_cursor: str | None = None


@runtime_checkable
class GitHubReadClient(Protocol):
    async def list_events(
        self, *, repository: str, cursor: str | None, limit: int
    ) -> GitHubReadPage: ...


class FakeGitHubReadClient:
    """确定性只读 fake：`offset:<n>` 游标分页，按调用顺序记录，失败一次即清空。"""

    def __init__(self, items: Iterable[GitHubReadItem], *, page_size: int | None = None) -> None:
        self.items = list(items)
        self.page_size = page_size
        self.calls: list[dict[str, Any]] = []
        self.fail_next: Exception | None = None

    async def list_events(
        self, *, repository: str, cursor: str | None, limit: int
    ) -> GitHubReadPage:
        self.calls.append({"repository": repository, "cursor": cursor, "limit": limit})
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        offset = int(cursor.split(":")[1]) if cursor else 0
        size = min(limit, self.page_size) if self.page_size is not None else limit
        chunk = self.items[offset : offset + size]
        consumed = offset + len(chunk)
        next_cursor = f"offset:{consumed}" if consumed < len(self.items) else None
        return GitHubReadPage(items=chunk, next_cursor=next_cursor)
```

- [ ] **Step 4: 新建 `supervisor/source.py` 契约层**

```python
"""GitHub Source 契约：把只读轮询结果标准化为 Trigger / Thread / Channel（规格 WP-6.2）。

方向锁：本模块只 import `integrations` 与 `supervisor.model`；读取只产生候选事件，
Channel 只是回写目的地（ADR-06 决策 8），不携带授权。
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from stableagent.supervisor.model import Channel, ChannelKind, Thread, Trigger


class SourceError(Exception):
    """Source 层稳定错误：四分类决定 cursor health 与是否可重试。"""

    code = "source.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class SourceTransientError(SourceError):
    """网络抖动、限流、5xx：cursor 记 degraded，下一轮可重试。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.transient", message)


class SourceAuthError(SourceError):
    """403：读权限不足或 token 失效，需要人工处理。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.auth", message)


class SourceGoneError(SourceError):
    """404：仓库不可见或事件已不存在，继续轮询不会恢复。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.gone", message)


class SourceSchemaError(SourceError):
    """载荷形状不合约：整批不落库，避免写进半个事实。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.schema", message)


class SourceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: Trigger
    thread: Thread
    channel: Channel | None = None


class PollBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: list[SourceEvent] = Field(default_factory=list)
    next_cursor: str | None = None
    duplicates: int = Field(default=0, ge=0)


@runtime_checkable
class Source(Protocol):
    async def poll(self, *, limit: int = 100) -> PollBatch: ...


@runtime_checkable
class ChannelWriter(Protocol):
    async def write(self, *, channel: Channel, body: str) -> dict[str, Any]: ...


class FakeChannelWriter:
    """记录 `(channel, body)`；`fail_next` 抛一次后清空。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Channel, str]] = []
        self.fail_next: Exception | None = None

    async def write(self, *, channel: Channel, body: str) -> dict[str, Any]:
        self.calls.append((channel, body))
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        return {"id": f"fake-{len(self.calls)}"}


def stable_trigger_id(external_id: str) -> str:
    return "tg-" + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:12]


def stable_channel_id(thread_key: str, kind: ChannelKind, external_ref: str) -> str:
    seed = f"{thread_key}|{kind.value}|{external_ref}"
    return "ch-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def issue_thread_key(repository: str, number: int) -> str:
    return f"issue:{repository}#{number}"


def pull_request_thread_key(repository: str, number: int) -> str:
    return f"pr:{repository}#{number}"


def review_thread_key(repository: str, pull_number: int, review_id: int) -> str:
    return f"review:{repository}#{pull_number}:{review_id}"


def repository_thread_key(repository: str) -> str:
    return f"repo:{repository}"
```

- [ ] **Step 5: 跑测试确认通过并做静态检查**

Run:
`.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py`
`.venv/bin/ruff check stableagent/integrations/github.py stableagent/supervisor/source.py tests/unit/test_github_source.py`
`.venv/bin/ruff format --check stableagent/integrations/github.py stableagent/supervisor/source.py tests/unit/test_github_source.py`
`.venv/bin/mypy`

Expected: 5 passed；`All checks passed!`；格式已排列；mypy 无新增错误。

- [ ] **Step 6: 提交**

```bash
git add stableagent/integrations/github.py stableagent/supervisor/source.py \
  tests/unit/test_github_source.py
git commit -m "feat: add github read client seam and source contracts"
```

### Task 2：Issue / PR 标准化与 bot 过滤（M2-2）

**Files:**
- Modify: `stableagent/supervisor/source.py`
- Test: `tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: Task 1 的 `GitHubReadItem` / `stable_trigger_id` / thread key 函数；
  `SupervisorStore`、`Clock`（`stableagent.orchestration.model`）。
- Produces:
  - `GitHubSource(store, client, *, repository, bot_login, clock)`
  - `GitHubSource.normalize(item) -> SourceEvent | None`（`None` 表示自身 bot 事件，被过滤）
  - Trigger `external_id = github:{repository}:{kind}:{subject}`，`id = tg-<sha256 前 12>`

- [ ] **Step 1: 追加失败测试**

测试文件导入补充：

```python
import pathlib

from stableagent.supervisor.model import ThreadKind, ThreadStatus, TriggerKind
from stableagent.supervisor.source import GitHubSource
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock
```

```python
def _source(tmp_path: pathlib.Path):
    clock = FixedClock()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    client = FakeGitHubReadClient([])
    source = GitHubSource(
        store, client, repository="owner/repo", bot_login="sa-agent[bot]", clock=clock
    )
    return source, store, clock


def _read_item(
    kind: GitHubReadKind,
    resource: dict[str, object],
    *,
    occurred_at: int = 10,
    deleted: bool = False,
) -> GitHubReadItem:
    return GitHubReadItem(
        kind=kind, occurred_at=occurred_at, resource=resource, deleted=deleted
    )


def test_issue_normalizes_to_trigger_thread_and_digest(tmp_path: pathlib.Path) -> None:
    source, _, clock = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 7, "state": "open", "user": {"login": "octocat"}},
        )
    )
    assert event is not None
    assert event.thread.key == "issue:owner/repo#7"
    assert event.thread.external_ref == "#7"
    assert event.thread.kind is ThreadKind.ISSUE
    assert event.thread.status is ThreadStatus.OPEN
    assert event.channel is None
    assert event.trigger.kind is TriggerKind.ISSUE
    assert event.trigger.external_id == "github:owner/repo:issue:7"
    assert event.trigger.id.startswith("tg-")
    assert event.trigger.thread_keys == ["issue:owner/repo#7"]
    assert event.trigger.actor == "octocat"
    assert event.trigger.occurred_at == 10
    assert event.trigger.received_at == clock.now_ms()
    assert event.trigger.payload == {
        "resource": {"number": 7, "state": "open", "user": {"login": "octocat"}},
        "deleted": False,
    }
    assert event.trigger.digest


def test_closed_pull_request_maps_to_a_closed_pr_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.PULL_REQUEST,
            {"number": 9, "state": "closed", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.thread.status is ThreadStatus.CLOSED
    assert event.trigger.kind is TriggerKind.PULL_REQUEST
    assert event.trigger.external_id == "github:owner/repo:pull_request:9"


def test_bot_identity_is_filtered_case_insensitively(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 7, "state": "open", "user": {"login": "SA-Agent[BOT]"}},
        )
    )
    assert event is None


@pytest.mark.parametrize(
    "resource",
    [
        {"state": "open", "user": {"login": "alice"}},
        {"number": "7", "state": "open", "user": {"login": "alice"}},
        {"number": 7, "state": "open"},
    ],
)
def test_malformed_issue_is_a_schema_error(
    tmp_path: pathlib.Path, resource: dict[str, object]
) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError):
        source.normalize(_read_item(GitHubReadKind.ISSUE, resource))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py -k "issue or pull_request or bot"`

Expected: FAIL —— `ImportError: cannot import name 'GitHubSource'`。

- [ ] **Step 3: 扩展导入**

```python
from stableagent.integrations.github import GitHubReadClient, GitHubReadItem
from stableagent.orchestration.model import Clock
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore
```

- [ ] **Step 4: 追加 `GitHubSource` 与字段校验辅助函数**

```python
class GitHubSource:
    """单仓库只读 Source：`normalize` 无副作用；写库只发生在 `poll`。"""

    def __init__(
        self,
        store: SupervisorStore,
        client: GitHubReadClient,
        *,
        repository: str,
        bot_login: str,
        clock: Clock,
    ) -> None:
        self._store = store
        self._client = client
        self._repository = repository
        self._bot_login = bot_login
        self._clock = clock

    def normalize(self, item: GitHubReadItem) -> SourceEvent | None:
        kind = TriggerKind(item.kind.value)
        if kind is TriggerKind.ISSUE:
            return self._issue(item)
        if kind is TriggerKind.PULL_REQUEST:
            return self._pull_request(item)
        raise SourceSchemaError(f"unsupported kind {item.kind.value}")

    def _issue(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        number = _int_field(resource, "number")
        key = issue_thread_key(self._repository, number)
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.ISSUE, number, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.ISSUE,
                external_ref=f"#{number}",
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
        )

    def _pull_request(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        number = _int_field(resource, "number")
        key = pull_request_thread_key(self._repository, number)
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.PULL_REQUEST, number, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.PULL_REQUEST,
                external_ref=f"#{number}",
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
        )

    def _trigger(
        self,
        item: GitHubReadItem,
        kind: TriggerKind,
        subject: int,
        actor: str,
        thread_key: str,
    ) -> Trigger:
        external_id = f"github:{self._repository}:{kind.value}:{subject}"
        return Trigger(
            id=stable_trigger_id(external_id),
            external_id=external_id,
            kind=kind,
            actor=actor,
            thread_keys=[thread_key],
            occurred_at=item.occurred_at,
            received_at=self._clock.now_ms(),
            payload={"resource": item.resource, "deleted": item.deleted},
        )

    def _is_self(self, actor: str) -> bool:
        return actor.casefold() == self._bot_login.casefold()


def _int_field(resource: dict[str, Any], field: str) -> int:
    value = resource.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SourceSchemaError(f"{field} 必须是整数")
    return value


def _actor(resource: dict[str, Any]) -> str:
    user = resource.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    if isinstance(login, str) and login:
        return login
    actor = resource.get("actor")
    if isinstance(actor, str) and actor:
        return actor
    raise SourceSchemaError("缺少 actor / user.login")


def _thread_status(
    item: GitHubReadItem, resource: dict[str, Any], *, closed_state: str = "closed"
) -> ThreadStatus:
    if item.deleted or resource.get("state") == closed_state:
        return ThreadStatus.CLOSED
    return ThreadStatus.OPEN
```

- [ ] **Step 5: 跑测试与静态检查**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py`
`.venv/bin/ruff check stableagent/supervisor/source.py tests/unit/test_github_source.py`
`.venv/bin/mypy`

Expected: 11 passed；ruff/mypy 无新增问题（Task 2 新增 6 个用例：3 个主路径 + 3 个参数化非法载荷）。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/source.py tests/unit/test_github_source.py
git commit -m "feat: normalize github issues and pull requests into supervisor threads"
```

### Task 3：Comment / Review 映射与 Channel（M2-3）

**Files:**
- Modify: `stableagent/supervisor/source.py`
- Test: `tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: Task 2 的 `_actor` / `_int_field` / `_thread_status` / `_trigger`。
- Produces:
  - issue comment → issue 或 PR thread；`ChannelKind.ISSUE_COMMENT` / `PULL_REQUEST_COMMENT`，
    `external_ref = "#{number}"`
  - review comment / review → review thread `review:{repo}#{pull}#{review_id}`；
    `ChannelKind.REVIEW_REPLY`，`external_ref = "review:{review_id}"`
  - review `state == "dismissed"` 或删除事件把 review thread 置 `closed`

- [ ] **Step 1: 追加失败测试**

```python
def test_issue_comment_maps_to_issue_thread_and_channel(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 11, "issue_number": 7, "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "issue:owner/repo#7"
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.ISSUE_COMMENT
    assert event.channel.external_ref == "#7"
    assert event.channel.thread_key == event.thread.key
    assert event.trigger.kind is TriggerKind.ISSUE_COMMENT
    assert event.trigger.external_id == "github:owner/repo:issue_comment:11"


def test_comment_on_pull_request_uses_the_pr_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {
                "id": 12,
                "issue_number": 9,
                "is_pull_request": True,
                "user": {"login": "alice"},
            },
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.PULL_REQUEST_COMMENT


def test_review_comment_creates_review_thread_and_reply_channel(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW_COMMENT,
            {"id": 13, "pull_number": 9, "review_id": 42, "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "review:owner/repo#9:42"
    assert event.thread.kind is ThreadKind.REVIEW
    assert event.thread.external_ref == "review:42"
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.REVIEW_REPLY
    assert event.channel.external_ref == "review:42"
    assert event.trigger.kind is TriggerKind.REVIEW_COMMENT
    assert event.trigger.external_id == "github:owner/repo:review_comment:13"


def test_review_submission_maps_to_an_open_review_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW,
            {"id": 42, "pull_number": 9, "state": "submitted", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.kind is ThreadKind.REVIEW
    assert event.thread.status is ThreadStatus.OPEN
    assert event.trigger.kind is TriggerKind.REVIEW
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.REVIEW_REPLY


def test_dismissed_review_closes_the_review_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW,
            {"id": 42, "pull_number": 9, "state": "dismissed", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.status is ThreadStatus.CLOSED


def test_bot_review_comment_is_not_a_candidate(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW_COMMENT,
            {
                "id": 13,
                "pull_number": 9,
                "review_id": 42,
                "user": {"login": "sa-agent[bot]"},
            },
        )
    )
    assert event is None


def test_review_comment_without_review_id_is_a_schema_error(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError, match="review_id"):
        source.normalize(
            _read_item(
                GitHubReadKind.REVIEW_COMMENT,
                {"id": 13, "pull_number": 9, "user": {"login": "alice"}},
            )
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py -k "comment or review"`

Expected: FAIL —— `SourceSchemaError: unsupported kind issue_comment`（其余 kind 也会失败）。

- [ ] **Step 3: 加入分发分支**

```python
        if kind is TriggerKind.ISSUE_COMMENT:
            return self._issue_comment(item)
        if kind is TriggerKind.REVIEW_COMMENT:
            return self._review_comment(item)
        if kind is TriggerKind.REVIEW:
            return self._review(item)
```

- [ ] **Step 4: 追加三个映射方法**

```python
    def _issue_comment(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        comment_id = _int_field(resource, "id")
        number = _int_field(resource, "issue_number")
        if bool(resource.get("is_pull_request")):
            key = pull_request_thread_key(self._repository, number)
            thread_kind = ThreadKind.PULL_REQUEST
            channel_kind = ChannelKind.PULL_REQUEST_COMMENT
        else:
            key = issue_thread_key(self._repository, number)
            thread_kind = ThreadKind.ISSUE
            channel_kind = ChannelKind.ISSUE_COMMENT
        external_ref = f"#{number}"
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.ISSUE_COMMENT, comment_id, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=thread_kind,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
            channel=Channel(
                id=stable_channel_id(key, channel_kind, external_ref),
                thread_key=key,
                kind=channel_kind,
                external_ref=external_ref,
            ),
        )

    def _review_comment(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        comment_id = _int_field(resource, "id")
        pull_number = _int_field(resource, "pull_number")
        review_id = _int_field(resource, "review_id")
        key = review_thread_key(self._repository, pull_number, review_id)
        external_ref = f"review:{review_id}"
        return SourceEvent(
            trigger=self._trigger(
                item, TriggerKind.REVIEW_COMMENT, comment_id, actor, key
            ),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REVIEW,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
            channel=Channel(
                id=stable_channel_id(key, ChannelKind.REVIEW_REPLY, external_ref),
                thread_key=key,
                kind=ChannelKind.REVIEW_REPLY,
                external_ref=external_ref,
            ),
        )

    def _review(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        review_id = _int_field(resource, "id")
        pull_number = _int_field(resource, "pull_number")
        key = review_thread_key(self._repository, pull_number, review_id)
        external_ref = f"review:{review_id}"
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.REVIEW, review_id, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REVIEW,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource, closed_state="dismissed"),
            ),
            channel=Channel(
                id=stable_channel_id(key, ChannelKind.REVIEW_REPLY, external_ref),
                thread_key=key,
                kind=ChannelKind.REVIEW_REPLY,
                external_ref=external_ref,
            ),
        )
```

- [ ] **Step 5: 跑测试与静态检查**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py`
`.venv/bin/ruff check stableagent/supervisor/source.py tests/unit/test_github_source.py`
`.venv/bin/mypy`

Expected: 18 passed；ruff/mypy 无新增问题（Task 3 新增 7 个用例）。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/source.py tests/unit/test_github_source.py
git commit -m "feat: map github comments and reviews to threads and reply channels"
```

### Task 4：CI status 与 repository Thread（M2-4）

**Files:**
- Modify: `stableagent/supervisor/model.py`
- Modify: `stableagent/supervisor/source.py`
- Test: `tests/unit/test_supervisor_model.py`、`tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: 现有 `ThreadKind`、`repository_thread_key`。
- Produces: `ThreadKind.REPOSITORY = "repository"`；CI 有 `pull_number` 时挂
  `pr:{repo}#{n}`，否则挂 `repo:{repo}`；CI 的 `actor` 取 `resource["actor"]`，缺省 `"github"`。
  CI 事件不创建 Channel（没有回写目的地）。

- [ ] **Step 1: 追加失败测试**

`tests/unit/test_supervisor_model.py` 追加：

```python
def test_thread_kinds_are_pinned() -> None:
    assert {k.value for k in ThreadKind} == {
        "issue",
        "pull_request",
        "review",
        "repository",
    }
```

`tests/unit/test_github_source.py` 追加：

```python
def test_ci_status_with_pull_request_attaches_to_the_pr_thread(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.CI_STATUS,
            {"id": 501, "sha": "abc123", "state": "success", "pull_number": 9},
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.trigger.kind is TriggerKind.CI_STATUS
    assert event.trigger.external_id == "github:owner/repo:ci_status:501"
    assert event.trigger.actor == "github"
    assert event.channel is None


def test_ci_status_without_pull_request_uses_the_repository_thread(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.CI_STATUS,
            {"id": 502, "sha": "abc123", "state": "failure"},
        )
    )
    assert event is not None
    assert event.thread.key == "repo:owner/repo"
    assert event.thread.kind is ThreadKind.REPOSITORY
    assert event.thread.external_ref == "owner/repo"
    assert event.channel is None


def test_unknown_ci_state_is_a_schema_error(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError):
        source.normalize(
            _read_item(
                GitHubReadKind.CI_STATUS,
                {"id": 503, "sha": "abc123", "state": "exploded"},
            )
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_model.py tests/unit/test_github_source.py -k "thread_kinds or ci_status"`

Expected: FAIL —— `ThreadKind.REPOSITORY` 不存在；`ci_status` 报 `unsupported kind`。

- [ ] **Step 3: 扩展枚举**

```python
class ThreadKind(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    REVIEW = "review"
    REPOSITORY = "repository"
```

- [ ] **Step 4: 加入 CI 分发与映射**

分发分支：

```python
        if kind is TriggerKind.CI_STATUS:
            return self._ci_status(item)
```

```python
    def _ci_status(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = resource.get("actor", "github")
        if not isinstance(actor, str) or not actor:
            raise SourceSchemaError("actor 必须是非空字符串")
        if self._is_self(actor):
            return None
        status_id = _int_field(resource, "id")
        _str_field(resource, "sha")
        state = _str_field(resource, "state")
        if state not in {"pending", "success", "failure", "error"}:
            raise SourceSchemaError(f"未知 CI state：{state}")
        pull_number = resource.get("pull_number")
        if pull_number is not None and (
            not isinstance(pull_number, int) or isinstance(pull_number, bool)
        ):
            raise SourceSchemaError("pull_number 必须是整数或 null")
        if pull_number is not None:
            key = pull_request_thread_key(self._repository, pull_number)
            thread = Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.PULL_REQUEST,
                external_ref=f"#{pull_number}",
                last_trigger_at=item.occurred_at,
                status=ThreadStatus.OPEN,
            )
        else:
            key = repository_thread_key(self._repository)
            thread = Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REPOSITORY,
                external_ref=self._repository,
                last_trigger_at=item.occurred_at,
                status=ThreadStatus.OPEN,
            )
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.CI_STATUS, status_id, actor, key),
            thread=thread,
        )
```

新增辅助函数：

```python
def _str_field(resource: dict[str, Any], field: str) -> str:
    value = resource.get(field)
    if not isinstance(value, str) or not value:
        raise SourceSchemaError(f"{field} 必须是非空字符串")
    return value
```

- [ ] **Step 5: 跑测试与静态检查**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_model.py tests/unit/test_github_source.py`
`.venv/bin/ruff check stableagent/supervisor tests/unit/test_github_source.py`
`.venv/bin/mypy`

Expected: model 6 passed、source 21 passed；ruff/mypy 无新增问题。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/model.py stableagent/supervisor/source.py \
  tests/unit/test_supervisor_model.py tests/unit/test_github_source.py
git commit -m "feat: map ci status to pr or repository threads"
```

### Task 5：poll 分页、排序、去重、删除与错误健康（M2-5）

**Files:**
- Modify: `stableagent/supervisor/source.py`
- Modify: `tests/unit/test_control_boundaries.py`
- Test: `tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: `SupervisorStore.{get_source_cursor, insert_trigger, upsert_thread, insert_channel,
  put_source_cursor}`、`SourceCursor` / `SourceHealth`。
- Produces: `GitHubSource.poll(*, limit=100, max_pages=10) -> PollBatch`，满足 `Source`：
  - 每轮最多 `max_pages` 页；未读完时把 `next_cursor` 持久化，下一轮从该 cursor 继续
  - 批内按 `external_id` 去重，输出按 `(occurred_at, external_id)` 升序
  - 与库内 `UNIQUE(source, external_id)` 重复的事件不返回，计入 `duplicates`
  - Trigger / Thread / Channel / cursor 在**同一个事务**内写
  - `SourceTransientError` → cursor `degraded`；其余 `SourceError` → `failed`；都先落健康再抛出

- [ ] **Step 1: 追加失败测试**

导入补充：

```python
import contextlib

from stableagent.supervisor.model import SourceHealth
from stableagent.supervisor.source import Source
```

```python
def _poll_source(
    tmp_path: pathlib.Path,
    items: list[GitHubReadItem],
    *,
    page_size: int | None = None,
):
    clock = FixedClock()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    client = FakeGitHubReadClient(items, page_size=page_size)
    source = GitHubSource(
        store, client, repository="owner/repo", bot_login="sa-agent[bot]", clock=clock
    )
    return source, store, client


async def test_poll_pages_sorts_dedupes_and_writes_in_one_transaction(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 2, "state": "open", "user": {"login": "alice"}},
            occurred_at=200,
        ),
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
            occurred_at=100,
        ),
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 2, "state": "open", "user": {"login": "alice"}},
            occurred_at=200,
        ),
    ]
    source, store, client = _poll_source(tmp_path, items, page_size=2)
    assert isinstance(source, Source)

    batch = await source.poll(limit=10)

    assert [event.thread.external_ref for event in batch.events] == ["#1", "#2"]
    assert batch.duplicates == 1
    assert batch.next_cursor is None
    assert [call["cursor"] for call in client.calls] == [None, "offset:2"]
    assert store.get_trigger_by_external("github", "github:owner/repo:issue:1") is not None
    cursor = store.get_source_cursor("github")
    assert cursor is not None
    assert cursor.cursor is None
    assert cursor.health is SourceHealth.OK
    assert cursor.consecutive_failures == 0


async def test_second_poll_skips_triggers_already_in_the_store(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
        )
    ]
    source, _, _ = _poll_source(tmp_path, items)
    assert len((await source.poll(limit=10)).events) == 1

    again = await source.poll(limit=10)

    assert again.events == []
    assert again.duplicates == 1


async def test_poll_stops_at_max_pages_and_resumes_from_cursor(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": number, "state": "open", "user": {"login": "alice"}},
            occurred_at=number,
        )
        for number in (1, 2, 3)
    ]
    source, store, _ = _poll_source(tmp_path, items, page_size=1)

    first = await source.poll(limit=10, max_pages=1)
    assert [event.thread.external_ref for event in first.events] == ["#1"]
    assert first.next_cursor == "offset:1"
    stored = store.get_source_cursor("github")
    assert stored is not None and stored.cursor == "offset:1"

    second = await source.poll(limit=10, max_pages=2)
    assert [event.thread.external_ref for event in second.events] == ["#2", "#3"]
    assert second.next_cursor is None
    stored = store.get_source_cursor("github")
    assert stored is not None and stored.cursor is None


@pytest.mark.parametrize(
    ("error", "health"),
    [
        (SourceTransientError("503"), SourceHealth.DEGRADED),
        (SourceAuthError("403"), SourceHealth.FAILED),
        (SourceGoneError("404"), SourceHealth.FAILED),
        (SourceSchemaError("bad payload"), SourceHealth.FAILED),
    ],
)
async def test_poll_records_cursor_health_before_reraising(
    tmp_path: pathlib.Path, error: Exception, health: SourceHealth
) -> None:
    source, store, client = _poll_source(tmp_path, [])
    client.fail_next = error

    with pytest.raises(type(error)):
        await source.poll(limit=10)

    cursor = store.get_source_cursor("github")
    assert cursor is not None
    assert cursor.health is health
    assert cursor.consecutive_failures == 1


async def test_malformed_item_fails_the_whole_batch_without_writing(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
        ),
        _read_item(GitHubReadKind.ISSUE, {"state": "open", "user": {"login": "alice"}}),
    ]
    source, store, _ = _poll_source(tmp_path, items)

    with pytest.raises(SourceSchemaError):
        await source.poll(limit=10)

    assert store.list_triggers() == []
    assert store.list_threads() == []
    cursor = store.get_source_cursor("github")
    assert cursor is not None and cursor.health is SourceHealth.FAILED


async def test_deleted_event_closes_the_thread_and_still_records_the_trigger(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
            deleted=True,
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)

    batch = await source.poll(limit=10)

    assert len(batch.events) == 1
    assert batch.events[0].thread.status is ThreadStatus.CLOSED
    thread = store.get_thread("issue:owner/repo#1")
    assert thread is not None and thread.status is ThreadStatus.CLOSED


async def test_poll_drops_our_own_bot_events_but_still_advances(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 1, "issue_number": 7, "user": {"login": "sa-agent[bot]"}},
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)

    batch = await source.poll(limit=10)

    assert batch.events == []
    assert batch.duplicates == 0
    assert store.get_source_cursor("github") is not None


async def test_poll_writes_state_in_a_single_transaction(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 1, "issue_number": 7, "user": {"login": "alice"}},
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)
    seen: list[bool] = []
    original = store.transaction

    @contextlib.contextmanager
    def watched():
        with original():
            seen.append(store.in_transaction)
            yield

    monkeypatch.setattr(store, "transaction", watched)

    await source.poll(limit=10)

    assert seen == [True]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py -k poll`

Expected: FAIL —— `AttributeError: 'GitHubSource' object has no attribute 'poll'`。

- [ ] **Step 3: 导入 cursor 记录**

```python
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    SourceCursor,
    SourceHealth,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
```

- [ ] **Step 4: 在 `GitHubSource` 内追加 `poll` / `_fetch` / `_persist` / `_dedupe` / `_record_failure`**

L3 静态检查（`tests/unit/test_execution_boundaries.py`）禁止在同一函数体内同时出现
`await` 与 `Store.transaction()`：`poll` 只做编排，`_fetch` 只做异步读，`_persist`
只做同步事务写，`_record_failure` 自己持有一个事务。

```python
    async def poll(self, *, limit: int = 100, max_pages: int = 10) -> PollBatch:
        stored = self._store.get_source_cursor("github")
        cursor = stored.cursor if stored else None
        try:
            events, next_cursor, in_batch_duplicates = await self._fetch(cursor, limit, max_pages)
        except SourceError as error:
            self._record_failure(stored, error)
            raise
        return self._persist(events, next_cursor, in_batch_duplicates)

    async def _fetch(
        self, cursor: str | None, limit: int, max_pages: int
    ) -> tuple[list[SourceEvent], str | None, int]:
        next_cursor = cursor
        items: list[GitHubReadItem] = []
        for _ in range(max_pages):
            page = await self._client.list_events(
                repository=self._repository, cursor=next_cursor, limit=limit
            )
            items.extend(page.items)
            if page.next_cursor is not None and page.next_cursor == next_cursor:
                raise SourceSchemaError("cursor 未推进")
            next_cursor = page.next_cursor
            if next_cursor is None:
                break
        events, duplicates = self._dedupe(items)
        return events, next_cursor, duplicates

    def _persist(
        self, events: list[SourceEvent], next_cursor: str | None, in_batch_duplicates: int
    ) -> PollBatch:
        with self._store.transaction():
            fresh: list[SourceEvent] = []
            duplicates = 0
            for event in events:
                if not self._store.insert_trigger(event.trigger):
                    duplicates += 1
                    continue
                self._store.upsert_thread(event.thread)
                if event.channel is not None:
                    self._store.insert_channel(event.channel)
                fresh.append(event)
            self._store.put_source_cursor(
                SourceCursor(
                    source="github",
                    cursor=next_cursor,
                    last_success_at=self._clock.now_ms(),
                    consecutive_failures=0,
                    health=SourceHealth.OK,
                )
            )
        return PollBatch(
            events=fresh,
            next_cursor=next_cursor,
            duplicates=duplicates + in_batch_duplicates,
        )

    def _dedupe(self, items: list[GitHubReadItem]) -> tuple[list[SourceEvent], int]:
        by_external: dict[str, SourceEvent] = {}
        duplicates = 0
        for item in items:
            event = self.normalize(item)
            if event is None:
                continue
            if event.trigger.external_id in by_external:
                duplicates += 1
                continue
            by_external[event.trigger.external_id] = event
        ordered = sorted(
            by_external.values(),
            key=lambda event: (event.trigger.occurred_at, event.trigger.external_id),
        )
        return ordered, duplicates

    def _record_failure(self, stored: SourceCursor | None, error: SourceError) -> None:
        health = (
            SourceHealth.DEGRADED
            if isinstance(error, SourceTransientError)
            else SourceHealth.FAILED
        )
        previous = stored.consecutive_failures if stored else 0
        with self._store.transaction():
            self._store.put_source_cursor(
                SourceCursor(
                    source="github",
                    cursor=stored.cursor if stored else None,
                    last_success_at=stored.last_success_at if stored else None,
                    consecutive_failures=previous + 1,
                    health=health,
                )
            )
```

- [ ] **Step 5: 调整 P6 方向锁测试**

`supervisor` 是 P6 新顶层，按 ADR-06 合法导入 `integrations`；P5 的"旧包不得导入
P5 层"用例必须把 P6 排除，同时补一条反向断言（旧包不得导入 supervisor）。

```python
P6_PACKAGES = ("supervisor",)


def _legacy_packages() -> list[str]:
    """ROOT 下除 P5 三新包与 P6 supervisor 以外的全部包目录。"""
    excluded = {*P5_PACKAGES, *P6_PACKAGES, "__pycache__"}
    return sorted(
        path.name for path in ROOT.iterdir() if path.is_dir() and path.name not in excluded
    )


def test_no_package_imports_supervisor() -> None:
    """L2 反向：supervisor 在最上游，旧包与 P5 包都不许 import 它。"""
    offenders: list[tuple[str, list[str]]] = []
    for module in _modules(*(_legacy_packages() + list(P5_PACKAGES))):
        banned = sorted(
            name for name in _imports_of(module) if name.startswith("stableagent.supervisor")
        )
        if banned:
            offenders.append((_label(module), banned))
    assert not offenders
```

- [ ] **Step 6: 跑测试与静态检查**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py tests/unit/test_control_boundaries.py tests/unit/test_execution_boundaries.py`
`.venv/bin/ruff check stableagent/supervisor/source.py tests/unit/test_github_source.py tests/unit/test_control_boundaries.py`
`.venv/bin/ruff format --check stableagent/supervisor/source.py tests/unit/test_github_source.py tests/unit/test_control_boundaries.py`
`.venv/bin/mypy`

Expected: 全绿（source 32 + 两个边界文件共 45）；ruff/mypy 无新增问题。

- [ ] **Step 7: 提交**

```bash
git add stableagent/supervisor/source.py tests/unit/test_github_source.py tests/unit/test_control_boundaries.py
git commit -m "feat: poll github with cursors dedupe and source health"
```

### Task 6：CI FOCUS 28 → 29（M2-7）

**Files:**
- Modify: `tests/unit/test_ci_focus_gate.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `supervisor/source.py`（Task 1~5 完成）。
- Produces: 心脏闸门 29 文件；契约测试同时钉住数量、步骤名与"禁止通配兜底"。

- [ ] **Step 1: 先改契约测试（红）**

`FOCUS_FILES` 末尾追加 `"supervisor/source.py",`；测试函数与断言改为：

```python
def test_focus_covers_the_twenty_nine_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 29
    assert "29 文件" in text  # 步骤名与注释同步
    assert text.count('".py"') == 0  # 不许用通配兜底
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py > /tmp/p6-m2-focus-red.txt 2>&1`

Read: `/tmp/p6-m2-focus-red.txt`

Expected: FAIL —— `"supervisor/source.py"` 不在工作流中（第 2 行断言）。

- [ ] **Step 3: 同步工作流**

步骤名改为：

```yaml
      - name: 心脏模块覆盖率闸门（29 文件：loop/checkpoint/errors/context/evidence/orchestration/control/execution/integrations/supervisor 各 ≥90%）
```

`FOCUS` 元组在 `"supervisor/views.py",` 之后追加：

```python
              "supervisor/source.py",
```

- [ ] **Step 4: 跑契约测试确认通过**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py > /tmp/p6-m2-focus-green.txt 2>&1`

Read: `/tmp/p6-m2-focus-green.txt`

Expected: `1 passed`。

- [ ] **Step 5: 全量测试并实测 `source.py` 覆盖率**

```bash
.venv/bin/pytest -q --cov=stableagent --cov-report=xml > /tmp/p6-m2-pytest.txt 2>&1
.venv/bin/python - <<'PY' > /tmp/p6-m2-coverage.txt
import xml.etree.ElementTree as ET

root = ET.parse("coverage.xml").getroot()
for cls in root.iter("class"):
    if cls.get("filename") == "supervisor/source.py":
        print("supervisor/source.py", cls.get("line-rate"))
PY
```

Read: `/tmp/p6-m2-pytest.txt`、`/tmp/p6-m2-coverage.txt`

Expected: `0 failed`；`supervisor/source.py` 行覆盖率 ≥ `0.9`（CI 闸门口径）。

本机提示：若 shell 的 `NO_PROXY` 含 `[::1]`，macOS `getproxies()` 会让 httpx 把
`no` 当代理 scheme（`Invalid port: ':1]'`）。跑全量前用
`env -u NO_PROXY -u no_proxy` 包一层即可；该失败与仓库代码无关。

- [ ] **Step 6: 提交**

```bash
git add tests/unit/test_ci_focus_gate.py .github/workflows/ci.yml
git commit -m "ci: include supervisor source in the cardiac coverage gate"
```

### Task 7：Source 部署文档与 M2 出口（M2-6 + M2-8）

**Files:**
- Create: `docs/guides/supervisor-github-source.md`
- Modify: `docs/acceptance/p6-supervisor-beta.md`、`README.md`、`docs/README.md`、
  `docs/acceptance/README.md`、`docs/Roadmap.md`、`CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1~6 的全部产物。
- Produces: 可操作的 polling 部署说明；M2 出口证据；状态面同步。

- [ ] **Step 1: 写部署与 cursor 契约文档**

`docs/guides/supervisor-github-source.md` 全文：

```markdown
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
```

- [ ] **Step 2: 全量门禁（读数落盘后读回）**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m2-full.txt 2>&1
.venv/bin/ruff check stableagent tests examples benchmarks > /tmp/p6-m2-ruff.txt 2>&1
.venv/bin/ruff format --check stableagent tests examples benchmarks > /tmp/p6-m2-format.txt 2>&1
.venv/bin/mypy > /tmp/p6-m2-mypy.txt 2>&1
```

Read: 四个 `/tmp/p6-m2-*.txt`

Expected: `0 failed`；`All checks passed!`；`... files already formatted`；
`Success: no issues found`。

- [ ] **Step 3: 三解释器等价**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m2-py310.txt 2>&1
env -u NO_PROXY -u no_proxy .matrix/py311/bin/pytest -q > /tmp/p6-m2-py311.txt 2>&1
env -u NO_PROXY -u no_proxy .matrix/py312/bin/pytest -q > /tmp/p6-m2-py312.txt 2>&1
```

Read: 三份 `/tmp/p6-m2-py31*.txt`

Expected: 三份末行均为 `0 failed`，passed 数一致（跳过数可不同）。

- [ ] **Step 4: 边界复核**

```bash
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m2-l1.txt
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py > /tmp/p6-m2-c1.txt
grep -rnE "(from|import) +stableagent\.supervisor" --include='*.py' \
  stableagent/core stableagent/orchestration stableagent/control \
  stableagent/execution stableagent/integrations > /tmp/p6-m2-imports.txt
git diff --check > /tmp/p6-m2-check.txt 2>&1
```

Read: 四份 `/tmp/p6-m2-*.txt`

Expected: 前两份 0 行；`grep` 无命中；`git diff --check` 无输出。

- [ ] **Step 5: 回填账本**

1. 第 1 节 M2-1~M2-8 改为 `[x]`，每条追加：提交号、测试文件与用例数、覆盖率读数、
   三解释器结论。
2. 第 4 节补一句："M2 展开为 **7 个 Task / 43 个 Step**（见 §7）。"
3. 第 1 节 M2 状态行改为："**状态：M2 收口（Task 1~7 全部完成）。** 证据提交：…"

- [ ] **Step 6: 同步状态面**

- `README.md` P6 行：`🟡 active — M2 done (8/8); milestones 2/6`。
- `docs/README.md`：当前主线改为 P6 M2 完成；阶段表 P6 行改为
  `进行中（M2 完成，8/8；里程碑 2/6）`；链接本账本与
  `docs/guides/supervisor-github-source.md`。
- `docs/acceptance/README.md`：P6 行 `Active（M2 完成 8/8；里程碑 2/6）`。
- `docs/Roadmap.md`：P6 段写明 M2 完成与下一步 M3（Router Intake）。
- `CHANGELOG.md`：新增 P6 M2 条目（只读 polling、事件映射、去重、cursor 健康、
  FOCUS 29）。

- [ ] **Step 7: 提交**

```bash
git add docs/guides/supervisor-github-source.md docs/acceptance/p6-supervisor-beta.md \
  README.md docs/README.md docs/acceptance/README.md docs/Roadmap.md CHANGELOG.md
git commit -m "docs: close p6 m2 with the github source evidence"
```

## 8. M2 自检记录（writing-plans）

- 规格覆盖：WP-6.2 第 1 条 → Task 1（`Source` / `ChannelWriter`）；第 2 条 →
  Task 2~4（六种 kind 的 fixture 与映射）；第 3 条 → Task 5 + Task 7 文档（polling 主路径、
  权限、webhook 后续接法）；第 4 条 → Task 5（cursor/分页/错误/删除/乱序/重复）；
  第 5 条 → Task 2~3 与 Task 5 的 bot 过滤用例。
- 类型一致性：`GitHubReadItem` / `GitHubReadPage` / `PollBatch` / `SourceEvent` 在 Task 1
  定义，Task 2~5 只使用不重定义；`review_thread_key` 从 Task 1 起就是三参数
  `(repository, pull_number, review_id)`；`GitHubSource.poll` 的 `max_pages` 只在 Task 5 出现。
- 无占位符：所有实现步骤给出完整代码；出口读数在 Task 7 执行时落盘后回填，不预填数字。
- 边界：integrations 侧只新增 `GitHubReadKind`，不 import supervisor（L2）；事务体内无
  `await`（L3）；Trigger payload 只进 digest 与持久化，不参与判定（L4）。

## 9. M3 任务级实施计划（writing-plans 产出，2026-11-09）

**Goal：** 让 `RouterService.receive(trigger)` 成为 Trigger → Subscription 扇出 →
Decision/Ticket 的唯一入口，并在 `observe` 锁定下完成六种 outcome 与七条命令。

**Architecture：** `supervisor/assessor.py` 只做确定性候选判断；`supervisor/router.py`
在一个 `BEGIN IMMEDIATE` 内完成去重、扇出与决策；`supervisor/service.py` 承载 operator
命令。Ticket/Assignment/Effect 的写入沿用 P5 `GitHubIntake` 的"五件事实"口径，不调用
`OrchestrationService`（其命令自带事务，嵌套会触发 `ReentrantTransactionError`）。

**Spec：** [P6 实施规格](../specs/p6-single-repository-super-agent-beta.md) §2.1–2.3 与
WP-6.3；决策来源 [ADR-06](../design-decisions/06-super-agent-router.md) 决策 3–9。

**Global Constraints（每个 Task 都隐含遵守）：**

- L1：`stableagent/core/` 对 `234f620` 零 diff；约束 1：`orchestration/service.py` 零字符改动。
- L2：方向锁 `supervisor → integrations → execution → control → orchestration → core`；
  旧包与 P5 包不得 import `stableagent.supervisor`。
- L3：`Store.transaction()` 体内不得出现 `await`；`receive` / `reevaluate` / 命令各只开
  一个事务。
- L4：外部正文只进 digest、TicketInput 与 Decision reason，不决定 action 集合；action
  集合只来自 `RouterConfig` 与 `StaticPolicyConfig`。
- L5：`resume` 的 Mandate 复检用 `when="launch"` 并写审计行。
- L6：不新增 `merge` / `enable_auto_merge` 路径；`EffectKind` 不加成员。
- L8：全部用例离线；定向测试必须加 `--no-cov`（全局 `fail_under = 70`）。

---

### Task 1：AutonomyMode 与 observe 评估器（M3-1）

**Files:**
- Modify: `stableagent/supervisor/model.py`
- Create: `stableagent/supervisor/assessor.py`
- Test: `tests/unit/test_supervisor_assessor.py`

**Interfaces:**
- Consumes: `supervisor.model` 的 `Trigger` / `Thread` / `ThreadStatus` / `TriggerKind`。
- Produces: `AutonomyMode`（`off` / `observe` / `auto`）；`AssessmentContext`、
  `CandidateAssessment`、`CandidateAssessor`（`assess(context) -> CandidateAssessment`）、
  `ObserveOnlyAssessor`。Task 2~6 只使用这些名字，不重定义。

- [ ] **Step 1: 写失败测试**

```python
"""WP-6.3：observe 唯一评估器的确定性规则。"""

from __future__ import annotations

from stableagent.supervisor.assessor import (
    AssessmentContext,
    CandidateAssessor,
    ObserveOnlyAssessor,
)
from stableagent.supervisor.model import (
    AutonomyMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)


def _trigger(kind: TriggerKind = TriggerKind.ISSUE) -> Trigger:
    return Trigger(
        id="tg-1",
        external_id="github:o/r:issue:1",
        kind=kind,
        actor="alice",
        thread_keys=["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload={"resource": {"number": 1}, "deleted": False},
    )


def _thread(status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    return Thread(
        key="issue:o/r#1",
        repository="o/r",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=1,
        status=status,
    )


def _context(**overrides: object) -> AssessmentContext:
    values: dict[str, object] = {
        "trigger": _trigger(),
        "thread": _thread(),
        "policy_version": "p5-v1",
    }
    values.update(overrides)
    return AssessmentContext(**values)


def test_autonomy_mode_has_three_values() -> None:
    assert [mode.value for mode in AutonomyMode] == ["off", "observe", "auto"]


def test_observe_only_assessor_marks_a_fresh_human_candidate() -> None:
    assessment = ObserveOnlyAssessor().assess(_context())
    assert assessment.would_accept is True
    assert assessment.reason_codes == ["inferred_candidate"]


def test_observe_only_assessor_rejects_ci_as_context_only() -> None:
    assessment = ObserveOnlyAssessor().assess(_context(trigger=_trigger(TriggerKind.CI_STATUS)))
    assert assessment.would_accept is False
    assert assessment.reason_codes == ["ci_context_only"]


def test_observe_only_assessor_rejects_non_open_threads() -> None:
    assessment = ObserveOnlyAssessor().assess(_context(thread=_thread(ThreadStatus.CLOSED)))
    assert assessment.would_accept is False
    assert assessment.reason_codes == ["thread_not_open"]


def test_observe_only_assessor_rejects_existing_tickets_and_subscriptions() -> None:
    existing = ObserveOnlyAssessor().assess(_context(existing_ticket_id="tk-1"))
    owned = ObserveOnlyAssessor().assess(_context(has_live_own_subscription=True))
    context = ObserveOnlyAssessor().assess(_context(has_live_context_subscription=True))
    assert existing.reason_codes == ["existing_ticket_context"]
    assert owned.reason_codes == ["already_owned"]
    assert context.reason_codes == ["context_only"]
    assert not existing.would_accept and not owned.would_accept and not context.would_accept


def test_structural_fake_satisfies_the_candidate_assessor_protocol() -> None:
    class Stub:
        def assess(self, context: AssessmentContext):
            return ObserveOnlyAssessor().assess(context)

    assert isinstance(Stub(), CandidateAssessor)
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_assessor.py
```

Expected: collection error `ModuleNotFoundError: stableagent.supervisor.assessor`。

- [ ] **Step 3: 最小实现**

`stableagent/supervisor/model.py` 在 `SourceHealth` 之后追加：

```python
class AutonomyMode(str, Enum):
    """分级自治：Beta 只允许 off/observe；auto 仅保留接口（规格 2.3）。"""

    OFF = "off"
    OBSERVE = "observe"
    AUTO = "auto"
```

`stableagent/supervisor/assessor.py` 全文：

```python
"""推断候选评估：窄协议 + observe 下的确定性规则（规格 2.3、WP-6.3 第 3 条）。

评估器只回答"若开启 auto 是否接受"，不写库、不创建 Ticket；失败由 Router 记 unavailable。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from stableagent.supervisor.model import Thread, ThreadStatus, Trigger, TriggerKind


class AssessmentContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: Trigger
    thread: Thread | None = None
    has_live_own_subscription: bool = False
    has_live_context_subscription: bool = False
    existing_ticket_id: str | None = None
    policy_version: str


class CandidateAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    would_accept: bool
    reason_codes: list[str] = Field(default_factory=list)


@runtime_checkable
class CandidateAssessor(Protocol):
    def assess(self, context: AssessmentContext) -> CandidateAssessment: ...


class ObserveOnlyAssessor:
    """Beta 的确定性评估器；规则顺序即 reason code 的优先级。"""

    def assess(self, context: AssessmentContext) -> CandidateAssessment:
        if context.trigger.kind is TriggerKind.CI_STATUS:
            return CandidateAssessment(would_accept=False, reason_codes=["ci_context_only"])
        if context.thread is not None and context.thread.status is not ThreadStatus.OPEN:
            return CandidateAssessment(would_accept=False, reason_codes=["thread_not_open"])
        if context.existing_ticket_id is not None:
            return CandidateAssessment(
                would_accept=False, reason_codes=["existing_ticket_context"]
            )
        if context.has_live_own_subscription:
            return CandidateAssessment(would_accept=False, reason_codes=["already_owned"])
        if context.has_live_context_subscription:
            return CandidateAssessment(would_accept=False, reason_codes=["context_only"])
        return CandidateAssessment(would_accept=True, reason_codes=["inferred_candidate"])
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_assessor.py
.venv/bin/ruff check stableagent/supervisor/assessor.py stableagent/supervisor/model.py
.venv/bin/ruff format --check stableagent/supervisor/assessor.py
.venv/bin/mypy
```

Expected: `7 passed`；ruff `All checks passed!`；mypy `Success`。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/model.py stableagent/supervisor/assessor.py \
  tests/unit/test_supervisor_assessor.py
git commit -m "feat: add supervisor autonomy mode and observe-only assessor"
```

---

### Task 2：Source request 信号与决策 attempt 仓储（M3-2）

**Files:**
- Modify: `stableagent/supervisor/source.py`
- Modify: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_github_source.py`、`tests/unit/test_supervisor_store.py`

**Interfaces:**
- Consumes: `GitHubSource._trigger` 已有的 `bot_login`；`DecisionOutcome`。
- Produces: Trigger payload 的 `request = {"text": str, "mentioned": bool, "url": str | None}`；
  `SupervisorStore.next_decision_attempt(digest) -> int`；
  `SupervisorStore.find_accepted_decision(digest) -> Decision | None`。
- 兼容：M2 的 `payload == {...}` 断言使用无正文 fixture，payload 不变。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_github_source.py` 末尾追加：

```python
def test_comment_body_carries_a_normalized_request_signal(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {
                "id": 21,
                "issue_number": 7,
                "user": {"login": "alice"},
                "body": "please @SA-Agent take a look",
                "html_url": "https://github.com/owner/repo/issues/7#issuecomment-21",
            },
        )
    )
    assert event is not None
    assert event.trigger.payload["request"] == {
        "text": "please @SA-Agent take a look",
        "mentioned": True,
        "url": "https://github.com/owner/repo/issues/7#issuecomment-21",
    }


def test_plain_body_and_ci_status_have_no_false_mention(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    comment = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {
                "id": 22,
                "issue_number": 7,
                "user": {"login": "alice"},
                "body": "unrelated note",
            },
        )
    )
    ci = source.normalize(
        _read_item(
            GitHubReadKind.CI_STATUS,
            {"id": 501, "sha": "abc123", "state": "success", "pull_number": 9},
        )
    )
    assert comment is not None and ci is not None
    assert comment.trigger.payload["request"]["mentioned"] is False
    assert comment.trigger.payload["request"]["url"] is None
    assert "request" not in ci.trigger.payload
```

在 `tests/unit/test_supervisor_store.py` 末尾追加：

```python
def test_next_decision_attempt_counts_per_digest(store: SupervisorStore) -> None:
    assert store.next_decision_attempt("digest-a") == 1
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=[],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=["x"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=1,
            created_at=1,
        )
    )
    assert store.next_decision_attempt("digest-a") == 2
    assert store.next_decision_attempt("digest-b") == 1


def test_find_accepted_decision_skips_observe_rows(store: SupervisorStore) -> None:
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=[],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=["x"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=1,
            created_at=1,
        )
    )
    store.insert_decision(
        Decision(
            id="dc-2",
            trigger_ids=[],
            outcome=DecisionOutcome.ACCEPT,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=["admitted"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=2,
            created_at=2,
        )
    )
    found = store.find_accepted_decision("digest-a")
    assert found is not None
    assert found.id == "dc-2"
    assert store.find_accepted_decision("digest-missing") is None
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py \
  tests/unit/test_supervisor_store.py
```

Expected: 2 个用例因 `KeyError: 'request'` / `AttributeError: next_decision_attempt` 失败。

- [ ] **Step 3: 实现 Source request 块**

`stableagent/supervisor/source.py` 的 `_trigger` 改为：

```python
    def _trigger(
        self,
        item: GitHubReadItem,
        kind: TriggerKind,
        subject: int,
        actor: str,
        thread_key: str,
    ) -> Trigger:
        external_id = f"github:{self._repository}:{kind.value}:{subject}"
        payload: dict[str, Any] = {"resource": item.resource, "deleted": item.deleted}
        request = self._request_block(item.resource)
        if request is not None:
            payload["request"] = request
        return Trigger(
            id=stable_trigger_id(external_id),
            external_id=external_id,
            kind=kind,
            actor=actor,
            thread_keys=[thread_key],
            occurred_at=item.occurred_at,
            received_at=self._clock.now_ms(),
            payload=payload,
        )

    def _request_block(self, resource: dict[str, Any]) -> dict[str, Any] | None:
        """只把"有人写了正文"的事件标成候选请求；mention 命中不决定 action 集合（L4）。"""
        body = resource.get("body")
        if not isinstance(body, str) or not body:
            return None
        handle = self._bot_login.removesuffix("[bot]")
        url = resource.get("html_url")
        return {
            "text": body,
            "mentioned": f"@{handle}".casefold() in body.casefold(),
            "url": url if isinstance(url, str) else None,
        }
```

- [ ] **Step 4: 实现 Store 决策 helper**

`stableagent/supervisor/store.py` 在 `find_accepted_decision` 之前/之后追加（放在
`list_decisions` 之后）：

```python
    def next_decision_attempt(self, request_digest: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) + 1 FROM decisions WHERE request_digest = ?",
            (request_digest,),
        ).fetchone()
        return int(row[0])

    def find_accepted_decision(self, request_digest: str) -> Decision | None:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE request_digest = ? AND outcome = ?"
            " ORDER BY attempt ASC LIMIT 1",
            (request_digest, DecisionOutcome.ACCEPT.value),
        ).fetchone()
        return self._row_to_decision(row) if row else None
```

- [ ] **Step 5: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py \
  tests/unit/test_supervisor_store.py
.venv/bin/ruff check stableagent/supervisor/source.py stableagent/supervisor/store.py
.venv/bin/ruff format --check stableagent/supervisor/source.py stableagent/supervisor/store.py
.venv/bin/mypy
```

Expected: `40 passed`（M2 32 + M3 2 + Store 新增 2）；ruff / mypy 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/source.py stableagent/supervisor/store.py \
  tests/unit/test_github_source.py tests/unit/test_supervisor_store.py
git commit -m "feat: normalize github request signals and decision attempts"
```

---

### Task 3：receive 原子推进、扇出与三档决策（M3-3 / M3-4 / M3-5）

**Files:**
- Create: `stableagent/supervisor/router.py`
- Test: `tests/unit/test_supervisor_router.py`

**Interfaces:**
- Consumes: `AutonomyMode` / `ObserveOnlyAssessor`（Task 1）、`next_decision_attempt` /
  `find_accepted_decision`（Task 2）、P5 `StaticPolicy.admit_explicit` / `create_mandate`。
- Produces: `RouterConfig`、`RouterError` 五子类、`RequestSignal`、`Reception`、
  `record_decision()`、`ensure_subscription()`、`latest_trigger()`、`RouterService.receive()`。
  Task 4 只新增 `reevaluate()` 与 unavailable 分支；Task 5/6 只调用既有 helper。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_router.py` 全文：

```python
"""WP-6.3：receive 的原子推进、扇出与三档决策。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind, TicketStatus
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    DecisionOutcome,
    DecisionStrength,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import (
    RequestSignal,
    RouterConfig,
    RouterModeError,
    RouterService,
    ensure_subscription,
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _thread(key: str = "issue:o/r#1", *, status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    kind = ThreadKind.PULL_REQUEST if key.startswith("pr:") else ThreadKind.ISSUE
    ref = key.rsplit("#", 1)[-1]
    return Thread(
        key=key,
        repository="o/r",
        kind=kind,
        external_ref=f"#{ref}",
        last_trigger_at=1,
        status=status,
    )


def _trigger(
    *,
    external_id: str = "github:o/r:issue:1",
    kind: TriggerKind = TriggerKind.ISSUE,
    actor: str = "alice",
    thread_keys: list[str] | None = None,
    text: str | None = None,
    mentioned: bool = True,
    request: Any = None,
) -> Trigger:
    payload: dict[str, Any] = {
        "resource": {"number": 1, "user": {"login": actor}},
        "deleted": False,
    }
    if request is not None:
        payload["request"] = request
    elif text is not None:
        payload["request"] = {"text": text, "mentioned": mentioned, "url": None}
    return Trigger(
        id=f"tg-{external_id}",
        external_id=external_id,
        kind=kind,
        actor=actor,
        thread_keys=thread_keys or ["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload=payload,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> SequenceIds:
    return SequenceIds()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=clock)


@pytest.fixture
def policy(clock: FixedClock, ids: SequenceIds) -> StaticPolicy:
    return StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.RUN_COMMAND}),
            allowed_commands=["pytest"],
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )


def _router(
    store: SupervisorStore,
    policy: StaticPolicy,
    clock: FixedClock,
    ids: SequenceIds,
    *,
    mode: AutonomyMode = AutonomyMode.OBSERVE,
    audit: AuditWriter | None = None,
    repository: str = "o/r",
) -> RouterService:
    return RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        RouterConfig(
            repository=repository,
            mode=mode,
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
        audit=audit,
    )


def _accept(
    router: RouterService,
    store: SupervisorStore,
    *,
    thread: str = "issue:o/r#1",
    text: str = "fix it",
    external_id: str = "github:o/r:issue:1",
):
    store.upsert_thread(_thread(thread))
    reception = router.receive(
        _trigger(external_id=external_id, thread_keys=[thread], text=text)
    )
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None
    return ticket


def test_router_rejects_auto_mode(store, policy, clock, ids) -> None:
    with pytest.raises(RouterModeError):
        _router(store, policy, clock, ids, mode=AutonomyMode.AUTO)


def test_request_signal_reads_only_the_normalized_request_block() -> None:
    signal = RequestSignal.from_trigger(_trigger(text="fix it"))
    assert signal is not None
    assert (signal.text, signal.mentioned, signal.stable_id) == (
        "fix it",
        True,
        "github:o/r:issue:1",
    )


def test_receive_persists_trigger_and_writes_one_observe_decision(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(_trigger())
    assert reception.duplicate is False
    assert len(reception.decision_ids) == 1
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.strength is DecisionStrength.INFERRED
    assert decision.reason_codes == ["inferred_candidate"]
    assert store.get_trigger(_trigger().id) is not None


def test_receive_duplicate_returns_duplicate_without_new_side_effects(
    store, policy, clock, ids
) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids)
    first = router.receive(_trigger())
    second = router.receive(_trigger())
    assert first.duplicate is False
    assert second.duplicate is True
    assert second.decision_ids == [] and second.input_ids == []
    assert len(store.list_decisions(trigger_id=_trigger().id)) == 1


def test_receive_accepts_a_trigger_already_persisted_by_the_source(
    store, policy, clock, ids
) -> None:
    store.upsert_thread(_thread())
    store.insert_trigger(_trigger())
    reception = _router(store, policy, clock, ids).receive(_trigger())
    assert reception.duplicate is False
    assert len(reception.decision_ids) == 1


def test_off_mode_ignores_inferred_without_consulting_the_assessor(
    store, policy, clock, ids
) -> None:
    class Bomb:
        def assess(self, context):  # pragma: no cover - 被调用即失败
            raise AssertionError("off 模式不得调用评估器")

    store.upsert_thread(_thread())
    router = RouterService(
        store,
        policy,
        Bomb(),
        RouterConfig(repository="o/r", mode=AutonomyMode.OFF, operators=frozenset({"alice"})),
        clock=clock,
        ids=ids,
    )
    reception = router.receive(_trigger())
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.reason_codes == ["autonomy_off"]


def test_explicit_admission_creates_seven_facts_atomically(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    audit = AuditWriter(policy, store, clock=clock, ids=ids)
    router = _router(store, policy, clock, ids, audit=audit)
    reception = router.receive(_trigger(text="fix the bug"))
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None and ticket.status is TicketStatus.QUEUED
    mandate = store.get_mandate(ticket.mandate_id)
    assert mandate is not None
    assert mandate.origin_kind == "github"
    assert mandate.requester == "alice"
    assert mandate.scope == "github:o/r:issue:1"
    assert mandate.actions == ["read_repository", "run_command"]
    assert [(item.kind, item.payload) for item in store.list_inputs(ticket.id)] == [
        ("task", "fix the bug")
    ]
    assert len(store.list_assignments(ticket.id)) == 1
    assert [effect.kind for effect in store.due_effects(clock.now_ms())] == [
        EffectKind.LAUNCH_ASSIGNMENT
    ]
    assert [
        subscription.mode
        for subscription in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    ] == [SubscriptionMode.OWN]
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ACCEPT
    assert decision.strength is DecisionStrength.EXPLICIT
    assert decision.mandate_id == mandate.id
    assert decision.reason_codes == ["admitted"]
    assert len(store.list_audit(kind="admit")) == 1


def test_explicit_rejection_records_not_admitted_ignore(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids)
    reception = router.receive(_trigger(actor="mallory", text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.strength is DecisionStrength.NOT_ADMITTED
    assert decision.reason_codes == ["actor_not_allowlisted"]
    assert store.list_tickets() == [] and reception.ticket_id is None


def test_explicit_without_a_mention_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(_trigger(text="fix it", mentioned=False))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["mention_required"]


def test_router_and_policy_repository_mismatch_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids, repository="other/r")
    reception = router.receive(_trigger(text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["repository_mismatch"]
    assert store.list_tickets() == []


def test_malformed_request_block_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(
        _trigger(request={"text": 1, "mentioned": True})
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.strength is DecisionStrength.NOT_ADMITTED
    assert decision.reason_codes == ["malformed_request"]


def test_explicit_without_a_thread_row_is_ignored(store, policy, clock, ids) -> None:
    reception = _router(store, policy, clock, ids).receive(_trigger(text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["thread_unknown"]
    assert store.list_tickets() == []


def test_followup_on_an_owned_thread_attaches_without_a_new_ticket(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:2",
            kind=TriggerKind.ISSUE_COMMENT,
            text="also add tests",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ATTACH
    assert decision.strength is DecisionStrength.EXPLICIT
    assert decision.reason_codes == ["owned_thread_followup"]
    assert reception.ticket_id == ticket.id
    assert len(store.list_tickets()) == 1
    assert len(store.list_inputs(ticket.id)) == 2


def test_duplicate_request_digest_attaches_to_the_existing_ticket(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store, text="fix it")
    store.upsert_thread(_thread("issue:o/r#2"))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue:2",
            thread_keys=["issue:o/r#2"],
            text="fix it",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ATTACH
    assert decision.reason_codes == ["duplicate_request"]
    assert decision.attempt == 2
    assert reception.ticket_id == ticket.id
    assert len(store.list_tickets()) == 1
    subscriptions = {
        (item.thread_key, item.mode)
        for item in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    }
    assert ("issue:o/r#2", SubscriptionMode.CONTEXT) in subscriptions


def test_followup_on_a_stopped_ticket_is_a_stop_decision(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.CANCELLED}))
    before = len(store.list_inputs(ticket.id))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:3",
            kind=TriggerKind.ISSUE_COMMENT,
            text="please continue",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.STOP
    assert decision.reason_codes == ["stopped_ignored"]
    assert reception.ticket_id == ticket.id
    assert len(store.list_inputs(ticket.id)) == before


def test_followup_on_a_completed_ticket_keeps_the_input_and_observes(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:4",
            kind=TriggerKind.ISSUE_COMMENT,
            text="one more thing",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["resume_requires_operator"]
    assert len(store.list_inputs(ticket.id)) == 2
    assert EffectKind.DELIVER_INPUT not in {
        effect.kind for effect in store.due_effects(clock.now_ms())
    }


def test_multiple_owners_observe_ambiguous_without_a_new_ticket(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    first = _accept(router, store, thread="issue:o/r#1", text="first task")
    second = _accept(
        router,
        store,
        thread="issue:o/r#2",
        text="second task",
        external_id="github:o/r:issue:2",
    )
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=second.id,
        thread_key="issue:o/r#1",
        mode=SubscriptionMode.OWN,
        created_at=clock.now_ms(),
    )
    reception = router.receive(_trigger(text="third task"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["ambiguous_owner"]
    assert len(store.list_tickets()) == 2
    assert first.id != second.id


def test_fanout_delivers_one_input_per_ticket_across_thread_keys(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    first = _accept(router, store, thread="issue:o/r#1", text="first task")
    second = _accept(
        router,
        store,
        thread="issue:o/r#2",
        text="second task",
        external_id="github:o/r:issue:2",
    )
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=second.id,
        thread_key="issue:o/r#1",
        mode=SubscriptionMode.CONTEXT,
        created_at=clock.now_ms(),
    )
    reception = router.receive(
        _trigger(thread_keys=["issue:o/r#1", "issue:o/r#2"])
    )
    assert reception.fanned_out == 2
    assert len(store.list_inputs(first.id)) == 2
    assert len(store.list_inputs(second.id)) == 2
    deliver = [
        effect
        for effect in store.due_effects(clock.now_ms())
        if effect.kind is EffectKind.DELIVER_INPUT
    ]
    assert len(deliver) == 2


def test_terminal_subscriptions_are_not_fanned_out(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    before = len(store.list_inputs(ticket.id))
    reception = router.receive(_trigger())
    assert reception.fanned_out == 0
    assert len(store.list_inputs(ticket.id)) == before


def test_context_subscription_never_expands_the_mandate(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store, thread="issue:o/r#1", text="first task")
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=ticket.id,
        thread_key="issue:o/r#2",
        mode=SubscriptionMode.CONTEXT,
        created_at=clock.now_ms(),
    )
    mandate_before = store.get_mandate(ticket.mandate_id)
    router.receive(_trigger(thread_keys=["issue:o/r#2"]))
    mandate_after = store.get_mandate(ticket.mandate_id)
    assert mandate_before is not None and mandate_after is not None
    assert mandate_after.actions == mandate_before.actions
    assert len(store.list_tickets()) == 1
    assert len(store.list_inputs(ticket.id)) == 2
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py
```

Expected: `ModuleNotFoundError: stableagent.supervisor.router`。

- [ ] **Step 3: 实现 router.py（完整文件）**

```python
"""Router Intake：Trigger 进入领域的唯一命令（规格 WP-6.3、ADR-06 决策 3/4/6/7/8）。

方向锁：只 import supervisor 记录与 Store、P5 Policy 与 orchestration 记录；外部形态由
Source 标准化，Router 不解析 GitHub 原始字段。事务：`receive` 只开一个 BEGIN IMMEDIATE，
体内无 await（L3）；任一步失败整体回滚。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Admission, ExplicitRequest
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    Assignment,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Ticket,
    TicketInput,
    TicketStatus,
)
from stableagent.supervisor.assessor import AssessmentContext, CandidateAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    Decision,
    DecisionOutcome,
    DecisionStrength,
    Subscription,
    SubscriptionMode,
    Thread,
    Trigger,
    TriggerKind,
    canonical_json,
)
from stableagent.supervisor.store import SupervisorStore

_ALLOWED_MODES = (AutonomyMode.OFF, AutonomyMode.OBSERVE)


class RouterError(Exception):
    code = "router.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class RouterModeError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.mode", message)


class RouterPayloadError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.payload", message)


class RouterNotFoundError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.not_found", message)


class RouterPermissionError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.permission", message)


class RouterStateError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.state", message)


class RouterConfig(BaseModel):
    """operator 写定的单仓库配置；actions 只来自这里，不来自外部文本（L4）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str
    mode: AutonomyMode = AutonomyMode.OBSERVE
    operators: frozenset[str] = frozenset()
    actions: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    allowed_mcp: tuple[str, ...] = ()
    network_profile: str | None = None
    base_ref: str | None = None
    requires_approval: bool = False


class RequestSignal(BaseModel):
    """Source 标准化后的显式请求；text 只进 digest 与 Input，不参与判定（L4）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    mentioned: bool
    stable_id: str
    url: str | None = None

    @classmethod
    def from_trigger(cls, trigger: Trigger) -> RequestSignal | None:
        block = trigger.payload.get("request")
        if block is None:
            return None
        if not isinstance(block, dict):
            raise RouterPayloadError("request 块不是对象")
        text = block.get("text")
        mentioned = block.get("mentioned")
        if not isinstance(text, str) or not isinstance(mentioned, bool):
            raise RouterPayloadError("request 块缺少 text/mentioned")
        url = block.get("url")
        if url is not None and not isinstance(url, str):
            raise RouterPayloadError("request.url 不是字符串")
        return cls(text=text, mentioned=mentioned, stable_id=trigger.external_id, url=url)


class Reception(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_id: str
    duplicate: bool = False
    decision_ids: list[str] = Field(default_factory=list)
    ticket_id: str | None = None
    input_ids: list[str] = Field(default_factory=list)
    fanned_out: int = 0


def record_decision(
    store: SupervisorStore,
    *,
    ids: IdFactory,
    created_at: int,
    trigger_ids: list[str],
    outcome: DecisionOutcome,
    strength: DecisionStrength,
    reason_codes: list[str],
    policy_version: str,
    request_digest: str,
    mandate_id: str | None = None,
) -> Decision:
    decision = Decision(
        id=ids.new_id("dc"),
        trigger_ids=list(trigger_ids),
        outcome=outcome,
        strength=strength,
        reason_codes=list(reason_codes),
        policy_version=policy_version,
        mandate_id=mandate_id,
        request_digest=request_digest,
        attempt=store.next_decision_attempt(request_digest),
        created_at=created_at,
    )
    store.insert_decision(decision)
    return decision


def ensure_subscription(
    store: SupervisorStore,
    *,
    ids: IdFactory,
    ticket_id: str,
    thread_key: str,
    mode: SubscriptionMode,
    created_at: int,
) -> Subscription:
    for subscription in store.list_subscriptions(
        ticket_id=ticket_id, thread_key=thread_key, live_only=True
    ):
        if subscription.mode is mode:
            return subscription
    subscription = Subscription(
        id=ids.new_id("sb"),
        ticket_id=ticket_id,
        thread_key=thread_key,
        mode=mode,
        created_at=created_at,
    )
    store.insert_subscription(subscription)
    return subscription


def latest_trigger(store: SupervisorStore, thread_key: str) -> Trigger | None:
    triggers = store.list_triggers(thread_key=thread_key, limit=1_000)
    return triggers[-1] if triggers else None


def _input_text(trigger: Trigger, signal: RequestSignal | None) -> str:
    if signal is not None:
        return signal.text
    return canonical_json(trigger.payload)


class RouterService:
    def __init__(
        self,
        store: SupervisorStore,
        policy: StaticPolicy,
        assessor: CandidateAssessor,
        config: RouterConfig,
        *,
        clock: Clock,
        ids: IdFactory,
        audit: AuditWriter | None = None,
    ) -> None:
        if config.mode not in _ALLOWED_MODES:
            raise RouterModeError(f"{config.mode.value} 未启用：P6 Beta 锁定 observe")
        self._store = store
        self._policy = policy
        self._assessor = assessor
        self._config = config
        self._clock = clock
        self._ids = ids
        self._audit = audit

    @property
    def config(self) -> RouterConfig:
        return self._config

    def receive(self, trigger: Trigger) -> Reception:
        signal, signal_error = self._read_signal(trigger)
        admission = self._admit(trigger, signal)
        with self._store.transaction():
            return self._route(
                trigger, signal=signal, signal_error=signal_error, admission=admission
            )

    # ------------------------------------------------------------------ 路由
    def _route(
        self,
        trigger: Trigger,
        *,
        signal: RequestSignal | None,
        signal_error: str | None,
        admission: Admission | None,
    ) -> Reception:
        if self._already_decided(trigger.id):
            return Reception(trigger_id=trigger.id, duplicate=True)
        self._store.insert_trigger(trigger)
        now = self._clock.now_ms()
        input_ids = self._fanout(trigger, now, signal)
        threads = [
            thread
            for key in trigger.thread_keys
            if (thread := self._store.get_thread(key)) is not None
        ]
        primary = threads[0] if threads else None
        if signal_error is not None:
            decision = self._ignore(
                trigger,
                now,
                signal_error,
                DecisionStrength.NOT_ADMITTED,
                digest=trigger.digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        if admission is not None and signal is not None:
            return self._route_explicit(
                trigger, signal, admission, primary, now, input_ids
            )
        return self._route_inferred(trigger, threads, now, input_ids)

    def _route_explicit(
        self,
        trigger: Trigger,
        signal: RequestSignal,
        admission: Admission,
        primary: Thread | None,
        now: int,
        input_ids: list[str],
    ) -> Reception:
        if not admission.admitted:
            decision = self._ignore(
                trigger,
                now,
                admission.reason_code,
                DecisionStrength.NOT_ADMITTED,
                digest=admission.request_digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        owners = self._live_own_tickets(trigger)
        if len(owners) == 1:
            owner = owners[0]
            if owner.status is TicketStatus.CANCELLED:
                decision = self._stop(
                    trigger, now, "stopped_ignored", admission.request_digest
                )
                return self._reception(trigger, decision, ticket_id=owner.id)
            if owner.status in (TicketStatus.COMPLETED, TicketStatus.FAILED):
                stored = self._store_input(owner, trigger, signal, now)
                decision = self._observe(
                    trigger,
                    now,
                    ["resume_requires_operator"],
                    digest=admission.request_digest,
                )
                return self._reception(
                    trigger, decision, ticket_id=owner.id, input_ids=[stored]
                )
            decision = self._attach(
                trigger, now, "owned_thread_followup", admission.request_digest
            )
            return self._reception(
                trigger, decision, ticket_id=owner.id, input_ids=input_ids
            )
        if len(owners) > 1:
            decision = self._observe(
                trigger, now, ["ambiguous_owner"], digest=admission.request_digest
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        existing = self._existing_ticket(admission)
        if existing is not None:
            if primary is not None:
                ensure_subscription(
                    self._store,
                    ids=self._ids,
                    ticket_id=existing.id,
                    thread_key=primary.key,
                    mode=SubscriptionMode.CONTEXT,
                    created_at=now,
                )
            decision = self._attach(
                trigger, now, "duplicate_request", admission.request_digest
            )
            return self._reception(
                trigger, decision, ticket_id=existing.id, input_ids=input_ids
            )
        if primary is None:
            decision = self._ignore(
                trigger, now, "thread_unknown", DecisionStrength.NOT_ADMITTED
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        ticket = self._accept(signal, admission, primary, now)
        decision = record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.ACCEPT,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=["admitted"],
            policy_version=self._policy.policy_version,
            request_digest=admission.request_digest,
            mandate_id=ticket.mandate_id,
        )
        return self._reception(trigger, decision, ticket_id=ticket.id, input_ids=input_ids)

    def _route_inferred(
        self, trigger: Trigger, threads: list[Thread], now: int, input_ids: list[str]
    ) -> Reception:
        if self._config.mode is AutonomyMode.OFF:
            decision = self._ignore(
                trigger, now, "autonomy_off", DecisionStrength.INFERRED
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        context = self._assessment_context(trigger, threads)
        assessment = self._assessor.assess(context)
        decision = self._observe(
            trigger, now, assessment.reason_codes, digest=trigger.digest
        )
        return self._reception(trigger, decision, input_ids=input_ids)

    def _accept(
        self,
        signal: RequestSignal,
        admission: Admission,
        primary: Thread,
        now: int,
    ) -> Ticket:
        mandate = self._policy.create_mandate(
            admission,
            actions=self._config.actions,
            allowed_paths=self._config.allowed_paths,
            allowed_commands=self._config.allowed_commands,
            allowed_mcp=self._config.allowed_mcp,
            network_profile=self._config.network_profile,
            base_ref=self._config.base_ref,
            requires_approval=self._config.requires_approval,
        )
        ticket = Ticket(
            id=self._ids.new_id("tk"),
            title=signal.text[:40],
            task=signal.text,
            created_at=now,
            status=TicketStatus.QUEUED,
            queued_at=now,
            mandate_id=mandate.id,
        )
        assignment = Assignment(id=self._ids.new_id("as"), ticket_id=ticket.id, attempt=1)
        self._store.insert_mandate(mandate)
        self._store.insert_ticket(ticket)
        self._store.insert_input(
            TicketInput(
                id=self._ids.new_id("in"),
                ticket_id=ticket.id,
                sequence=1,
                kind="task",
                payload=signal.text,
                created_at=now,
            )
        )
        self._store.insert_assignment(assignment)
        self._store.enqueue_effect(
            Effect(
                id=self._ids.new_id("ef"),
                kind=EffectKind.LAUNCH_ASSIGNMENT,
                idempotency_key=f"launch:{assignment.id}:1",
                payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
                due_at=now,
            )
        )
        ensure_subscription(
            self._store,
            ids=self._ids,
            ticket_id=ticket.id,
            thread_key=primary.key,
            mode=SubscriptionMode.OWN,
            created_at=now,
        )
        return ticket

    # ------------------------------------------------------------------ 判定辅助
    def _read_signal(self, trigger: Trigger) -> tuple[RequestSignal | None, str | None]:
        if trigger.kind is TriggerKind.CI_STATUS:
            return None, None
        try:
            return RequestSignal.from_trigger(trigger), None
        except RouterPayloadError:
            return None, "malformed_request"

    def _admit(self, trigger: Trigger, signal: RequestSignal | None) -> Admission | None:
        if signal is None:
            return None
        admission = self._policy.admit_explicit(
            ExplicitRequest(
                repository=self._config.repository,
                requester=trigger.actor,
                mentioned=signal.mentioned,
                stable_id=signal.stable_id,
                text=signal.text,
            )
        )
        if self._audit is not None:
            self._audit.admitted(admission)
        return admission

    def _already_decided(self, trigger_id: str) -> bool:
        return bool(self._store.list_decisions(trigger_id=trigger_id, limit=1))

    def _live_subscriptions(
        self, trigger: Trigger, mode: SubscriptionMode
    ) -> list[Subscription]:
        seen: set[str] = set()
        result: list[Subscription] = []
        for thread_key in trigger.thread_keys:
            for subscription in self._store.list_subscriptions(
                thread_key=thread_key, live_only=True
            ):
                if subscription.mode is not mode or subscription.id in seen:
                    continue
                seen.add(subscription.id)
                result.append(subscription)
        return result

    def _live_own_tickets(self, trigger: Trigger) -> list[Ticket]:
        tickets: dict[str, Ticket] = {}
        for subscription in self._live_subscriptions(trigger, SubscriptionMode.OWN):
            ticket = self._store.get_ticket(subscription.ticket_id)
            if ticket is not None:
                tickets[ticket.id] = ticket
        return list(tickets.values())

    def _assessment_context(
        self, trigger: Trigger, threads: list[Thread]
    ) -> AssessmentContext:
        owners = self._live_own_tickets(trigger)
        return AssessmentContext(
            trigger=trigger,
            thread=threads[0] if threads else None,
            has_live_own_subscription=bool(owners),
            has_live_context_subscription=bool(
                self._live_subscriptions(trigger, SubscriptionMode.CONTEXT)
            ),
            existing_ticket_id=owners[0].id if len(owners) == 1 else None,
            policy_version=self._policy.policy_version,
        )

    def _existing_ticket(self, admission: Admission) -> Ticket | None:
        decision = self._store.find_accepted_decision(admission.request_digest)
        if decision is None or decision.mandate_id is None:
            return None
        mandate = self._store.get_mandate(decision.mandate_id)
        if mandate is None:
            return None
        return self._store.get_ticket_by_mandate_scope(
            mandate.scope, origin_kind="github"
        )

    def _fanout(
        self, trigger: Trigger, now: int, signal: RequestSignal | None
    ) -> list[str]:
        seen: set[str] = set()
        input_ids: list[str] = []
        for thread_key in trigger.thread_keys:
            for subscription in self._store.list_subscriptions(
                thread_key=thread_key, live_only=True
            ):
                ticket = self._store.get_ticket(subscription.ticket_id)
                if ticket is None or ticket.status in TERMINAL_TICKET_STATUSES:
                    continue
                if ticket.id in seen:
                    continue
                seen.add(ticket.id)
                input_ids.append(self._append_input(ticket, trigger, signal, now))
        return input_ids

    def _append_input(
        self,
        ticket: Ticket,
        trigger: Trigger,
        signal: RequestSignal | None,
        now: int,
    ) -> str:
        sequence = len(self._store.list_inputs(ticket.id)) + 1
        item = TicketInput(
            id=self._ids.new_id("in"),
            ticket_id=ticket.id,
            sequence=sequence,
            kind="text",
            payload=_input_text(trigger, signal),
            created_at=now,
        )
        self._store.insert_input(item)
        if ticket.status is not TicketStatus.RUNNING:
            self._store.update_ticket(
                ticket.model_copy(update={"woken_at": ticket.woken_at or now})
            )
        self._store.enqueue_effect(
            Effect(
                id=self._ids.new_id("ef"),
                kind=EffectKind.DELIVER_INPUT,
                idempotency_key=f"deliver:{ticket.id}:{sequence}",
                payload={
                    "ticket_id": ticket.id,
                    "input_id": item.id,
                    "sequence": sequence,
                },
                due_at=now,
            )
        )
        return item.id

    def _store_input(
        self, ticket: Ticket, trigger: Trigger, signal: RequestSignal, now: int
    ) -> str:
        text = _input_text(trigger, signal)
        for item in self._store.list_inputs(ticket.id):
            if item.state == "pending" and item.payload == text:
                return item.id
        sequence = len(self._store.list_inputs(ticket.id)) + 1
        item = TicketInput(
            id=self._ids.new_id("in"),
            ticket_id=ticket.id,
            sequence=sequence,
            kind="text",
            payload=text,
            created_at=now,
        )
        self._store.insert_input(item)
        return item.id

    def _reception(
        self,
        trigger: Trigger,
        decision: Decision,
        *,
        ticket_id: str | None = None,
        input_ids: list[str] | None = None,
    ) -> Reception:
        ids = list(input_ids or [])
        return Reception(
            trigger_id=trigger.id,
            decision_ids=[decision.id],
            ticket_id=ticket_id,
            input_ids=ids,
            fanned_out=len(ids),
        )

    def _ignore(
        self,
        trigger: Trigger,
        now: int,
        reason: str,
        strength: DecisionStrength,
        *,
        digest: str | None = None,
    ) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.IGNORE,
            strength=strength,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest or trigger.digest,
        )

    def _observe(
        self,
        trigger: Trigger,
        now: int,
        reasons: list[str],
        *,
        digest: str | None = None,
    ) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=list(reasons),
            policy_version=self._policy.policy_version,
            request_digest=digest or trigger.digest,
        )

    def _attach(
        self, trigger: Trigger, now: int, reason: str, digest: str
    ) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.ATTACH,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest,
        )

    def _stop(self, trigger: Trigger, now: int, reason: str, digest: str) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.STOP,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest,
        )
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py
.venv/bin/ruff check stableagent/supervisor/router.py
.venv/bin/ruff format --check stableagent/supervisor/router.py
.venv/bin/mypy
```

Expected: `20 passed`；ruff / mypy 全绿。若 `test_receive_accepts_a_trigger_already_persisted_by_the_source`
因 `insert_trigger` 返回 False 但决策仍写入而通过，保留该用例作为 M2/M3 接缝契约。

- [ ] **Step 5: 回归 M2 与边界测试**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py \
  tests/unit/test_supervisor_model.py tests/unit/test_supervisor_store.py \
  tests/unit/test_control_boundaries.py tests/unit/test_execution_boundaries.py
```

Expected: 全绿；`test_execution_boundaries` 不应报 `router.py`（无 `await`）。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/router.py tests/unit/test_supervisor_router.py
git commit -m "feat: route supervisor triggers through one atomic receive"
```

---

### Task 4：unavailable 与 reevaluate（M3-6）

**Files:**
- Modify: `stableagent/supervisor/router.py`
- Test: `tests/unit/test_supervisor_router.py`

**Interfaces:**
- Consumes: Task 3 的 `_route` / `_read_signal` / `_admit` / `_route_inferred`。
- Produces: `RouterService.reevaluate(trigger_id) -> Reception`；评估器异常只落
  `UNAVAILABLE`，重评估不重复扇出、不新增 Trigger。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_supervisor_router.py` 追加：

```python
class FlakyAssessor:
    """第一次失败、之后委托给 ObserveOnlyAssessor；用于 unavailable → reevaluate。"""

    def __init__(self) -> None:
        self.calls = 0

    def assess(self, context):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("assessor down")
        return ObserveOnlyAssessor().assess(context)


def _flaky_router(store, policy, clock, ids) -> RouterService:
    return RouterService(
        store,
        policy,
        FlakyAssessor(),
        RouterConfig(
            repository="o/r",
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
    )


def test_assessor_failure_records_unavailable(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _flaky_router(store, policy, clock, ids).receive(_trigger())
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.UNAVAILABLE
    assert decision.strength is DecisionStrength.INFERRED
    assert decision.reason_codes == ["assessor_error"]


def test_reevaluate_retries_unavailable_without_refanout(store, policy, clock, ids) -> None:
    router = _flaky_router(store, policy, clock, ids)
    ticket = _accept(router, store)
    trigger = _trigger(
        external_id="github:o/r:issue_comment:9",
        kind=TriggerKind.ISSUE_COMMENT,
    )
    first = router.receive(trigger)
    assert store.get_decision(first.decision_ids[0]).outcome is DecisionOutcome.UNAVAILABLE
    inputs_before = len(store.list_inputs(ticket.id))
    retried = router.reevaluate(trigger.id)
    assert retried.duplicate is False
    assert retried.input_ids == []
    decision = store.get_decision(retried.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.attempt == 2
    assert "reevaluated" in decision.reason_codes
    assert len(store.list_inputs(ticket.id)) == inputs_before
    assert len(store.list_decisions(trigger_id=trigger.id)) == 2


def test_reevaluate_of_a_missing_trigger_raises(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    with pytest.raises(RouterNotFoundError):
        router.reevaluate("tg-missing")
```

需要把 `RouterNotFoundError` 加入 import 列表。

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py -k "unavailable or reevaluate"
```

Expected: `RouterService` 无 `reevaluate`；评估器异常直接冒泡导致前两条失败。

- [ ] **Step 3: 实现**

`receive` 与 `_route` 改为带 `reevaluate` 参数；新增方法：

```python
    def reevaluate(self, trigger_id: str) -> Reception:
        trigger = self._store.get_trigger(trigger_id)
        if trigger is None:
            raise RouterNotFoundError(f"trigger 不存在：{trigger_id}")
        signal, signal_error = self._read_signal(trigger)
        admission = self._admit(trigger, signal)
        with self._store.transaction():
            return self._route(
                trigger,
                signal=signal,
                signal_error=signal_error,
                admission=admission,
                reevaluate=True,
            )
```

`_route` 的签名与开头改为：

```python
    def _route(
        self,
        trigger: Trigger,
        *,
        signal: RequestSignal | None,
        signal_error: str | None,
        admission: Admission | None,
        reevaluate: bool = False,
    ) -> Reception:
        if not reevaluate:
            if self._already_decided(trigger.id):
                return Reception(trigger_id=trigger.id, duplicate=True)
            self._store.insert_trigger(trigger)
        now = self._clock.now_ms()
        input_ids = [] if reevaluate else self._fanout(trigger, now, signal)
        ...
        return self._route_inferred(trigger, threads, now, input_ids, reevaluate=reevaluate)
```

`receive` 的调用补 `reevaluate=False`。`_route_inferred` 改为：

```python
    def _route_inferred(
        self,
        trigger: Trigger,
        threads: list[Thread],
        now: int,
        input_ids: list[str],
        *,
        reevaluate: bool = False,
    ) -> Reception:
        if self._config.mode is AutonomyMode.OFF:
            decision = self._ignore(
                trigger, now, "autonomy_off", DecisionStrength.INFERRED
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        context = self._assessment_context(trigger, threads)
        try:
            assessment = self._assessor.assess(context)
        except Exception:
            decision = record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=[trigger.id],
                outcome=DecisionOutcome.UNAVAILABLE,
                strength=DecisionStrength.INFERRED,
                reason_codes=["assessor_error"],
                policy_version=self._policy.policy_version,
                request_digest=trigger.digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        reasons = list(assessment.reason_codes)
        if reevaluate:
            reasons.append("reevaluated")
        decision = self._observe(trigger, now, reasons, digest=trigger.digest)
        return self._reception(trigger, decision, input_ids=input_ids)
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py
.venv/bin/ruff check stableagent/supervisor/router.py
.venv/bin/ruff format --check stableagent/supervisor/router.py
.venv/bin/mypy
```

Expected: `23 passed`；ruff / mypy 全绿。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/router.py tests/unit/test_supervisor_router.py
git commit -m "feat: record assessor failures as unavailable and allow reevaluation"
```

---

### Task 5：命令层 attach / subscribe / unsubscribe / stop（M3-7 前半）

**Files:**
- Create: `stableagent/supervisor/service.py`
- Test: `tests/unit/test_supervisor_service.py`

**Interfaces:**
- Consumes: Task 3 的 `record_decision` / `ensure_subscription` / `latest_trigger`；
  `RouterService.config.operators`。
- Produces: `SupervisorService(store, policy, router, *, clock, ids, audit=None)`；`receive`
  透传；`reevaluate`、`attach`、`subscribe`、`unsubscribe`、`stop`。Task 6 只追加
  `handback` / `resume` / `record_pull_request` 与私有 helper。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_service.py` 全文：

```python
"""WP-6.3：命令层权限、context 订阅与人工 stop。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind, TicketStatus
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    DecisionOutcome,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import (
    RouterConfig,
    RouterNotFoundError,
    RouterPermissionError,
    RouterService,
    RouterStateError,
)
from stableagent.supervisor.service import SupervisorService
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _thread(key: str = "issue:o/r#1") -> Thread:
    kind = ThreadKind.PULL_REQUEST if key.startswith("pr:") else ThreadKind.ISSUE
    ref = key.rsplit("#", 1)[-1]
    return Thread(
        key=key,
        repository="o/r",
        kind=kind,
        external_ref=f"#{ref}",
        last_trigger_at=1,
        status=ThreadStatus.OPEN,
    )


def _trigger(
    *,
    external_id: str = "github:o/r:issue:1",
    thread_keys: list[str] | None = None,
    text: str | None = None,
) -> Trigger:
    payload: dict[str, Any] = {"resource": {"number": 1}, "deleted": False}
    if text is not None:
        payload["request"] = {"text": text, "mentioned": True, "url": None}
    return Trigger(
        id=f"tg-{external_id}",
        external_id=external_id,
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=thread_keys or ["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload=payload,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> SequenceIds:
    return SequenceIds()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=clock)


@pytest.fixture
def service(store: SupervisorStore, clock: FixedClock, ids: SequenceIds) -> SupervisorService:
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.RUN_COMMAND}),
            allowed_commands=["pytest"],
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    router = RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        RouterConfig(
            repository="o/r",
            mode=AutonomyMode.OBSERVE,
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
    )
    return SupervisorService(store, policy, router, clock=clock, ids=ids)


def _accept(service: SupervisorService, store: SupervisorStore):
    store.upsert_thread(_thread())
    reception = service.receive(_trigger(text="fix it"))
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None
    return ticket


def test_non_operators_cannot_attach_or_stop(service, store) -> None:
    ticket = _accept(service, store)
    with pytest.raises(RouterPermissionError):
        service.attach(ticket.id, thread_key="issue:o/r#2", actor="mallory")
    with pytest.raises(RouterPermissionError):
        service.stop(ticket.id, actor="mallory")
    with pytest.raises(RouterPermissionError):
        service.unsubscribe(ticket.id, thread_key="issue:o/r#1", actor="mallory")


def test_attach_is_an_idempotent_context_subscription(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    first = service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    second = service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    assert first.id == second.id
    assert first.mode is SubscriptionMode.CONTEXT
    assert len(store.list_subscriptions(ticket_id=ticket.id, thread_key="issue:o/r#2")) == 1


def test_subscribe_uses_the_trigger_primary_thread(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    trigger = _trigger(external_id="github:o/r:issue:2", thread_keys=["issue:o/r#2"])
    store.insert_trigger(trigger)
    subscription = service.subscribe(ticket.id, trigger_id=trigger.id, actor="alice")
    assert subscription.thread_key == "issue:o/r#2"
    assert subscription.mode is SubscriptionMode.CONTEXT


def test_unsubscribe_ends_live_subscriptions_and_stops_fanout(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    assert service.unsubscribe(ticket.id, thread_key="issue:o/r#2", actor="alice") == 1
    before = len(store.list_inputs(ticket.id))
    reception = service.receive(
        _trigger(external_id="github:o/r:issue:3", thread_keys=["issue:o/r#2"])
    )
    assert reception.fanned_out == 0
    assert len(store.list_inputs(ticket.id)) == before


def test_stop_cancels_ticket_effects_and_context_but_keeps_own(
    service, store, clock
) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    stopped = service.stop(ticket.id, actor="alice", reason="operator stop")
    assert stopped.status is TicketStatus.CANCELLED
    kinds = {effect.kind for effect in store.due_effects(clock.now_ms())}
    assert EffectKind.CANCEL_ASSIGNMENT in kinds
    live = store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]
    decisions = store.list_decisions(trigger_id=_trigger().id)
    assert decisions[-1].outcome is DecisionOutcome.STOP
    assert decisions[-1].reason_codes == ["manual_stop"]


def test_stop_is_idempotent_for_a_cancelled_ticket(service, store) -> None:
    ticket = _accept(service, store)
    once = service.stop(ticket.id, actor="alice")
    twice = service.stop(ticket.id, actor="alice")
    assert once.status is TicketStatus.CANCELLED
    assert twice.status is TicketStatus.CANCELLED
    assert twice.ended_at == once.ended_at


def test_stop_on_completed_ticket_only_ends_context_subscriptions(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    stopped = service.stop(ticket.id, actor="alice")
    assert stopped.status is TicketStatus.COMPLETED
    live = store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py
```

Expected: `ModuleNotFoundError: stableagent.supervisor.service`。

- [ ] **Step 3: 实现 service.py（完整文件）**

```python
"""Router 的运维命令层：ticket 域命令，全部检查 operator 与状态（规格 WP-6.3 第 4~6 条）。

方向锁：service → router → supervisor.store → orchestration；不 import integrations。
receive / reevaluate 留在 RouterService；命令层只做 Ticket / Subscription 的受控变更。
"""

from __future__ import annotations

from stableagent.control.audit import AuditWriter
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    AssignmentStatus,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Ticket,
    TicketAction,
    TicketStatus,
    next_ticket_status,
)
from stableagent.supervisor.model import (
    Decision,
    DecisionOutcome,
    DecisionStrength,
    Subscription,
    SubscriptionMode,
    Trigger,
    payload_digest,
)
from stableagent.supervisor.router import (
    Reception,
    RouterNotFoundError,
    RouterPermissionError,
    RouterService,
    RouterStateError,
    ensure_subscription,
    latest_trigger,
    record_decision,
)
from stableagent.supervisor.store import SupervisorStore

_LIVE_ASSIGNMENT_STATUSES = (
    AssignmentStatus.PENDING,
    AssignmentStatus.RUNNING,
    AssignmentStatus.INTERRUPTED,
)


class SupervisorService:
    def __init__(
        self,
        store: SupervisorStore,
        policy: StaticPolicy,
        router: RouterService,
        *,
        clock: Clock,
        ids: IdFactory,
        audit: AuditWriter | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._router = router
        self._clock = clock
        self._ids = ids
        self._audit = audit

    @property
    def router(self) -> RouterService:
        return self._router

    def receive(self, trigger: Trigger) -> Reception:
        return self._router.receive(trigger)

    def reevaluate(self, trigger_id: str, *, actor: str) -> Decision:
        self._require_operator(actor)
        reception = self._router.reevaluate(trigger_id)
        if not reception.decision_ids:
            raise RouterStateError("reevaluate 未产生 Decision")
        decision = self._store.get_decision(reception.decision_ids[0])
        if decision is None:  # pragma: no cover - 刚写入的行必可读
            raise RouterStateError("Decision 缺失")
        return decision

    def attach(self, ticket_id: str, *, thread_key: str, actor: str) -> Subscription:
        """把一个相关 Thread 作为 context 订阅接上；已有 live 订阅则原样返回。"""
        self._require_operator(actor)
        with self._store.transaction():
            ticket = self._require_live_ticket(ticket_id)
            self._require_thread(thread_key)
            live = self._store.list_subscriptions(
                ticket_id=ticket.id, thread_key=thread_key, live_only=True
            )
            if live:
                return live[0]
            return ensure_subscription(
                self._store,
                ids=self._ids,
                ticket_id=ticket.id,
                thread_key=thread_key,
                mode=SubscriptionMode.CONTEXT,
                created_at=self._clock.now_ms(),
            )

    def subscribe(self, ticket_id: str, *, trigger_id: str, actor: str) -> Subscription:
        trigger = self._require_trigger(trigger_id)
        if not trigger.thread_keys:
            raise RouterStateError("trigger 没有 thread key")
        return self.attach(ticket_id, thread_key=trigger.thread_keys[0], actor=actor)

    def unsubscribe(self, ticket_id: str, *, thread_key: str, actor: str) -> int:
        self._require_operator(actor)
        with self._store.transaction():
            self._require_ticket(ticket_id)
            ended = 0
            for subscription in self._store.list_subscriptions(
                ticket_id=ticket_id, thread_key=thread_key, live_only=True
            ):
                ended += self._store.end_subscription(
                    subscription.id, ended_at=self._clock.now_ms()
                )
            return ended

    def stop(self, ticket_id: str, *, actor: str, reason: str | None = None) -> Ticket:
        """人工停：取消未结束 Ticket、入队 cancel effect、只结束 context 订阅。

        own 订阅故意保留：被 stop 的 Ticket 之后收到的明确后续消息由 Router 记 STOP 并
        丢弃，不会因为订阅消失而被当成新任务；人工 resume 也拒绝 CANCELLED。
        """
        self._require_operator(actor)
        with self._store.transaction():
            ticket = self._require_ticket(ticket_id)
            now = self._clock.now_ms()
            if ticket.status is TicketStatus.CANCELLED:
                return ticket
            updated = ticket
            if ticket.status not in TERMINAL_TICKET_STATUSES:
                updated = ticket.model_copy(
                    update={
                        "status": next_ticket_status(ticket.status, TicketAction.CANCEL),
                        "ended_at": now,
                    }
                )
                self._store.update_ticket(updated)
                for assignment in self._store.list_assignments(ticket_id):
                    if assignment.status not in _LIVE_ASSIGNMENT_STATUSES:
                        continue
                    self._store.enqueue_effect(
                        Effect(
                            id=self._ids.new_id("ef"),
                            kind=EffectKind.CANCEL_ASSIGNMENT,
                            idempotency_key=f"cancel:{assignment.id}",
                            payload={
                                "assignment_id": assignment.id,
                                "ticket_id": ticket_id,
                                "reason": reason,
                            },
                            due_at=now,
                        )
                    )
            for subscription in self._store.list_subscriptions(
                ticket_id=ticket_id, live_only=True
            ):
                if subscription.mode is SubscriptionMode.CONTEXT:
                    self._store.end_subscription(subscription.id, ended_at=now)
            record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=self._decision_triggers(ticket_id),
                outcome=DecisionOutcome.STOP,
                strength=DecisionStrength.EXPLICIT,
                reason_codes=["manual_stop"],
                policy_version=self._policy.policy_version,
                request_digest=payload_digest({"command": "stop", "ticket": ticket_id}),
            )
            return updated

    # ------------------------------------------------------------- 内部
    def _require_operator(self, actor: str) -> None:
        allowed = {name.casefold() for name in self._router.config.operators}
        if actor.casefold() not in allowed:
            raise RouterPermissionError(f"actor 不是 operator：{actor}")

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise RouterNotFoundError(f"ticket 不存在：{ticket_id}")
        return ticket

    def _require_live_ticket(self, ticket_id: str) -> Ticket:
        ticket = self._require_ticket(ticket_id)
        if ticket.status in TERMINAL_TICKET_STATUSES:
            raise RouterStateError(f"ticket 已结束：{ticket.status.value}")
        return ticket

    def _require_thread(self, thread_key: str) -> None:
        if self._store.get_thread(thread_key) is None:
            raise RouterNotFoundError(f"thread 不存在：{thread_key}")

    def _require_trigger(self, trigger_id: str) -> Trigger:
        trigger = self._store.get_trigger(trigger_id)
        if trigger is None:
            raise RouterNotFoundError(f"trigger 不存在：{trigger_id}")
        return trigger

    def _decision_triggers(self, ticket_id: str) -> list[str]:
        own = [
            subscription.thread_key
            for subscription in self._store.list_subscriptions(ticket_id=ticket_id)
            if subscription.mode is SubscriptionMode.OWN
        ]
        if not own:
            return []
        trigger = latest_trigger(self._store, own[0])
        return [trigger.id] if trigger is not None else []
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py
.venv/bin/ruff check stableagent/supervisor/service.py
.venv/bin/ruff format --check stableagent/supervisor/service.py
.venv/bin/mypy
```

Expected: `8 passed`；ruff / mypy 全绿。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/service.py tests/unit/test_supervisor_service.py
git commit -m "feat: add supervisor command service for subscriptions and stop"
```

---

### Task 6：handback / resume / record_pull_request（M3-7 后半）

**Files:**
- Modify: `stableagent/supervisor/service.py`
- Test: `tests/unit/test_supervisor_service.py`

**Interfaces:**
- Consumes: `RequestSignal` / `ensure_subscription` / `latest_trigger`；`StaticPolicy.revalidate`；
  `pull_request_thread_key`（`supervisor.source`）。
- Produces: `handback(ticket_id, *, thread_key, trigger_id, actor) -> Decision`；
  `resume(ticket_id, *, trigger_id, actor) -> Ticket`；
  `record_pull_request(ticket_id, *, report_id, actor) -> Subscription`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_supervisor_service.py` 追加（并把 `Assignment`、`Report`、`ReportKind`、
`DecisionOutcome` 加入 import）：

```python
def test_handback_turns_own_into_context_and_allows_a_new_ticket(service, store) -> None:
    ticket = _accept(service, store)
    follow = _trigger(
        external_id="github:o/r:issue_comment:7", text="follow up"
    )
    store.insert_trigger(follow)
    decision = service.handback(
        ticket.id, thread_key="issue:o/r#1", trigger_id=follow.id, actor="alice"
    )
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["handback"]
    live = {
        item.mode
        for item in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    }
    assert live == {SubscriptionMode.CONTEXT}
    reception = service.receive(
        _trigger(
            external_id="github:o/r:issue_comment:8",
            text="new task",
        )
    )
    assert reception.ticket_id is not None
    assert reception.ticket_id != ticket.id
    assert len(store.list_tickets()) == 2


def test_resume_refuses_cancelled_and_live_tickets(service, store) -> None:
    ticket = _accept(service, store)
    trigger = _trigger(external_id="github:o/r:issue_comment:9", text="resume please")
    store.insert_trigger(trigger)
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.CANCELLED}))
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")


def test_resume_requires_an_admitted_explicit_request(service, store) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    plain = _trigger(external_id="github:o/r:issue_comment:10")
    store.insert_trigger(plain)
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=plain.id, actor="alice")


def test_resume_rejects_an_expired_mandate(service, store, clock) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    trigger = _trigger(external_id="github:o/r:issue_comment:11", text="resume please")
    store.insert_trigger(trigger)
    clock.advance(60_001)
    with pytest.raises(RouterStateError, match="revalidation_failed"):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")


def test_resume_requeues_with_a_new_assignment_and_reuses_the_pending_input(
    service, store
) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    follow = _trigger(
        external_id="github:o/r:issue_comment:12",
        text="continue the work",
    )
    service.receive(follow)
    inputs_before = len(store.list_inputs(ticket.id))
    resumed = service.resume(ticket.id, trigger_id=follow.id, actor="alice")
    assert resumed.status is TicketStatus.QUEUED
    assert len(store.list_assignments(ticket.id)) == 2
    assignments = store.list_assignments(ticket.id)
    assert [item.attempt for item in assignments] == [1, 2]
    assert len(store.list_inputs(ticket.id)) == inputs_before
    decisions = store.list_decisions(trigger_id=follow.id)
    assert decisions[-1].outcome is DecisionOutcome.ACCEPT
    assert decisions[-1].reason_codes == ["resumed"]


def test_record_pull_request_adds_own_subscription_from_a_report(service, store, clock) -> None:
    ticket = _accept(service, store)
    assignment = store.list_assignments(ticket.id)[0]
    store.upsert_thread(_thread("pr:o/r#9"))
    service.attach(ticket.id, thread_key="pr:o/r#9", actor="alice")
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=ReportKind.READY,
            summary="opened a PR",
            created_at=clock.now_ms(),
            details={"pull_number": 9},
        )
    )
    subscription = service.record_pull_request(
        ticket.id, report_id="rp-1", actor="alice"
    )
    assert subscription.thread_key == "pr:o/r#9"
    assert subscription.mode is SubscriptionMode.OWN
    live = store.list_subscriptions(ticket_id=ticket.id, thread_key="pr:o/r#9", live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]
    assert store.get_thread("pr:o/r#9") is not None


def test_record_pull_request_rejects_a_report_without_a_number(service, store, clock) -> None:
    ticket = _accept(service, store)
    assignment = store.list_assignments(ticket.id)[0]
    store.insert_report(
        Report(
            id="rp-2",
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=ReportKind.READY,
            summary="no number",
            created_at=clock.now_ms(),
            details={},
        )
    )
    with pytest.raises(RouterStateError, match="pull_number"):
        service.record_pull_request(ticket.id, report_id="rp-2", actor="alice")
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py -k "handback or resume or pull_request"
```

Expected: `SupervisorService` 无对应方法。

- [ ] **Step 3: 实现**

`stableagent/supervisor/service.py` 的 import 追加：

```python
from stableagent.control.model import ExplicitRequest
from stableagent.orchestration.model import Assignment, TicketInput
from stableagent.supervisor.model import Thread, ThreadKind, ThreadStatus
from stableagent.supervisor.router import RequestSignal
from stableagent.supervisor.source import pull_request_thread_key
```

在 `stop` 之后、`# ------------------------------------------------------------- 内部`
之前追加：

```python
    def handback(
        self,
        ticket_id: str,
        *,
        thread_key: str,
        trigger_id: str,
        actor: str,
    ) -> Decision:
        """把 own 降为 context：之后的明确消息重新走 Router，可创建新 Ticket。"""
        self._require_operator(actor)
        trigger = self._require_trigger(trigger_id)
        with self._store.transaction():
            ticket = self._require_live_ticket(ticket_id)
            self._require_thread(thread_key)
            now = self._clock.now_ms()
            own = [
                subscription
                for subscription in self._store.list_subscriptions(
                    ticket_id=ticket.id, thread_key=thread_key, live_only=True
                )
                if subscription.mode is SubscriptionMode.OWN
            ]
            if not own:
                raise RouterStateError("ticket 不 own 该 thread")
            for subscription in own:
                self._store.end_subscription(subscription.id, ended_at=now)
            ensure_subscription(
                self._store,
                ids=self._ids,
                ticket_id=ticket.id,
                thread_key=thread_key,
                mode=SubscriptionMode.CONTEXT,
                created_at=now,
            )
            return record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=[trigger.id],
                outcome=DecisionOutcome.OBSERVE,
                strength=DecisionStrength.INFERRED,
                reason_codes=["handback"],
                policy_version=self._policy.policy_version,
                request_digest=trigger.digest,
            )

    def resume(self, ticket_id: str, *, trigger_id: str, actor: str) -> Ticket:
        """已结束 Ticket 的唯一重开路径：operator + 显式请求 + Mandate 复检。"""
        self._require_operator(actor)
        trigger = self._require_trigger(trigger_id)
        signal = RequestSignal.from_trigger(trigger)
        if signal is None:
            raise RouterStateError("resume 需要显式 request")
        admission = self._policy.admit_explicit(
            ExplicitRequest(
                repository=self._router.config.repository,
                requester=trigger.actor,
                mentioned=signal.mentioned,
                stable_id=signal.stable_id,
                text=signal.text,
            )
        )
        if self._audit is not None:
            self._audit.admitted(admission)
        if not admission.admitted:
            raise RouterPermissionError(f"resume 未准入：{admission.reason_code}")
        with self._store.transaction():
            ticket = self._require_ticket(ticket_id)
            if ticket.status is TicketStatus.CANCELLED:
                raise RouterStateError("cancelled ticket 不可 resume")
            if ticket.status not in (TicketStatus.COMPLETED, TicketStatus.FAILED):
                raise RouterStateError(f"ticket 未结束：{ticket.status.value}")
            mandate = self._store.get_mandate(ticket.mandate_id)
            if mandate is None:
                raise RouterStateError("mandate 不存在")
            verdict = self._policy.revalidate(mandate, when="launch")
            if self._audit is not None:
                self._audit.revalidated(mandate, "launch", verdict, ticket_id=ticket.id)
            if not verdict.allowed:
                raise RouterStateError(f"revalidation_failed:{verdict.reason}")
            now = self._clock.now_ms()
            pending = [
                item
                for item in self._store.list_inputs(ticket.id)
                if item.state == "pending" and item.payload == signal.text
            ]
            if not pending:
                sequence = len(self._store.list_inputs(ticket.id)) + 1
                self._store.insert_input(
                    TicketInput(
                        id=self._ids.new_id("in"),
                        ticket_id=ticket.id,
                        sequence=sequence,
                        kind="task",
                        payload=signal.text,
                        created_at=now,
                    )
                )
            updated = ticket.model_copy(
                update={
                    "status": TicketStatus.QUEUED,
                    "queued_at": now,
                    "ended_at": None,
                    "woken_at": None,
                }
            )
            self._store.update_ticket(updated)
            attempt = len(self._store.list_assignments(ticket.id)) + 1
            assignment = Assignment(
                id=self._ids.new_id("as"), ticket_id=ticket.id, attempt=attempt
            )
            self._store.insert_assignment(assignment)
            self._store.enqueue_effect(
                Effect(
                    id=self._ids.new_id("ef"),
                    kind=EffectKind.LAUNCH_ASSIGNMENT,
                    idempotency_key=f"launch:{assignment.id}:{attempt}",
                    payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
                    due_at=now,
                )
            )
            own = [
                subscription
                for subscription in self._store.list_subscriptions(
                    ticket_id=ticket.id, live_only=True
                )
                if subscription.mode is SubscriptionMode.OWN
            ]
            if not own and trigger.thread_keys:
                ensure_subscription(
                    self._store,
                    ids=self._ids,
                    ticket_id=ticket.id,
                    thread_key=trigger.thread_keys[0],
                    mode=SubscriptionMode.OWN,
                    created_at=now,
                )
            record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=[trigger.id],
                outcome=DecisionOutcome.ACCEPT,
                strength=DecisionStrength.EXPLICIT,
                reason_codes=["resumed"],
                policy_version=self._policy.policy_version,
                request_digest=admission.request_digest,
                mandate_id=mandate.id,
            )
            return updated

    def record_pull_request(
        self, ticket_id: str, *, report_id: str, actor: str
    ) -> Subscription:
        """Worker 报告里出现的 PR 才能升为 own；正文只取数字，不取授权（L4）。"""
        self._require_operator(actor)
        with self._store.transaction():
            ticket = self._require_live_ticket(ticket_id)
            report = next(
                (
                    item
                    for item in self._store.list_reports(ticket.id)
                    if item.id == report_id
                ),
                None,
            )
            if report is None:
                raise RouterNotFoundError(f"report 不存在：{report_id}")
            number = report.details.get("pull_number", report.details.get("pr_number"))
            if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                raise RouterStateError("report 缺少 pull_number")
            thread_key = pull_request_thread_key(
                self._router.config.repository, number
            )
            self._store.upsert_thread(
                Thread(
                    key=thread_key,
                    repository=self._router.config.repository,
                    kind=ThreadKind.PULL_REQUEST,
                    external_ref=f"#{number}",
                    last_trigger_at=report.created_at,
                    status=ThreadStatus.OPEN,
                )
            )
            now = self._clock.now_ms()
            for subscription in self._store.list_subscriptions(
                ticket_id=ticket.id, thread_key=thread_key, live_only=True
            ):
                if subscription.mode is SubscriptionMode.CONTEXT:
                    self._store.end_subscription(subscription.id, ended_at=now)
            return ensure_subscription(
                self._store,
                ids=self._ids,
                ticket_id=ticket.id,
                thread_key=thread_key,
                mode=SubscriptionMode.OWN,
                created_at=now,
            )
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py \
  tests/unit/test_supervisor_service.py
.venv/bin/ruff check stableagent/supervisor/service.py
.venv/bin/ruff format --check stableagent/supervisor/service.py
.venv/bin/mypy
```

Expected: `15 passed`（Service）；Router + Service 合计 `38 passed`；ruff / mypy 全绿。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/service.py tests/unit/test_supervisor_service.py
git commit -m "feat: handback resume and pull-request subscription commands"
```

---

### Task 7：CI FOCUS 32、Router 指南与 M3 出口（M3-8）

**Files:**
- Modify: `tests/unit/test_ci_focus_gate.py`
- Modify: `.github/workflows/ci.yml`
- Create: `docs/guides/supervisor-router.md`
- Modify: `docs/acceptance/p6-supervisor-beta.md`、`README.md`、`docs/README.md`、
  `docs/acceptance/README.md`、`docs/Roadmap.md`、`CHANGELOG.md`

**Interfaces:**
- Consumes: 前 6 个 Task 的测试与模块。
- Produces: 32 文件心脏闸门；Router 运维文档；M3 收口读数与状态面。

- [ ] **Step 1: FOCUS 29 → 32（先写红）**

`tests/unit/test_ci_focus_gate.py`：

```python
    "supervisor/source.py",
    "supervisor/assessor.py",
    "supervisor/router.py",
    "supervisor/service.py",
]


def test_focus_covers_the_thirty_two_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 32
    assert "32 文件" in text  # 步骤名与注释同步
    assert text.count('".py"') == 0  # 不许用通配兜底
```

`.github/workflows/ci.yml` 的步骤名改为
`心脏模块覆盖率闸门（32 文件：loop/checkpoint/errors/context/evidence/orchestration/control/execution/integrations/supervisor 各 ≥90%）`，
并在 `FOCUS` 元组中 `"supervisor/source.py",` 之后追加：

```python
              "supervisor/assessor.py",
              "supervisor/router.py",
              "supervisor/service.py",
```

- [ ] **Step 2: 跑红再跑绿**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py
```

Expected（只改测试、未改 workflow 时）：`assert "32 文件" in text` 失败；补齐 workflow
后 `1 passed`。

- [ ] **Step 3: 写 Router 运维指南**

`docs/guides/supervisor-router.md` 全文：

```markdown
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
```

- [ ] **Step 4: 全量门禁、三解释器与边界复核**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m3-full.txt 2>&1
.venv/bin/ruff check stableagent tests examples benchmarks > /tmp/p6-m3-ruff.txt 2>&1
.venv/bin/ruff format --check stableagent tests examples benchmarks > /tmp/p6-m3-format.txt 2>&1
.venv/bin/mypy > /tmp/p6-m3-mypy.txt 2>&1
.venv/bin/python - <<'PY' > /tmp/p6-m3-coverage.txt 2>&1
import xml.etree.ElementTree as ET

root = ET.parse("coverage.xml").getroot()
wanted = {"supervisor/assessor.py", "supervisor/router.py", "supervisor/service.py"}
for cls in root.iter("class"):
    name = cls.get("filename", "")
    if name in wanted:
        lines = cls.findall("./lines/line")
        hit = sum(1 for line in lines if int(line.get("hits", "0")) > 0)
        print(f"{name}: {hit}/{len(lines)} = {hit / len(lines):.2%}")
PY
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m3-l1.txt 2>&1
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py > /tmp/p6-m3-c1.txt 2>&1
grep -rnE "(from|import) +stableagent\.supervisor" --include='*.py' \
  stableagent/orchestration stableagent/control stableagent/execution stableagent/integrations \
  > /tmp/p6-m3-reverse.txt 2>&1 || true
git diff --check > /tmp/p6-m3-diffcheck.txt 2>&1
```

三解释器全量（与 M2 相同，不用 `--no-cov`，避免只验证新文件）：

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m3-py310.txt 2>&1
env -u NO_PROXY -u no_proxy .matrix/py311/bin/pytest -q > /tmp/p6-m3-py311.txt 2>&1
env -u NO_PROXY -u no_proxy .matrix/py312/bin/pytest -q > /tmp/p6-m3-py312.txt 2>&1
```

逐项读回 `/tmp/p6-m3-*.txt`，确认：全量 `0 failed`、三解释器 passed 数一致、三文件覆盖率均 `≥90%`、ruff
`All checks passed!`、format 已格式化、mypy `Success`、L1/C1 两个 diff 为空、反向 import
0 行、`git diff --check` 无输出。**只回填实测数字，不预填。**

- [ ] **Step 5: 回填账本与状态面**

- §1 的 M3-1…M3-8 全部改 `[x]`，每条写明用例文件、提交号与关键断言。
- M3 标题下状态改为 `M3 收口（Task 1~7 全部完成）`。
- §3 验证命令补 `tests/unit/test_supervisor_assessor.py`、
  `tests/unit/test_supervisor_router.py`、`tests/unit/test_supervisor_service.py`。
- §4 保持 `M3 展开为 7 个 Task / 38 个 Step`（与 Task 1~7 的 Step 数一致）。
- §9 末尾追加"出口读数"小节，粘贴 Step 4 的实测值。
- `README.md` P6 行、`docs/README.md` 主线、`docs/acceptance/README.md`、`docs/Roadmap.md`
  改为 `M3 完成（8/8）；里程碑 3/6`，并链接 `docs/guides/supervisor-router.md`。
- `CHANGELOG.md` 顶部新增 `### 实现（P6 M3 Router Intake，2026-11-09）`。

- [ ] **Step 6: 提交**

```bash
git add tests/unit/test_ci_focus_gate.py .github/workflows/ci.yml \
  docs/guides/supervisor-router.md docs/acceptance/p6-supervisor-beta.md \
  README.md docs/README.md docs/acceptance/README.md docs/Roadmap.md CHANGELOG.md
git commit -m "docs: close p6 m3 with router intake evidence"
```

---

### M3 出口读数（2026-11-09，落盘 `/tmp/p6-m3-*.txt` 后读回）

- 全量：`804 passed, 6 skipped, 0 failed`（19.54s，总覆盖 `98.76%`）。
- 三解释器：`.venv`（3.10）/ `.matrix/py311`（3.11.16）/ `.matrix/py312`（3.12.14）
  均为 `804 passed, 6 skipped`。
- 新增三文件覆盖率：`supervisor/assessor.py` `31/31 = 100.00%`；
  `supervisor/router.py` `267/274 = 97.45%`；`supervisor/service.py`
  `167/172 = 97.09%`（均 ≥90%）。
- 静态：`ruff check` `All checks passed!`；`ruff format --check`
  `151 files already formatted`；`mypy` `Success: no issues found in 68 source files`。
- 边界：`git diff 234f620..HEAD --stat -- stableagent/core/` 与
  `... -- stableagent/orchestration/service.py` 均为 0 行；下层包真实 import
  `stableagent.supervisor` 的 grep 命中 0 行；`git diff --check` 无输出。
- 计划预期偏差（以实测为准）：Task 1 计划写 `7 passed`，实际 6 例；Task 2 计划写
  `40 passed`，实际 51 例（含 M3 前存量）；Task 5 计划写 `8 passed`，实际 7 例；
  Task 6 计划写 `15 passed` / 合计 `38 passed`，实际 Service 14 例、合计 37 例。
  Task 7 为把 `service.py` 覆盖率从 86.63% 抬到闸门线，补了 8 例错误路径/审计用例，
  最终 Service 22 例、三文件合计 51 例。

## 10. M3 自检记录（writing-plans）

- **规格覆盖：** WP-6.3 第 1 条 → Task 3（`receive` 唯一入口、duplicate）；
  第 2 条 → Task 3（扇出与 sequence）；第 3 条 → Task 3（Policy gate 三档）与 Task 1；
  第 4 条 → Task 5/6（七条命令与权限）；第 5 条 → Task 3 + Task 6（own/context、
  PR own）；第 6 条 → Task 3（结束/stop/重复语义）与 Task 5（stop 记录）。
- **类型一致性：** `AutonomyMode` / `AssessmentContext` / `CandidateAssessment` 只在
  Task 1 定义；`RouterConfig` / `RequestSignal` / `Reception` / `RouterError` 只在 Task 3
  定义；`SupervisorService` 的构造签名从 Task 5 起不变，Task 6 只追加方法；
  `record_decision` / `ensure_subscription` / `latest_trigger` 三处 helper 只定义一次。
- **无占位符：** 所有实现步骤给出完整代码；Task 7 的出口读数在实测后回填，不预填数字。
- **边界：** `supervisor` 不 import `execution` / `integrations`（Task 2 的 Source 变更
  仍在 M2 包内）；`receive` / 命令各只开一个事务（L3）；外部正文只进 digest/Input（L4）；
  `resume` 的 `when="launch"` 与审计行是 L5 的唯一新增复检点；`auto` 构造期拒绝与
  `forbidden merge` 保持 L6。

## 11. M4 任务级实施计划（writing-plans 产出，2026-11-09）

**Goal：** 让 Runtime 常驻地把 Source、Router、Scheduler 与 Outbox 串成一轮可恢复的
poll → route → tick → effect；单实例锁、暂停与优雅停止都落库，崩溃后不丢 Trigger、
不重复外部写入。

**Architecture：** 单实例锁与暂停控制复用 `SupervisorStore` 同库同事务，落在
`runtime_lock` / `runtime_control` 两张单例行；`supervisor/runtime.py` 只做编排与生命周期，
业务规则仍在 Router / SupervisorService / OrchestrationService。启动顺序固定为
"锁 → P4 恢复 → 读暂停位 → 补路由未路由 Trigger"，任何 effect/worker 派发都留到 `step()`。
停止只等 `Scheduler.wait_idle` 的宽限期，不取消 Worker、不删 Ticket/Effect/checkpoint。

**Spec：** [P6 实施规格](../specs/p6-single-repository-super-agent-beta.md) §2.4 与 WP-6.4；
运行控制口径依赖 [ADR-06](../design-decisions/06-super-agent-router.md)。

**边界裁定（维护者确认）：**

- 单实例锁用库内 `runtime` 行（`runtime_lock` 单例 + owner/TTL/心跳），随库走、可审计，
  不引入 `fcntl` 跨平台差异。
- `pause_processing` 落库内控制行（`runtime_control` 单例），重启后仍暂停；Source 仍可
  记录 Trigger，但 Router/Scheduler/外部 effect 全部停摆。
- `status` 只给运行态摘要（锁、暂停、Source 健康、队列、effect/审批计数）；待人工首屏的
  注意力排序留给 M5，不在 M4 抢跑。

**Global Constraints（每个 Task 都隐含遵守）：**

- L1：`stableagent/core/` 对 `234f620` 零 diff；约束 1：`orchestration/service.py` 零字符改动。
- L2：方向锁 `supervisor → integrations → execution → control → orchestration → core`；
  `runtime.py` 只 import supervisor 既有组件与 orchestration 公开入口，不 import
  `execution` / `integrations`。
- L3：`Store.transaction()` 体内不得出现 `await`；Runtime 的事务边界都在 Store 方法内部。
- L4：外部正文只进 digest/Input/Report，不参与锁、暂停或命令授权判定。
- L6：不新增 `merge` / `enable_auto_merge` 路径；`EffectKind` 不加成员。
- L7：运行控制命令只认 `RouterConfig.operators`（大小写不敏感）；锁 owner 只是进程标识。
- L8：全部用例离线（Fake GitHub / 确定性时钟）；定向测试加 `--no-cov`。

---

### Task 1：Runtime 锁/控制行与未路由查询（M4-1）

**Files:**
- Modify: `stableagent/supervisor/model.py`
- Modify: `stableagent/supervisor/__init__.py`
- Modify: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_supervisor_store.py`

**Interfaces:**
- Consumes: `SupervisorStore(Store)`、`Clock`、既有 `triggers` / `decision_triggers` /
  `tickets` / `assignments` / `effects` 表。
- Produces:
  - `SUPERVISOR_SCHEMA_VERSION == "p6-supervisor-v2"`
  - `RuntimeLock(owner, acquired_at, heartbeat_at, expires_at)`、
    `RuntimeControl(paused, reason, actor, changed_at, generation)`
  - `SupervisorStore.get_runtime_lock() -> RuntimeLock | None`
  - `acquire_runtime_lock(owner, *, now, ttl_ms) -> bool`
  - `heartbeat_runtime_lock(owner, *, now, ttl_ms) -> bool`
  - `release_runtime_lock(owner) -> bool`
  - `get_runtime_control() -> RuntimeControl`
  - `set_runtime_paused(paused, *, actor, now, reason=None) -> RuntimeControl`
  - `list_unrouted_triggers(*, limit=100) -> list[Trigger]`
  - `count_tickets(*, status) -> int`、`count_assignments(*, status) -> int`、
    `count_due_effects(now_ms) -> int`

- [ ] **Step 1: 写失败测试**

把 `tests/unit/test_supervisor_store.py` 顶部已有 import 扩写为（ruff `I` 规则启用，
同一模块不得拆成两条 import）：

```python
from stableagent.orchestration.model import AssignmentStatus, Mandate, Ticket, TicketStatus
from stableagent.orchestration.service import OrchestrationService
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    Decision,
    DecisionOutcome,
    DecisionStrength,
    RuntimeControl,
    RuntimeLock,
    SourceCursor,
    SourceHealth,
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from tests.support import FixedClock, SequenceIds
```

在文件末尾追加：

```python
def test_schema_v2_seeds_runtime_control_and_no_lock(store: SupervisorStore) -> None:
    assert SUPERVISOR_SCHEMA_VERSION == "p6-supervisor-v2"
    assert store.schema_version() == "p6-supervisor-v2"
    assert store.get_runtime_lock() is None
    assert store.get_runtime_control() == RuntimeControl()


def test_runtime_lock_is_single_owner_until_ttl_expires(store: SupervisorStore) -> None:
    assert store.acquire_runtime_lock("a", now=100, ttl_ms=50) is True
    assert store.acquire_runtime_lock("b", now=149, ttl_ms=50) is False
    assert store.acquire_runtime_lock("a", now=150, ttl_ms=50) is True  # 同 owner 重入
    assert store.get_runtime_lock() == RuntimeLock(
        owner="a", acquired_at=150, heartbeat_at=150, expires_at=200
    )
    assert store.acquire_runtime_lock("b", now=200, ttl_ms=50) is True  # 过期可接管
    assert store.release_runtime_lock("a") is False
    assert store.release_runtime_lock("b") is True
    assert store.get_runtime_lock() is None


def test_heartbeat_extends_lease_only_for_the_owner(store: SupervisorStore) -> None:
    assert store.acquire_runtime_lock("a", now=0, ttl_ms=10) is True
    assert store.heartbeat_runtime_lock("b", now=5, ttl_ms=10) is False
    assert store.heartbeat_runtime_lock("a", now=5, ttl_ms=10) is True
    lock = store.get_runtime_lock()
    assert lock is not None
    assert (lock.heartbeat_at, lock.expires_at) == (5, 15)


def test_pause_control_survives_reopen(tmp_path: Path) -> None:
    clock = FixedClock()
    path = tmp_path / "p6.db"
    store = SupervisorStore(path, clock=clock)
    try:
        paused = store.set_runtime_paused(True, actor="alice", now=100, reason="maintenance")
        assert paused.paused is True and paused.generation == 1
    finally:
        store.close()
    reopened = SupervisorStore(path, clock=clock)
    try:
        control = reopened.get_runtime_control()
        assert (control.paused, control.actor, control.reason) == (True, "alice", "maintenance")
        resumed = reopened.set_runtime_paused(False, actor="alice", now=200)
        assert resumed.paused is False and resumed.generation == 2
    finally:
        reopened.close()


def test_unrouted_triggers_are_exactly_the_ones_without_decisions(
    store: SupervisorStore,
) -> None:
    routed = _trigger("issue:1", id="tg-1")
    pending = _trigger("issue:2", id="tg-2")
    store.insert_trigger(routed)
    store.insert_trigger(pending)
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=[routed.id],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=["inferred_candidate"],
            policy_version="p5-v1",
            request_digest="d-1",
            created_at=1,
        )
    )
    assert [trigger.id for trigger in store.list_unrouted_triggers()] == [pending.id]


def test_runtime_counts_read_only_facts(tmp_path: Path) -> None:
    clock, ids = FixedClock(), SequenceIds()
    store = SupervisorStore(tmp_path / "counts.db", clock=clock)
    try:
        service = OrchestrationService(store, clock=clock, ids=ids)
        # queue_ticket 一次落：QUEUED 票 + PENDING Assignment + due launch effect
        service.queue_ticket(service.create_ticket("计数").id)
        now = clock.now_ms()
        assert store.count_tickets(status=TicketStatus.QUEUED) == 1
        assert store.count_assignments(status=AssignmentStatus.PENDING) == 1
        assert store.count_due_effects(now) == 1
        assert store.count_due_effects(now - 1) == 0
    finally:
        store.close()
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_store.py
```

Expected：`ImportError: cannot import name 'RuntimeControl'`（或
`AttributeError: 'SupervisorStore' object has no attribute 'get_runtime_lock'`）。

- [ ] **Step 3: 模型与 schema 版本**

`stableagent/supervisor/model.py` 末尾追加：

```python
class RuntimeLock(BaseModel):
    """单实例锁行：随库走，进程崩溃后按 expires_at 过期，不依赖 OS 文件锁。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str
    acquired_at: int
    heartbeat_at: int
    expires_at: int


class RuntimeControl(BaseModel):
    """运行控制行：pause 跨重启保留；generation 让每次状态变更可审计。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paused: bool = False
    reason: str | None = None
    actor: str | None = None
    changed_at: int = 0
    generation: int = 0
```

`stableagent/supervisor/__init__.py` 改：

```python
SUPERVISOR_SCHEMA_VERSION = "p6-supervisor-v2"
```

- [ ] **Step 4: Store 表、种子与查询**

`stableagent/supervisor/store.py`。

顶部 import 改为：

```python
from stableagent.orchestration.model import (
    AssignmentStatus,
    Clock,
    EffectState,
    TicketStatus,
)
```

`from stableagent.supervisor.model import (...)` 追加 `RuntimeControl, RuntimeLock`。

`_SUPERVISOR_SCHEMA` 末尾追加：

```python
    "runtime_lock": (
        "CREATE TABLE IF NOT EXISTS runtime_lock ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " owner TEXT NOT NULL, acquired_at INTEGER NOT NULL,"
        " heartbeat_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
    ),
    "runtime_control": (
        "CREATE TABLE IF NOT EXISTS runtime_control ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " paused INTEGER NOT NULL DEFAULT 0, reason TEXT, actor TEXT,"
        " changed_at INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0)"
    ),
```

`__init__` 里 `schema_migrations` 的 `INSERT OR IGNORE` 之前追加：

```python
            self._conn.execute(
                "INSERT OR IGNORE INTO runtime_control"
                " (id, paused, reason, actor, changed_at, generation)"
                " VALUES (1, 0, NULL, NULL, 0, 0)"
            )
```

模块级追加两个行转换器：

```python
def _row_to_runtime_lock(row: sqlite3.Row) -> RuntimeLock:
    return RuntimeLock(
        owner=row["owner"],
        acquired_at=row["acquired_at"],
        heartbeat_at=row["heartbeat_at"],
        expires_at=row["expires_at"],
    )


def _row_to_runtime_control(row: sqlite3.Row) -> RuntimeControl:
    return RuntimeControl(
        paused=bool(row["paused"]),
        reason=row["reason"],
        actor=row["actor"],
        changed_at=row["changed_at"],
        generation=row["generation"],
    )
```

`SupervisorStore` 类末尾追加：

```python
    # ------------------------------------------------------------ runtime lock
    def get_runtime_lock(self) -> RuntimeLock | None:
        row = self._conn.execute("SELECT * FROM runtime_lock WHERE id = 1").fetchone()
        return _row_to_runtime_lock(row) if row else None

    def acquire_runtime_lock(self, owner: str, *, now: int, ttl_ms: int) -> bool:
        """单例行：无行则插入；其他 owner 未过期时拒绝；同 owner 可重入刷新租约。"""
        with self.transaction():
            self._conn.execute(
                "INSERT OR IGNORE INTO runtime_lock"
                " (id, owner, acquired_at, heartbeat_at, expires_at) VALUES (1, ?, ?, ?, ?)",
                (owner, now, now, now + ttl_ms),
            )
            cursor = self._conn.execute(
                "UPDATE runtime_lock SET owner = ?, acquired_at = ?, heartbeat_at = ?,"
                " expires_at = ? WHERE id = 1 AND (owner = ? OR expires_at <= ?)",
                (owner, now, now, now + ttl_ms, owner, now),
            )
            return int(cursor.rowcount) == 1

    def heartbeat_runtime_lock(self, owner: str, *, now: int, ttl_ms: int) -> bool:
        cursor = self._conn.execute(
            "UPDATE runtime_lock SET heartbeat_at = ?, expires_at = ?"
            " WHERE id = 1 AND owner = ?",
            (now, now + ttl_ms, owner),
        )
        return int(cursor.rowcount) == 1

    def release_runtime_lock(self, owner: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM runtime_lock WHERE id = 1 AND owner = ?", (owner,)
        )
        return int(cursor.rowcount) == 1

    # ---------------------------------------------------------- runtime control
    def get_runtime_control(self) -> RuntimeControl:
        row = self._conn.execute("SELECT * FROM runtime_control WHERE id = 1").fetchone()
        if row is None:  # pragma: no cover - 构造函数必种子
            return RuntimeControl()
        return _row_to_runtime_control(row)

    def set_runtime_paused(
        self,
        paused: bool,
        *,
        actor: str,
        now: int,
        reason: str | None = None,
    ) -> RuntimeControl:
        self._conn.execute(
            "UPDATE runtime_control SET paused = ?, reason = ?, actor = ?,"
            " changed_at = ?, generation = generation + 1 WHERE id = 1",
            (int(paused), reason, actor, now),
        )
        return self.get_runtime_control()

    # -------------------------------------------------------- 启动补路由与计数
    def list_unrouted_triggers(self, *, limit: int = 100) -> list[Trigger]:
        """已持久化但没有任何 Decision 的 Trigger：Source 落库后、Router 路由前崩溃的窗口。"""
        rows = self._conn.execute(
            "SELECT t.* FROM triggers t"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM decision_triggers dt WHERE dt.trigger_id = t.id"
            " )"
            " ORDER BY t.received_at ASC, t.id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_trigger(row) for row in rows]

    def count_tickets(self, *, status: TicketStatus) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE status = ?", (status.value,)
        ).fetchone()
        return int(row[0])

    def count_assignments(self, *, status: AssignmentStatus) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE status = ?", (status.value,)
        ).fetchone()
        return int(row[0])

    def count_due_effects(self, now_ms: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM effects WHERE due_at <= ? AND state IN (?, ?)",
            (now_ms, EffectState.PENDING.value, EffectState.RETRY_WAIT.value),
        ).fetchone()
        return int(row[0])
```

- [ ] **Step 5: 跑绿与静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_store.py
.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_store.py
.venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_store.py
```

Expected：全文件通过（Task 1 追加 6 例）；ruff 两项均无输出。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/model.py stableagent/supervisor/__init__.py \
  stableagent/supervisor/store.py tests/unit/test_supervisor_store.py
git commit -m "feat: persist supervisor runtime lock and pause control"
```

---

### Task 2：Runtime 核心、启动恢复与状态摘要（M4-2）

**Files:**
- Create: `stableagent/supervisor/runtime.py`
- Test: `tests/unit/test_supervisor_runtime.py`

**Interfaces:**
- Consumes: Task 1 的锁/控制/未路由/计数方法；`RouterService.receive`、
  `OrchestrationService.recover`、`Scheduler.tick`、`EffectDispatcher.dispatch_due`、
  `Source.poll`、`SourceError`。
- Produces:
  - `RuntimeConfig(router, owner, poll_limit, lock_ttl_ms, stop_grace_ms, step_interval_ms)`
  - `RuntimeStart(recovery: RecoverySummary, routed: int, paused: bool)`
  - `RuntimeStep(paused, received, routed, duplicates, enqueued, dispatched, external,
    running, source_error: str | None = None)`
  - `RuntimeStop(drained: bool, running_assignments: int)`
  - `RuntimeStatus`（冻结 pydantic：started/paused/owner/paused_at/paused_by/reason/
    source_health/queued_tickets/running_assignments/due_effects/failed_effects/
    pending_approvals）
  - `RuntimeLockError`、`RuntimeStateError`
  - `Runtime.start() -> RuntimeStart`、`async step() -> RuntimeStep`、
    `status() -> RuntimeStatus`
  - `RuntimeBundle`、`build_runtime(...) -> RuntimeBundle`
  - Task 3 只追加 `pause/resume`、`RuntimeController` 与 bundle 的 `controller` 字段。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_runtime.py` 全文：

```python
"""WP-6.4：单实例锁、启动恢复与每轮 poll/route/tick 推进。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind
from stableagent.supervisor.model import Thread, ThreadKind, Trigger, TriggerKind
from stableagent.supervisor.router import RouterConfig
from stableagent.supervisor.runtime import (
    RuntimeConfig,
    RuntimeLockError,
    build_runtime,
)
from stableagent.supervisor.source import PollBatch, SourceEvent, SourceTransientError
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _event(number: int = 1) -> SourceEvent:
    key = f"issue:o/r#{number}"
    thread = Thread(
        key=key,
        repository="o/r",
        kind=ThreadKind.ISSUE,
        external_ref=f"#{number}",
        last_trigger_at=1,
    )
    trigger = Trigger(
        id=f"tg-{number}",
        external_id=f"github:o/r:issue:{number}",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=[key],
        occurred_at=1,
        received_at=1,
        payload={
            "resource": {"number": number, "user": {"login": "alice"}},
            "deleted": False,
            "request": {"text": f"task {number}", "mentioned": True, "url": None},
        },
    )
    return SourceEvent(trigger=trigger, thread=thread)


class ScriptedSource:
    def __init__(self, store: SupervisorStore) -> None:
        self._store = store
        self.events: list[SourceEvent] = []
        self.fail_next: Exception | None = None
        self.calls = 0

    def feed(self, event: SourceEvent) -> None:
        self.events.append(event)

    async def poll(self, *, limit: int = 100) -> PollBatch:
        self.calls += 1
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        batch, self.events = self.events[:limit], self.events[limit:]
        with self._store.transaction():
            for event in batch:
                self._store.insert_trigger(event.trigger)
                self._store.upsert_thread(event.thread)
                if event.channel is not None:
                    self._store.insert_channel(event.channel)
        return PollBatch(events=batch)


class RecordingExecutor:
    def __init__(self) -> None:
        self.launched: list[str] = []
        self.recovered: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.cancelled: list[str] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)
        await self.gate.wait()

    async def deliver(self, assignment, ticket, inputs) -> None:
        self.delivered.extend((ticket.id, item.payload) for item in inputs)

    async def cancel(self, assignment) -> None:
        self.cancelled.append(assignment.id)

    async def recover(self, assignment, ticket) -> None:
        self.recovered.append(assignment.id)


async def _noop(effect) -> None:
    return None


@pytest.fixture
def env(tmp_path: Path):
    clock, ids = FixedClock(), SequenceIds()
    store = SupervisorStore(tmp_path / "runtime.db", clock=clock)
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY}),
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    executor = RecordingExecutor()
    source = ScriptedSource(store)
    bundle = build_runtime(
        store=store,
        policy=policy,
        source=source,
        executor=executor,
        effect_handlers={EffectKind.GITHUB_COMMENT: _noop},
        config=RuntimeConfig(
            router=RouterConfig(
                repository="o/r",
                operators=frozenset({"alice"}),
                actions=("read_repository",),
            ),
            owner="test-runtime",
            lock_ttl_ms=1_000,
            stop_grace_ms=10,
            step_interval_ms=0,
        ),
        clock=clock,
        ids=ids,
    )
    try:
        yield bundle, store, clock, source, executor
    finally:
        store.close()


async def test_start_routes_persisted_triggers_and_acquires_lock(env) -> None:
    bundle, store, _, _, _ = env
    event = _event()
    with store.transaction():
        store.insert_trigger(event.trigger)
        store.upsert_thread(event.thread)

    started = bundle.runtime.start()

    assert started.routed == 1 and started.paused is False
    lock = store.get_runtime_lock()
    assert lock is not None and lock.owner == "test-runtime"
    assert store.list_unrouted_triggers() == []
    assert len(store.list_decisions()) == 1


async def test_start_refuses_second_owner_without_recovery(env) -> None:
    bundle, store, clock, _, _ = env
    assert store.acquire_runtime_lock("other", now=clock.now_ms(), ttl_ms=60_000) is True

    with pytest.raises(RuntimeLockError):
        bundle.runtime.start()

    assert store.get_runtime_lock().owner == "other"  # 没有抢占，也没有写恢复记录


async def test_step_polls_routes_and_dispatches_launch(env) -> None:
    bundle, store, _, source, executor = env
    source.feed(_event())
    bundle.runtime.start()

    step = await bundle.runtime.step()
    await asyncio.sleep(0)  # create_task 的 launch 在下一轮事件循环记录

    assert (step.received, step.routed, step.dispatched) == (1, 1, 1)
    assert len(store.list_tickets()) == 1
    assert len(executor.launched) == 1
    assert store.get_ticket(store.list_tickets()[0].id).status.value == "running"


async def test_step_while_paused_records_triggers_without_routing(env) -> None:
    bundle, store, clock, source, _ = env
    store.set_runtime_paused(True, actor="alice", now=clock.now_ms(), reason="maintenance")
    bundle.runtime.start()
    source.feed(_event())

    step = await bundle.runtime.step()

    assert step.paused is True and step.received == 1 and step.routed == 0
    assert step.dispatched == 0
    assert store.list_decisions() == []
    assert len(store.list_unrouted_triggers()) == 1


async def test_step_fails_when_lease_is_lost(env) -> None:
    bundle, store, clock, _, _ = env
    bundle.runtime.start()
    clock.advance(1_001)  # 租约过期后另一进程接管
    assert store.acquire_runtime_lock("other", now=clock.now_ms(), ttl_ms=60_000) is True

    with pytest.raises(RuntimeLockError):
        await bundle.runtime.step()


async def test_source_error_does_not_stop_effect_dispatch(env) -> None:
    bundle, _, clock, source, _ = env
    bundle.orchestration.queue_ticket(bundle.orchestration.create_ticket("排队").id)
    source.fail_next = SourceTransientError("503")
    bundle.runtime.start()

    step = await bundle.runtime.step()

    assert step.source_error == "source.transient"
    assert step.dispatched == 1  # Source 失败不阻断已有 effect


async def test_status_reports_runtime_facts_without_start(env) -> None:
    bundle, _, _, _, _ = env
    offline = bundle.runtime.status()
    assert offline.started is False and offline.owner is None
    assert offline.paused is False

    bundle.runtime.start()
    online = bundle.runtime.status()
    assert online.started is True and online.owner == "test-runtime"
    assert online.queued_tickets == 0 and online.source_health == ()
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py
```

Expected：`ModuleNotFoundError: No module named 'stableagent.supervisor.runtime'`。

- [ ] **Step 3: 写 Runtime 与组成根**

`stableagent/supervisor/runtime.py` 全文：

```python
"""常驻运行面：单实例锁、启动恢复与 poll/route/tick 推进（规格 WP-6.4）。

方向锁：只依赖 supervisor 既有组件与 orchestration 公开入口，不 import execution/integrations。
组成根只注入依赖，不存业务规则（WP-6.4 第 1 条）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from stableagent.control.audit import AuditWriter
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.executor import WorkerExecutor
from stableagent.orchestration.model import (
    AssignmentStatus,
    Clock,
    EffectKind,
    IdFactory,
    TicketStatus,
)
from stableagent.orchestration.outbox import EffectDispatcher, EffectHandler
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService, RecoverySummary
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import SourceCursor
from stableagent.supervisor.router import RouterConfig, RouterService
from stableagent.supervisor.service import SupervisorService
from stableagent.supervisor.source import PollBatch, Source, SourceError
from stableagent.supervisor.store import SupervisorStore


class RuntimeLockError(RuntimeError):
    """单实例锁被占用或中途丢失：调用方不得继续写库。"""


class RuntimeStateError(RuntimeError):
    """Runtime 生命周期被打乱（未 start 就 step），不是可重试的外部错误。"""


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    router: RouterConfig
    owner: str = "supervisor-runtime"
    poll_limit: int = 100
    lock_ttl_ms: int = 30_000
    stop_grace_ms: int = 5_000
    step_interval_ms: int = 1_000


@dataclass(frozen=True)
class RuntimeStart:
    recovery: RecoverySummary
    routed: int
    paused: bool


@dataclass(frozen=True)
class RuntimeStep:
    paused: bool
    received: int
    routed: int
    duplicates: int
    enqueued: int
    dispatched: int
    external: int
    running: int
    source_error: str | None = None


@dataclass(frozen=True)
class RuntimeStop:
    drained: bool
    running_assignments: int


class RuntimeStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    started: bool
    paused: bool
    owner: str | None
    paused_at: int | None
    paused_by: str | None
    reason: str | None
    source_health: tuple[SourceCursor, ...]
    queued_tickets: int
    running_assignments: int
    due_effects: int
    failed_effects: int
    pending_approvals: int


@dataclass(frozen=True)
class RuntimeBundle:
    runtime: "Runtime"
    router: RouterService
    service: SupervisorService
    orchestration: OrchestrationService
    scheduler: Scheduler
    effects: EffectDispatcher


class Runtime:
    def __init__(
        self,
        *,
        store: SupervisorStore,
        source: Source,
        router: RouterService,
        orchestration: OrchestrationService,
        scheduler: Scheduler,
        effects: EffectDispatcher,
        config: RuntimeConfig,
        clock: Clock,
    ) -> None:
        self._store = store
        self._source = source
        self._router = router
        self._orchestration = orchestration
        self._scheduler = scheduler
        self._effects = effects
        self._config = config
        self._clock = clock
        self._started = False

    @property
    def config(self) -> RuntimeConfig:
        return self._config

    def start(self) -> RuntimeStart:
        """先锁、再恢复、后补路由；任何 effect/worker 派发都留到 step()。"""
        now = self._clock.now_ms()
        if not self._store.acquire_runtime_lock(
            self._config.owner, now=now, ttl_ms=self._config.lock_ttl_ms
        ):
            lock = self._store.get_runtime_lock()
            raise RuntimeLockError(f"运行锁被占用：owner={lock.owner if lock else '?'}")
        # 锁已在手 = 上一进程确定死亡：RUNNING 收成 INTERRUPTED，并重置在途 effect。
        recovery = self._orchestration.recover()
        control = self._store.get_runtime_control()
        routed = 0 if control.paused else self._route_pending()
        self._started = True
        return RuntimeStart(recovery=recovery, routed=routed, paused=control.paused)

    async def step(self) -> RuntimeStep:
        if not self._started:
            raise RuntimeStateError("Runtime 未 start：step 不得写库")
        self._renew_lock()
        source_error: str | None = None
        try:
            batch = await self._source.poll(limit=self._config.poll_limit)
        except SourceError as error:
            # Source 失败已由 Source 记进 source_health；Runtime 只让本轮空转，不杀调度。
            batch = PollBatch()
            source_error = error.code
        control = self._store.get_runtime_control()
        if control.paused:
            return RuntimeStep(
                paused=True,
                received=len(batch.events),
                routed=0,
                duplicates=batch.duplicates,
                enqueued=0,
                dispatched=0,
                external=0,
                running=len(self._scheduler.running_assignments()),
                source_error=source_error,
            )
        self._renew_lock()
        routed = duplicates = 0
        for event in batch.events:
            reception = self._router.receive(event.trigger)
            if reception.duplicate:
                duplicates += 1
            else:
                routed += 1
        tick = await self._scheduler.tick()
        external = await self._effects.dispatch_due()
        return RuntimeStep(
            paused=False,
            received=len(batch.events),
            routed=routed,
            duplicates=duplicates + batch.duplicates,
            enqueued=tick.enqueued,
            dispatched=tick.dispatched,
            external=external,
            running=tick.running,
            source_error=source_error,
        )

    def status(self) -> RuntimeStatus:
        now = self._clock.now_ms()
        lock = self._store.get_runtime_lock()
        control = self._store.get_runtime_control()
        started = lock is not None and lock.expires_at > now
        return RuntimeStatus(
            started=started,
            paused=control.paused,
            owner=lock.owner if started and lock is not None else None,
            paused_at=control.changed_at if control.paused else None,
            paused_by=control.actor if control.paused else None,
            reason=control.reason if control.paused else None,
            source_health=tuple(self._store.list_source_cursors()),
            queued_tickets=self._store.count_tickets(status=TicketStatus.QUEUED),
            running_assignments=self._store.count_assignments(status=AssignmentStatus.RUNNING),
            due_effects=self._store.count_due_effects(now),
            failed_effects=len(self._store.list_failed_effects()),
            pending_approvals=len(self._store.list_pending_approvals()),
        )

    def _renew_lock(self) -> None:
        if not self._store.heartbeat_runtime_lock(
            self._config.owner, now=self._clock.now_ms(), ttl_ms=self._config.lock_ttl_ms
        ):
            self._started = False
            raise RuntimeLockError("运行锁已丢失：owner 变更或租约过期")

    def _route_pending(self) -> int:
        routed = 0
        for trigger in self._store.list_unrouted_triggers(limit=self._config.poll_limit):
            if not self._router.receive(trigger).duplicate:
                routed += 1
        return routed


def build_runtime(
    *,
    store: SupervisorStore,
    policy: StaticPolicy,
    source: Source,
    executor: WorkerExecutor,
    effect_handlers: Mapping[EffectKind, EffectHandler],
    config: RuntimeConfig,
    clock: Clock,
    ids: IdFactory,
    scheduler_max_concurrency: int = 1,
    audit: AuditWriter | None = None,
) -> RuntimeBundle:
    """组成根：只注入依赖；observe 评估器与 P4 服务在此装配。"""
    router = RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        config.router,
        clock=clock,
        ids=ids,
        audit=audit,
    )
    service = SupervisorService(store, policy, router, clock=clock, ids=ids, audit=audit)
    orchestration = OrchestrationService(store, clock=clock, ids=ids)
    scheduler = Scheduler(
        store=store,
        service=orchestration,
        executor=executor,
        clock=clock,
        max_concurrency=scheduler_max_concurrency,
    )
    effects = EffectDispatcher(store, effect_handlers, clock=clock)
    runtime = Runtime(
        store=store,
        source=source,
        router=router,
        orchestration=orchestration,
        scheduler=scheduler,
        effects=effects,
        config=config,
        clock=clock,
    )
    return RuntimeBundle(
        runtime=runtime,
        router=router,
        service=service,
        orchestration=orchestration,
        scheduler=scheduler,
        effects=effects,
    )
```

- [ ] **Step 4: 跑绿**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py
```

Expected：`7 passed`。

- [ ] **Step 5: 静态与提交**

```bash
.venv/bin/ruff check stableagent/supervisor/runtime.py tests/unit/test_supervisor_runtime.py
.venv/bin/ruff format --check stableagent/supervisor/runtime.py \
  tests/unit/test_supervisor_runtime.py
.venv/bin/mypy
git add stableagent/supervisor/runtime.py tests/unit/test_supervisor_runtime.py
git commit -m "feat: add supervisor runtime start, step and status"
```

---

### Task 3：暂停/恢复与运行控制面（M4-3）

**Files:**
- Modify: `stableagent/supervisor/runtime.py`
- Modify: `tests/unit/test_supervisor_runtime.py`

**Interfaces:**
- Consumes: Task 1 的 `set_runtime_paused` / `get_runtime_control`；`RouterConfig.operators`。
- Produces:
  - `Runtime.pause(*, actor, reason=None) -> RuntimeControl`
  - `Runtime.resume(*, actor, reason=None) -> RuntimeControl`（started 时立即补路由）
  - `RuntimeController(runtime, supervisor, orchestration)` 的
    `status()` / `pause_processing()` / `resume_processing()`
  - `RuntimeBundle.controller`

- [ ] **Step 1: 写失败测试**

把文件顶部已有的 `from stableagent.supervisor.router import RouterConfig` 扩写为：

```python
from stableagent.supervisor.router import RouterConfig, RouterPermissionError
```

文件末尾追加：

```python
async def test_pause_persists_and_resume_routes_pending_triggers(env) -> None:
    bundle, store, _, source, _ = env
    bundle.runtime.start()
    control = bundle.controller.pause_processing(actor="alice", reason="maintenance")
    assert control.paused is True
    assert store.get_runtime_control().reason == "maintenance"
    source.feed(_event())

    paused_step = await bundle.runtime.step()
    assert paused_step.received == 1 and store.list_decisions() == []

    resumed = bundle.controller.resume_processing(actor="alice")
    assert resumed.paused is False
    assert len(store.list_decisions()) == 1  # 暂停期间记录的 Trigger 立即补路由


async def test_pause_control_is_visible_in_status(env) -> None:
    bundle, _, _, _, _ = env
    bundle.controller.pause_processing(actor="alice", reason="maintenance")

    status = bundle.controller.status()

    assert status.paused is True
    assert status.paused_by == "alice" and status.reason == "maintenance"
    assert status.paused_at is not None


async def test_control_commands_are_operator_gated_case_insensitively(env) -> None:
    bundle, _, _, _, _ = env
    with pytest.raises(RouterPermissionError):
        bundle.controller.pause_processing(actor="mallory")
    control = bundle.controller.pause_processing(actor="ALICE")
    assert control.paused is True and control.actor == "ALICE"
    with pytest.raises(RouterPermissionError):
        bundle.controller.resume_processing(actor="mallory")
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py
```

Expected：`AttributeError: 'RuntimeBundle' object has no attribute 'controller'`。

- [ ] **Step 3: 实现 pause/resume 与控制器**

`stableagent/supervisor/runtime.py`。

把两条已有 import 扩写为（`RouterStateError` 留到 Task 5 再引入，避免本 Task 的
`F401`）：

```python
from stableagent.supervisor.model import RuntimeControl, SourceCursor
from stableagent.supervisor.router import (
    RouterConfig,
    RouterPermissionError,
    RouterService,
)
```

`RuntimeBundle` 追加字段：

```python
    controller: "RuntimeController"
```

`Runtime` 追加方法（放在 `status()` 之后）：

```python
    def pause(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        return self._store.set_runtime_paused(
            True, actor=actor, now=self._clock.now_ms(), reason=reason
        )

    def resume(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        control = self._store.set_runtime_paused(
            False, actor=actor, now=self._clock.now_ms(), reason=reason
        )
        if self._started:
            self._renew_lock()
            self._route_pending()
        return control
```

`build_runtime` 里构造并注入控制器：

```python
    controller = RuntimeController(runtime, service, orchestration)
    return RuntimeBundle(
        runtime=runtime,
        controller=controller,
        router=router,
        service=service,
        orchestration=orchestration,
        scheduler=scheduler,
        effects=effects,
    )
```

文件末尾追加：

```python
class RuntimeController:
    """运行控制命令面：只做 operator 校验与委托，业务规则仍在既有服务层。"""

    def __init__(
        self,
        runtime: Runtime,
        supervisor: SupervisorService,
        orchestration: OrchestrationService,
    ) -> None:
        self._runtime = runtime
        self._supervisor = supervisor
        self._orchestration = orchestration

    def status(self) -> RuntimeStatus:
        return self._runtime.status()

    def pause_processing(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        self._require_operator(actor)
        return self._runtime.pause(actor=actor, reason=reason)

    def resume_processing(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        self._require_operator(actor)
        return self._runtime.resume(actor=actor, reason=reason)

    def _require_operator(self, actor: str) -> None:
        allowed = {name.casefold() for name in self._runtime.config.router.operators}
        if actor.casefold() not in allowed:
            raise RouterPermissionError(f"actor 不是 operator：{actor}")
```

> `RuntimeStateError` 在本 Task 仍未使用；它由 Task 2 的 `step()` 抛出，保留导出。

- [ ] **Step 4: 跑绿与静态**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py
.venv/bin/ruff check stableagent/supervisor/runtime.py tests/unit/test_supervisor_runtime.py
.venv/bin/ruff format --check stableagent/supervisor/runtime.py \
  tests/unit/test_supervisor_runtime.py
```

Expected：`10 passed`。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/runtime.py tests/unit/test_supervisor_runtime.py
git commit -m "feat: add supervisor pause, resume and runtime controller"
```

---

### Task 4：优雅停止与常驻循环（M4-4）

**Files:**
- Modify: `stableagent/orchestration/scheduler.py`
- Modify: `stableagent/supervisor/runtime.py`
- Modify: `tests/unit/test_scheduler.py`
- Modify: `tests/unit/test_supervisor_runtime.py`

**Interfaces:**
- Consumes: `Scheduler.running_assignments()`。
- Produces:
  - `Scheduler.wait_idle(*, timeout_ms) -> bool`（只等待，绝不取消）
  - `Runtime.stop() -> RuntimeStop`、`Runtime.serve(*, max_steps=None) -> int`
  - `Runtime._stop_requested`

- [ ] **Step 1: Scheduler 失败测试**

`tests/unit/test_scheduler.py` 末尾追加：

```python
async def test_wait_idle_returns_immediately_when_idle(env) -> None:
    _, _, _, scheduler, _ = env
    assert await scheduler.wait_idle(timeout_ms=0) is True


async def test_wait_idle_times_out_without_cancelling_the_worker(env) -> None:
    _, service, executor, scheduler, _ = env
    executor.gate.clear()
    _queued(service, "运行中")
    await scheduler.tick()
    await asyncio.sleep(0)
    assert scheduler.running_assignments()

    assert await scheduler.wait_idle(timeout_ms=1) is False
    assert scheduler.running_assignments()  # 只等待，绝不取消
    assert executor.cancelled == []

    executor.gate.set()
    assert await scheduler.wait_idle(timeout_ms=1_000) is True
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_scheduler.py
```

Expected：`AttributeError: 'Scheduler' object has no attribute 'wait_idle'`。

- [ ] **Step 3: 实现 wait_idle**

`stableagent/orchestration/scheduler.py` 在 `running_assignments()` 之后追加：

```python
    async def wait_idle(self, *, timeout_ms: int) -> bool:
        """只等待进行中的 Worker 结束；宽限期到点返回 False，绝不取消任务。"""
        tasks = [task for task in self._tasks.values() if not task.done()]
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=max(timeout_ms / 1000.0, 0.0))
        return not pending
```

- [ ] **Step 4: Runtime 停止测试**

`tests/unit/test_supervisor_runtime.py` 的 fixture teardown 改为：

```python
    try:
        yield bundle, store, clock, source, executor
    finally:
        executor.gate.set()
        await bundle.runtime.stop()
        store.close()
```

文件末尾追加：

```python
async def test_stop_waits_for_the_worker_without_cancelling_it(env) -> None:
    bundle, store, _, source, executor = env
    executor.gate.clear()
    source.feed(_event())
    bundle.runtime.start()
    await bundle.runtime.step()
    await asyncio.sleep(0)
    assert executor.launched

    stopping = asyncio.create_task(bundle.runtime.stop())
    await asyncio.sleep(0)
    assert stopping.done() is False  # 宽限期内不硬杀
    assert executor.cancelled == []

    executor.gate.set()
    result = await stopping
    assert result.drained is True and result.running_assignments == 0
    assert store.get_runtime_lock() is None


async def test_stop_is_idempotent_and_releases_the_lock(env) -> None:
    bundle, store, _, _, _ = env
    bundle.runtime.start()

    first = await bundle.runtime.stop()
    second = await bundle.runtime.stop()

    assert first.drained is True and second.drained is True
    assert store.get_runtime_lock() is None


async def test_serve_runs_bounded_steps_then_stops(env) -> None:
    bundle, store, _, source, executor = env
    source.feed(_event())
    bundle.runtime.start()

    steps = await bundle.runtime.serve(max_steps=2)

    assert steps == 2
    assert len(executor.launched) == 1
    assert store.get_runtime_lock() is None
```

- [ ] **Step 5: 实现 stop/serve**

`stableagent/supervisor/runtime.py` 顶部追加：

```python
import asyncio
```

`Runtime.__init__` 末尾追加：

```python
        self._stop_requested = False
```

`Runtime` 追加方法（放在 `resume()` 之后）：

```python
    async def serve(self, *, max_steps: int | None = None) -> int:
        """常驻循环；max_steps 只给测试/演练用，正常部署不传。"""
        steps = 0
        try:
            while not self._stop_requested and (max_steps is None or steps < max_steps):
                await self.step()
                steps += 1
                if self._stop_requested or (max_steps is not None and steps >= max_steps):
                    break
                await asyncio.sleep(self._config.step_interval_ms / 1000.0)
        finally:
            await self.stop()
        return steps

    async def stop(self) -> RuntimeStop:
        """先停 intake（不再 step），再等宽限期；不取消 Worker、不删任何历史。"""
        self._stop_requested = True
        if not self._started:
            return RuntimeStop(drained=True, running_assignments=0)
        drained = await self._scheduler.wait_idle(timeout_ms=self._config.stop_grace_ms)
        running = len(self._scheduler.running_assignments())
        self._store.release_runtime_lock(self._config.owner)
        self._started = False
        return RuntimeStop(drained=drained, running_assignments=running)
```

- [ ] **Step 6: 跑绿、静态与提交**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_scheduler.py \
  tests/unit/test_supervisor_runtime.py
.venv/bin/ruff check stableagent/orchestration/scheduler.py \
  stableagent/supervisor/runtime.py tests/unit/test_scheduler.py \
  tests/unit/test_supervisor_runtime.py
.venv/bin/ruff format --check stableagent/orchestration/scheduler.py \
  stableagent/supervisor/runtime.py tests/unit/test_scheduler.py \
  tests/unit/test_supervisor_runtime.py
git add stableagent/orchestration/scheduler.py stableagent/supervisor/runtime.py \
  tests/unit/test_scheduler.py tests/unit/test_supervisor_runtime.py
git commit -m "feat: add graceful supervisor stop and bounded serve loop"
```

---

### Task 5：retry_ticket / stop_ticket / reevaluate 控制命令（M4-5）

**Files:**
- Modify: `stableagent/supervisor/runtime.py`
- Modify: `tests/unit/test_supervisor_runtime.py`

**Interfaces:**
- Consumes: `OrchestrationService.retry_ticket / get_ticket`、`SupervisorService.stop /
  reevaluate`、`RouterStateError`。
- Produces:
  - `RuntimeController.retry_ticket(ticket_id, *, actor) -> Assignment`（仅 FAILED）
  - `RuntimeController.stop_ticket(ticket_id, *, actor, reason=None) -> Ticket`
  - `RuntimeController.reevaluate(trigger_id, *, actor) -> Decision`

- [ ] **Step 1: 写失败测试**

把 `tests/unit/test_supervisor_runtime.py` 顶部三处已有 import 分别扩写为（只新增
`TicketAction` / `TicketStatus`、`DecisionOutcome`、`RouterStateError`，避免重复导入触发
`F811`）：

```python
from stableagent.orchestration.model import EffectKind, TicketAction, TicketStatus
from stableagent.supervisor.model import (
    DecisionOutcome,
    Thread,
    ThreadKind,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import (
    RouterConfig,
    RouterPermissionError,
    RouterStateError,
)
```

文件末尾追加：

```python
async def test_retry_ticket_only_accepts_failed_and_is_operator_gated(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()
    await bundle.runtime.step()
    ticket = store.list_tickets()[0]

    with pytest.raises(RouterStateError):
        bundle.controller.retry_ticket(ticket.id, actor="alice")
    with pytest.raises(RouterPermissionError):
        bundle.controller.retry_ticket(ticket.id, actor="mallory")

    bundle.orchestration.transition_ticket(ticket.id, TicketAction.FAIL)
    assignment = bundle.controller.retry_ticket(ticket.id, actor="alice")
    assert assignment.attempt == 2
    assert store.get_ticket(ticket.id).status is TicketStatus.QUEUED


async def test_stop_ticket_cancels_and_records_a_stop_decision(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()
    await bundle.runtime.step()
    ticket = store.list_tickets()[0]

    stopped = bundle.controller.stop_ticket(ticket.id, actor="alice", reason="duplicate")

    assert stopped.status is TicketStatus.CANCELLED
    assert DecisionOutcome.STOP in [decision.outcome for decision in store.list_decisions()]


async def test_reevaluate_appends_an_attempt_without_new_triggers(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()
    await bundle.runtime.step()
    trigger = store.list_triggers()[0]
    before = len(store.list_triggers())

    decision = bundle.controller.reevaluate(trigger.id, actor="alice")

    assert decision.attempt == 2
    assert len(store.list_triggers()) == before
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py
```

Expected：`AttributeError: 'RuntimeController' object has no attribute 'retry_ticket'`。

- [ ] **Step 3: 实现三条命令**

`stableagent/supervisor/runtime.py` 的 orchestration import 追加 `Assignment`、`Ticket`：

```python
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Clock,
    EffectKind,
    IdFactory,
    Ticket,
    TicketStatus,
)
```

`supervisor.model` import 追加 `Decision`：

```python
from stableagent.supervisor.model import Decision, RuntimeControl, SourceCursor
```

`RuntimeController` 追加方法（放在 `resume_processing` 之后）：

```python
    def retry_ticket(self, ticket_id: str, *, actor: str) -> Assignment:
        self._require_operator(actor)
        ticket = self._orchestration.get_ticket(ticket_id).ticket
        if ticket.status is not TicketStatus.FAILED:
            raise RouterStateError(
                f"retry_ticket 只接受 FAILED：{ticket.status.value}"
            )
        return self._orchestration.retry_ticket(ticket_id)

    def stop_ticket(
        self, ticket_id: str, *, actor: str, reason: str | None = None
    ) -> Ticket:
        self._require_operator(actor)
        return self._supervisor.stop(ticket_id, actor=actor, reason=reason)

    def reevaluate(self, trigger_id: str, *, actor: str) -> Decision:
        self._require_operator(actor)
        return self._supervisor.reevaluate(trigger_id, actor=actor)
```

- [ ] **Step 4: 跑绿与静态**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py
.venv/bin/ruff check stableagent/supervisor/runtime.py tests/unit/test_supervisor_runtime.py
.venv/bin/ruff format --check stableagent/supervisor/runtime.py \
  tests/unit/test_supervisor_runtime.py
```

Expected：`13 passed`。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/runtime.py tests/unit/test_supervisor_runtime.py
git commit -m "feat: add supervisor retry, stop and reevaluate commands"
```

---

### Task 6：五个崩溃窗口的恢复证明（M4-6）

**Files:**
- Create: `tests/integration/test_superagent_recovery.py`

**Interfaces:**
- Consumes: Task 2~5 的 `build_runtime` / `Runtime.start/step`；真实 `GitHubSource` +
  `FakeGitHubReadClient`；Task 1 的 `list_unrouted_triggers` 与锁过期语义。
- Produces: WP-6.4 第 4 条要求的五个崩溃窗口证据；不新增生产代码。

- [ ] **Step 1: 写集成测试**

`tests/integration/test_superagent_recovery.py` 全文：

```python
"""WP-6.4 第 4 条：五个崩溃窗口在重启后不丢语义、不重复外部写入。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
)
from stableagent.orchestration.model import (
    AssignmentStatus,
    Effect,
    EffectKind,
    IdFactory,
)
from stableagent.supervisor.router import RouterConfig
from stableagent.supervisor.runtime import RuntimeConfig, build_runtime
from stableagent.supervisor.source import GitHubSource
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _issue_item(number: int = 1) -> GitHubReadItem:
    return GitHubReadItem(
        kind=GitHubReadKind.ISSUE,
        occurred_at=1,
        resource={
            "number": number,
            "user": {"login": "alice"},
            "body": "@agent please add rate limiter",
        },
    )


class RecordingExecutor:
    def __init__(self) -> None:
        self.launched: list[str] = []
        self.recovered: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)

    async def deliver(self, assignment, ticket, inputs) -> None:
        self.delivered.extend((ticket.id, item.payload) for item in inputs)

    async def cancel(self, assignment) -> None:
        self.cancelled.append(assignment.id)

    async def recover(self, assignment, ticket) -> None:
        self.recovered.append(assignment.id)


def _build(
    *,
    store: SupervisorStore,
    client: FakeGitHubReadClient,
    executor: RecordingExecutor,
    handlers,
    clock: FixedClock,
    ids: IdFactory,
    owner: str,
):
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY}),
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    source = GitHubSource(store, client, repository="o/r", bot_login="agent", clock=clock)
    bundle = build_runtime(
        store=store,
        policy=policy,
        source=source,
        executor=executor,
        effect_handlers=handlers,
        config=RuntimeConfig(
            router=RouterConfig(
                repository="o/r",
                operators=frozenset({"alice"}),
                actions=("read_repository",),
            ),
            owner=owner,
            lock_ttl_ms=1_000,
            stop_grace_ms=10,
            step_interval_ms=0,
        ),
        clock=clock,
        ids=ids,
    )
    return bundle, source


@pytest.fixture
def env(tmp_path: Path):
    clock, ids = FixedClock(), SequenceIds()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    client = FakeGitHubReadClient([_issue_item()])
    try:
        yield store, client, clock, ids
    finally:
        store.close()


async def test_trigger_persisted_before_routing_is_routed_on_restart(env) -> None:
    store, client, clock, ids = env
    first, source = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    first.runtime.start()
    await source.poll(limit=10)  # Trigger/Thread/cursor 已落库，路由尚未发生
    assert store.list_decisions() == []

    clock.advance(2_000)  # 进程死亡：锁过期
    restarted, _ = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-b",
    )

    started = restarted.runtime.start()

    assert started.routed == 1
    assert len(store.list_decisions()) == 1
    assert store.list_unrouted_triggers() == []


async def test_ticket_committed_before_dispatch_is_dispatched_after_restart(env) -> None:
    store, client, clock, ids = env
    first, source = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    first.runtime.start()
    batch = await source.poll(limit=10)
    first.router.receive(batch.events[0].trigger)  # 五件原子写已提交，launch 尚未派发
    assert len(store.list_tickets()) == 1

    clock.advance(2_000)
    executor = RecordingExecutor()
    restarted, _ = _build(
        store=store,
        client=client,
        executor=executor,
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-b",
    )
    restarted.runtime.start()

    step = await restarted.runtime.step()
    await asyncio.sleep(0)

    assert step.dispatched == 1
    assert len(store.list_tickets()) == 1
    assert len(executor.launched) == 1


async def test_github_comment_effect_is_neither_lost_nor_replayed(env) -> None:
    store, client, clock, ids = env
    calls: list[str] = []

    async def comment_handler(effect) -> None:
        calls.append(effect.id)

    handlers = {EffectKind.GITHUB_COMMENT: comment_handler}
    first, _ = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers=handlers,
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    first.runtime.start()
    store.enqueue_effect(
        Effect(
            id="ef-comment",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="comment:1",
            payload={"repository": "o/r", "url": "https://example/1", "body": "done"},
            due_at=clock.now_ms(),
        )
    )

    clock.advance(2_000)
    restarted, _ = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers=handlers,
        clock=clock,
        ids=ids,
        owner="runtime-b",
    )
    restarted.runtime.start()
    await restarted.runtime.step()
    assert calls == ["ef-comment"]

    clock.advance(2_000)
    third, _ = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers=handlers,
        clock=clock,
        ids=ids,
        owner="runtime-c",
    )
    third.runtime.start()
    await third.runtime.step()
    assert calls == ["ef-comment"]  # 已成功 effect 不重放


async def test_worker_run_crash_becomes_interrupted_and_relaunched(env) -> None:
    store, client, clock, ids = env
    first_executor = RecordingExecutor()
    first, _ = _build(
        store=store,
        client=client,
        executor=first_executor,
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    first.runtime.start()
    await first.runtime.step()
    await asyncio.sleep(0)
    ticket = store.list_tickets()[0]
    assignment = store.list_assignments(ticket.id)[0]
    assert assignment.status is AssignmentStatus.RUNNING

    clock.advance(2_000)
    second_executor = RecordingExecutor()
    restarted, _ = _build(
        store=store,
        client=client,
        executor=second_executor,
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-b",
    )

    started = restarted.runtime.start()
    assert started.recovery.interrupted_assignments == 1
    assert started.recovery.relaunched == 1

    await restarted.runtime.step()
    await asyncio.sleep(0)
    assert second_executor.recovered == [assignment.id]
    assert second_executor.launched == []  # 走 recover，不冷启动


async def test_cursor_update_failure_rolls_back_the_whole_poll(env, monkeypatch) -> None:
    store, client, clock, ids = env
    bundle, source = _build(
        store=store,
        client=client,
        executor=RecordingExecutor(),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    bundle.runtime.start()

    def boom(cursor) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(store, "put_source_cursor", boom)
        with pytest.raises(sqlite3.OperationalError):
            await bundle.runtime.step()

    assert store.get_source_cursor("github") is None
    assert store.list_triggers() == []  # Trigger/Thread 与 cursor 同批回滚

    step = await bundle.runtime.step()
    assert step.received == 1
    assert len(store.list_triggers()) == 1
    assert len(store.list_decisions()) == 1  # 游标未推进，下一次 poll 不丢事件
```

- [ ] **Step 2: 跑绿**

```bash
.venv/bin/pytest -q --no-cov tests/integration/test_superagent_recovery.py
```

Expected：`5 passed`。若出现失败，按失败窗口修生产代码（只允许改
`supervisor/runtime.py` 或 Task 1 的 Store 方法），不得改测试预期。

- [ ] **Step 3: 与本轮前四个 Task 合跑**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py \
  tests/unit/test_scheduler.py tests/integration/test_superagent_recovery.py
```

Expected：全部通过。

- [ ] **Step 4: 静态与提交**

```bash
.venv/bin/ruff check tests/integration/test_superagent_recovery.py
.venv/bin/ruff format --check tests/integration/test_superagent_recovery.py
git add tests/integration/test_superagent_recovery.py
# 若 Step 2 修过生产代码，把对应文件一并 add
git commit -m "test: cover five supervisor runtime crash windows"
```

---

### Task 7：CI FOCUS 33、Runtime 指南与 M4 出口（M4-7）

**Files:**
- Modify: `tests/unit/test_ci_focus_gate.py`
- Modify: `.github/workflows/ci.yml`
- Create: `docs/guides/supervisor-runtime.md`
- Modify: `docs/acceptance/p6-supervisor-beta.md`、`README.md`、`docs/README.md`、
  `docs/acceptance/README.md`、`docs/Roadmap.md`、`CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1~6 的模块与测试。
- Produces: 33 文件心脏闸门；Runtime 运维文档；M4 收口读数与状态面。

- [ ] **Step 1: FOCUS 32 → 33（先写红）**

`tests/unit/test_ci_focus_gate.py`：

```python
    "supervisor/service.py",
    "supervisor/runtime.py",
]


def test_focus_covers_the_thirty_three_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 33
    assert "33 文件" in text  # 步骤名与注释同步
    assert text.count('".py"') == 0  # 不许用通配兜底
```

`.github/workflows/ci.yml` 的步骤名改为
`心脏模块覆盖率闸门（33 文件：loop/checkpoint/errors/context/evidence/orchestration/control/execution/integrations/supervisor 各 ≥90%）`，
并在 `"supervisor/service.py",` 之后追加：

```python
              "supervisor/runtime.py",
```

- [ ] **Step 2: 跑红再跑绿**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py
```

Expected：只改测试时 `assert "33 文件" in text` 失败；补齐 workflow 后 `1 passed`。

- [ ] **Step 3: 写 Runtime 运维指南**

`docs/guides/supervisor-runtime.md` 全文：

```markdown
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
```

- [ ] **Step 4: 全量门禁（先落盘再读回）**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m4-full.txt 2>&1; tail -5 /tmp/p6-m4-full.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff check stableagent tests examples benchmarks \
  > /tmp/p6-m4-ruff.txt 2>&1; cat /tmp/p6-m4-ruff.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff format --check stableagent tests examples benchmarks \
  > /tmp/p6-m4-format.txt 2>&1; tail -3 /tmp/p6-m4-format.txt
env -u NO_PROXY -u no_proxy .venv/bin/mypy > /tmp/p6-m4-mypy.txt 2>&1; tail -3 /tmp/p6-m4-mypy.txt
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m4-l1.txt; cat /tmp/p6-m4-l1.txt
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py \
  > /tmp/p6-m4-c1.txt; cat /tmp/p6-m4-c1.txt
grep -rn "stableagent.supervisor" stableagent/core stableagent/orchestration \
  stableagent/control stableagent/execution stableagent/integrations \
  > /tmp/p6-m4-reverse.txt; cat /tmp/p6-m4-reverse.txt
git diff --check > /tmp/p6-m4-diffcheck.txt; cat /tmp/p6-m4-diffcheck.txt
```

Expected：全量全绿；`stableagent/supervisor/runtime.py` 行覆盖率 ≥90%（见全量输出的
`runtime.py` 行）；L1/C1/reverse/diffcheck 四个文件均为空。

- [ ] **Step 5: 三解释器与覆盖率读数**

```bash
env -u NO_PROXY -u no_proxy .matrix/py311/bin/pytest -q > /tmp/p6-m4-py311.txt 2>&1; tail -3 /tmp/p6-m4-py311.txt
env -u NO_PROXY -u no_proxy .matrix/py312/bin/pytest -q > /tmp/p6-m4-py312.txt 2>&1; tail -3 /tmp/p6-m4-py312.txt
grep "runtime.py" /tmp/p6-m4-full.txt
```

Expected：3.11 / 3.12 与 3.10 同为 `passed, skipped`；`runtime.py` 覆盖率 ≥90%。

- [ ] **Step 6: 账本与状态面回填**

`docs/acceptance/p6-supervisor-beta.md`：

- §1 的 M4 小节：把 `### M4：常驻调度、恢复与运行控制（规格 WP-6.4；待 M3 收口后展开）`
  改为收口状态行 `> **状态：M4 收口（Task 1~7 全部完成）。**`，并按 M1~M3 的账本格式
  补齐 `- [x] **M4-1**` ~ `- [x] **M4-7**` 七条（每条写对应提交与实测用例数）。
- §3 在 service 测试行之后追加：

```bash
.venv/bin/pytest -q tests/unit/test_supervisor_runtime.py \
  tests/integration/test_superagent_recovery.py
```

- §4 追加 M4 的 Task/Step 计数（以实际 `- [ ] **Step` 条数为准）。
- §11 末尾追加 `### M4 出口读数（2026-11-09，落盘 /tmp/p6-m4-*.txt 后读回）`，
  记录全量/三解释器/覆盖率/静态/边界四零/计划偏差，全部以读回文件为准。

状态面：

```markdown
| P6 | Single-repository Super-Agent Beta | 🟡 active — M4 done (7/7); milestones 4/6 |
```

`docs/README.md` 第 52 行、`docs/acceptance/README.md` 第 12 行同步为
`P6 进行中（M4 完成，7/7；里程碑 4/6）`；`docs/Roadmap.md` 的"当前进度"改为
M1~M4 完成，`下一步：M5 最小监管面与注意力协议（规格 WP-6.5）`；`CHANGELOG.md` 在
`## [Unreleased]` 下新增 `### 实现（P6 M4 Runtime，2026-11-09）`，写锁/暂停/停止/
恢复五窗口/FOCUS 33 与门禁读数。

- [ ] **Step 7: 提交**

```bash
git add tests/unit/test_ci_focus_gate.py .github/workflows/ci.yml \
  docs/guides/supervisor-runtime.md docs/acceptance/p6-supervisor-beta.md \
  README.md docs/README.md docs/acceptance/README.md docs/Roadmap.md CHANGELOG.md
git commit -m "docs: close p6 m4 with runtime recovery evidence"
```

---

### M4 自检记录（writing-plans）

- **规格覆盖：** WP-6.4 第 1 条 → Task 2（`build_runtime` 组成根只注入依赖）；
  第 2 条 → Task 2（锁 → recover → 读暂停 → 补路由，派发留到 step）；
  第 3 条 → Task 4（宽限期等待、不取消、不删历史、释放锁）；
  第 4 条 → Task 6（五个崩溃窗口各有独立用例）；
  第 5 条 → Task 3/5（pause/resume/status/retry/stop/reevaluate）；
  §2.4 的"Source 仍可记录 Trigger" → Task 2 的暂停用例（received=1、routed=0）。
- **类型一致性：** `RuntimeConfig` / `RuntimeStart` / `RuntimeStep` / `RuntimeStop` /
  `RuntimeStatus` / `RuntimeLockError` / `RuntimeStateError` / `RuntimeBundle` 只在
  Task 2 定义；Task 3 只追加 `controller` 字段与 `RuntimeController`；Task 4 只追加
  `wait_idle` / `stop` / `serve`；Task 5 只追加三条命令。Store 方法名在 Task 1 一次定义，
  后续 Task 不再改名。
- **无占位符：** 每个实现步骤给出完整代码；出口读数与用例数在实测后回填，不预填数字。
- **自检修正（落进 Task 正文）：** 测试 import 一律写"扩写既有语句"以免 `F811`/`I001`；
  计数用例只依赖 `queue_ticket` 一次写入的真实事实（不再额外插 Assignment/Effect）；
  "租约丢失"用例先让 `other` owner 在过期后接管，心跳才会失败；§1 的 M4 账本改为
  新建 M4-1~M4-7 七条收口记录（原 §1 只有标题行）。
- **边界：** `runtime.py` 不 import `execution` / `integrations`（GitHub 写只经注入的
  effect handler）；所有事务边界在 Store 方法内部（L3）；暂停/锁/命令授权不读外部正文
  （L4）；`EffectKind` 不加成员（L6）；`status` 不含 payload 或日志正文（I6）。

### M4 出口读数（2026-11-09，落盘 `/tmp/p6-m4-*.txt` 后读回）

- **全量（3.10）：** `/tmp/p6-m4-full.txt` → `833 passed, 6 skipped in 19.80s`，
  `TOTAL 5004 60 99%`，`Required test coverage of 70.0% reached. Total coverage: 98.80%`。
- **心脏覆盖率：** 同文件 `stableagent/supervisor/runtime.py 201 2 99% 161, 188`（≥90%）。
- **静态：** `/tmp/p6-m4-ruff.txt` → `All checks passed!`；`/tmp/p6-m4-format.txt` →
  `154 files already formatted`；`/tmp/p6-m4-mypy.txt` →
  `Success: no issues found in 69 source files`。
- **三解释器：** `/tmp/p6-m4-py311.txt` → `833 passed, 6 skipped`（3.11.16）；
  `/tmp/p6-m4-py312.txt` → `833 passed, 6 skipped`（3.12.14）。
- **边界四零：** `/tmp/p6-m4-l1.txt`（L1 `core/` 相对 `234f620`）、
  `/tmp/p6-m4-c1.txt`（约束 1 `orchestration/service.py`）、
  `/tmp/p6-m4-reverse.txt`（下层包 import supervisor）、
  `/tmp/p6-m4-diffcheck.txt`（空白错误）四个文件均为 0 字节。
- **计划偏差：**
  1. §4 原写 M4 为 `7 个 Task / 38 个 Step`，实际 `- [ ] **Step` 计数为 39（Task 1 六步、
     Task 4 六步、Task 7 八步），已按实际改为 39。
  2. Task 5 的累计预期原写 `13 passed`，实测 `16 passed`——Task 4 新增的 3 个用例在
     计划里未计入累计预期，实现无需改动。
  3. ruff `UP037` 在 `from __future__ import annotations` 下要求去掉 `runtime: "Runtime"`
     的引号；`ruff format` 把 `store.py` 中 heartbeat/release 的 SQL 折叠为单行，
     两者均只影响排版。
  4. Task 2 的"租约丢失"用例需先让另一 owner 在过期后接管，心跳才会因 owner 变更失败
     （自检阶段已修正，实现未改）。

## 12. M5 任务级实施计划（writing-plans 产出，2026-11-09）

**Goal：** 给单仓库 Beta 一个只读首屏、可审计的写入口与生命周期通知草稿：CLI 只读
`status/ticket/thread`，所有写命令必须带 `--confirm <target-id>`；通知先草拟成
`COMMENT_ISSUE` 审批，人工批准后才由 Runtime 的 effect 发出，拒绝后该 Ticket 永久静默。

**Architecture：** 读侧扩 `supervisor/views.py`（首屏段落 + Ticket 详情）；通知模板与
草拟器落在新 `supervisor/notifications.py`；写侧复用 `ApprovalService` /
`SupervisorService` / `OrchestrationService`，只补"批准与 effect 同事务"、"失败 effect
重试"与 stop 撤销；`supervisor/console.py` 只做 operator 门禁、`--confirm` 校验与渲染，
`supervisor/cli.py` 只做 argparse 与退出码。`Runtime` 在 tick 后、外部 effect 派发前草拟
通知；不新增 `EffectKind`，通知复用 `GITHUB_COMMENT`。

**Spec：** [P6 实施规格](../specs/p6-single-repository-super-agent-beta.md) WP-6.5 与
§2.5；设计决议见同文件 `WP-6.5 设计决议（M5，2026-11-09）`。

**边界裁定（维护者已确认）：**

- CLI-only，仅标准库 argparse；不做 TUI/Web，不发任何外部网络请求。
- 每条写命令必须 `--confirm <target-id>`：approve/deny 传 approval id，stop 传 ticket id，
  retry 传 effect 或 ticket id，reevaluate 传 trigger id；pause/resume 传字面量
  `pause`/`resume`。
- 生命周期通知一律先草拟成 `COMMENT_ISSUE` 审批，人工批准才发；任一 COMMENT_ISSUE
  被 DENIED 后，该 Ticket 不再草拟任何通知（拒绝即静默）。
- 飞书/微信/Slack 等 IM 推送与凭据配置留 P7；M5 不读任何凭据、不写 effect payload 之外
  的通知正文。

**Global Constraints（每个 Task 都隐含遵守）：**

- L1：`stableagent/core/` 对 `234f620` 零 diff；约束 1：`orchestration/service.py` 零字符改动。
- L2：方向锁 `supervisor → integrations → execution → control → orchestration → core`；
  既有包不得 import `stableagent.supervisor`；`supervisor` 不直接 import `integrations`。
- L3：`Store.transaction()` 体内不得出现 `await`；批准 + effect、stop + 撤销都在单一事务内。
- L4：外部正文只进 digest/Report/TicketInput，不参与授权；通知正文只取 Ticket 标题与
  结构化 Report 摘要，不进 prompt/日志。
- L6：不新增 `merge` / `enable_auto_merge` 路径；`EffectKind` 不加成员。
- L7：不读凭据；CLI 配置 `extra="forbid"`，不出现 token/secret 字段。
- L8：全部用例离线（Fake Source / 确定性时钟）；定向测试加 `--no-cov`。

---

### Task 1：首屏段落、注意力目标与稳定链接（M5-1）

**Files:**
- Modify: `stableagent/supervisor/views.py`
- Test: `tests/unit/test_supervisor_views.py`

**Interfaces:**
- Consumes: `SupervisorStore.list_pending_approvals()` / `list_failed_effects()` /
  `list_subscriptions(ticket_id=..., live_only=True)` / `get_thread(key)` /
  `count_tickets` / `count_assignments` / `count_due_effects`。
- Produces:
  - `QueueCounts(queued_tickets, running_assignments, due_effects, failed_effects, pending_approvals)`
  - `StatusSections(attention, source_health, queue)`
  - `AttentionItem(priority, source, target_id, ticket_id, needs, why, actions,
    ticket_link, thread_link, detail)`
  - `thread_url(thread: Thread) -> str | None`
  - `SupervisorViews(store, *, clock: Clock | None = None)`
  - `SupervisorViews.status_sections() -> StatusSections`

- [ ] **Step 1: 写失败测试**

扩写 `tests/unit/test_supervisor_views.py` 顶部既有 import（同一模块保持一条 import，
避免 ruff `I001`/`F811`）：

```python
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Effect,
    EffectKind,
    EffectState,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketInput,
    TicketStatus,
)
from stableagent.supervisor.model import (
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.views import SupervisorViews, thread_url
```

在文件末尾追加：

```python
def test_status_sections_group_attention_health_and_queue(store: SupervisorStore) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    running = _ticket(store, "tk-run", TicketStatus.RUNNING)
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="comment_issue",
        resource_digest="d-1",
        reason="notification:attention",
        requested_at=1,
    )
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=running.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.PENDING,
            due_at=0,
        )
    )
    store.put_source_cursor(SourceCursor(source="github", cursor="9"))

    sections = SupervisorViews(store, clock=FixedClock()).status_sections()

    assert [(item.priority, item.target_id) for item in sections.attention] == [(0, "ap-1")]
    assert [cursor.source for cursor in sections.source_health] == ["github"]
    assert sections.queue.queued_tickets == 0
    assert sections.queue.running_assignments == 1
    assert sections.queue.due_effects == 1
    assert sections.queue.failed_effects == 0
    assert sections.queue.pending_approvals == 1


def test_attention_items_carry_targets_actions_and_links(store: SupervisorStore) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    store.upsert_thread(
        Thread(
            key="issue:o/r#1",
            repository="o/r",
            kind=ThreadKind.ISSUE,
            external_ref="#1",
            last_trigger_at=1,
        )
    )
    store.insert_subscription(
        Subscription(
            id="sb-1",
            ticket_id=waiting.id,
            thread_key="issue:o/r#1",
            mode=SubscriptionMode.OWN,
            created_at=1,
        )
    )
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="comment_issue",
        resource_digest="d-1",
        reason="notification:attention",
        requested_at=1,
    )

    items = SupervisorViews(store).needs_attention()
    approval = next(item for item in items if item.source == "approval")

    assert approval.target_id == "ap-1"
    assert approval.actions == ("approve", "deny", "stop")
    assert approval.needs == "approve or deny the pending external write"
    assert approval.why == "comment_issue requires approval"
    assert approval.ticket_link == "https://github.com/o/r/issues/1"
    assert approval.thread_link == approval.ticket_link


def test_attention_links_stay_empty_without_a_live_subscription(
    store: SupervisorStore,
) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="comment_issue",
        resource_digest="d-1",
        reason="notification:attention",
        requested_at=1,
    )

    approval = next(
        item for item in SupervisorViews(store).needs_attention() if item.source == "approval"
    )

    assert approval.ticket_link is None and approval.thread_link is None


def test_thread_url_maps_issue_pull_request_review_and_repository() -> None:
    assert (
        thread_url(
            Thread(
                key="issue:o/r#7",
                repository="o/r",
                kind=ThreadKind.ISSUE,
                external_ref="#7",
                last_trigger_at=1,
            )
        )
        == "https://github.com/o/r/issues/7"
    )
    assert (
        thread_url(
            Thread(
                key="pr:o/r#8",
                repository="o/r",
                kind=ThreadKind.PULL_REQUEST,
                external_ref="#8",
                last_trigger_at=1,
            )
        )
        == "https://github.com/o/r/pull/8"
    )
    assert (
        thread_url(
            Thread(
                key="review:o/r#9:42",
                repository="o/r",
                kind=ThreadKind.REVIEW,
                external_ref="review:42",
                last_trigger_at=1,
            )
        )
        == "https://github.com/o/r"
    )
    assert (
        thread_url(
            Thread(
                key="repo:o/r",
                repository="o/r",
                kind=ThreadKind.REPOSITORY,
                external_ref="o/r",
                last_trigger_at=1,
            )
        )
        == "https://github.com/o/r"
    )
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_views.py \
  > /tmp/p6-m5-t1-red.txt 2>&1; tail -20 /tmp/p6-m5-t1-red.txt
```

Expected：`ImportError`（`thread_url` 不存在）与 `AttributeError`
（`status_sections` 不存在），不是断言失败。

- [ ] **Step 3: 实现首屏模型、目标映射与链接**

`stableagent/supervisor/views.py` 顶部 import 扩写为（保持一条 import）：

```python
from stableagent.orchestration.model import (
    AssignmentStatus,
    Clock,
    SystemClock,
    TicketStatus,
)
from stableagent.supervisor.model import (
    SourceCursor,
    SubscriptionMode,
    Thread,
    ThreadKind,
)
```

在 `_OPEN_STATUSES` 之后追加：

```python
_ACTIONS_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "approval": ("approve", "deny", "stop"),
    "failed_effect": ("retry", "stop"),
    "waiting_ticket": ("stop",),
    "ready_ticket": ("stop",),
}
_NEEDS_BY_SOURCE = {
    "approval": "approve or deny the pending external write",
    "failed_effect": "retry or stop the failed effect",
    "waiting_ticket": "reply to the ticket or stop it",
    "ready_ticket": "review the result or stop it",
}


class QueueCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    queued_tickets: int
    running_assignments: int
    due_effects: int
    failed_effects: int
    pending_approvals: int


class StatusSections(BaseModel):
    model_config = ConfigDict(frozen=True)

    attention: list[AttentionItem]
    source_health: list[SourceCursor]
    queue: QueueCounts


def thread_url(thread: Thread) -> str | None:
    """展示用链接：Issue/PR 指向具体条目，Review/Repository 按规格回落仓库 URL。"""
    base = f"https://github.com/{thread.repository}"
    if thread.kind is ThreadKind.ISSUE:
        return f"{base}/issues/{thread.external_ref.lstrip('#')}"
    if thread.kind is ThreadKind.PULL_REQUEST:
        return f"{base}/pull/{thread.external_ref.lstrip('#')}"
    return base


def _live_thread(store: SupervisorStore, ticket_id: str) -> Thread | None:
    subscriptions = sorted(
        store.list_subscriptions(ticket_id=ticket_id, live_only=True),
        key=lambda item: (item.mode is not SubscriptionMode.OWN, item.created_at, item.id),
    )
    for subscription in subscriptions:
        thread = store.get_thread(subscription.thread_key)
        if thread is not None:
            return thread
    return None


def ticket_links(store: SupervisorStore, ticket_id: str) -> tuple[str | None, str | None]:
    thread = _live_thread(store, ticket_id)
    if thread is None:
        return None, None
    url = thread_url(thread)
    return url, url


def ticket_url(store: SupervisorStore, ticket_id: str) -> str | None:
    return ticket_links(store, ticket_id)[0]
```

把既有 `AttentionItem` 替换为（新增字段全部带默认值，既有构造点不受影响）：

```python
class AttentionItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    priority: int
    source: Literal["approval", "failed_effect", "waiting_ticket", "ready_ticket"]
    ticket_id: str | None = None
    target_id: str = ""
    needs: str = ""
    why: str = ""
    actions: tuple[str, ...] = ()
    ticket_link: str | None = None
    thread_link: str | None = None
    detail: str = ""
```

把 `SupervisorViews.__init__` 替换为：

```python
def __init__(self, store: SupervisorStore, *, clock: Clock | None = None) -> None:
    self._store = store
    self._clock = clock or SystemClock()
```

把 `needs_attention` 的四处 `AttentionItem(...)` 依次替换为：

```python
for row in self._store.list_pending_approvals():
    ticket_link, thread_link = ticket_links(self._store, row["ticket_id"])
    items.append(
        AttentionItem(
            priority=0,
            source="approval",
            ticket_id=row["ticket_id"],
            target_id=row["id"],
            needs=_NEEDS_BY_SOURCE["approval"],
            why=f"{row['action']} requires approval",
            actions=_ACTIONS_BY_SOURCE["approval"],
            ticket_link=ticket_link,
            thread_link=thread_link,
            detail=f"approval pending: {row['action']}",
        )
    )
for row in self._store.list_failed_effects():
    ticket_link, thread_link = ticket_links(self._store, row["ticket_id"])
    items.append(
        AttentionItem(
            priority=1,
            source="failed_effect",
            ticket_id=row["ticket_id"],
            target_id=row["effect_id"],
            needs=_NEEDS_BY_SOURCE["failed_effect"],
            why=row["last_error"] or "effect failed after retries",
            actions=_ACTIONS_BY_SOURCE["failed_effect"],
            ticket_link=ticket_link,
            thread_link=thread_link,
            detail=f"effect failed: {row['kind']}",
        )
    )
for ticket in self._store.list_tickets(status=TicketStatus.WAITING):
    ticket_link, thread_link = ticket_links(self._store, ticket.id)
    items.append(
        AttentionItem(
            priority=2,
            source="waiting_ticket",
            ticket_id=ticket.id,
            target_id=ticket.id,
            needs=_NEEDS_BY_SOURCE["waiting_ticket"],
            why="ticket is waiting for human input",
            actions=_ACTIONS_BY_SOURCE["waiting_ticket"],
            ticket_link=ticket_link,
            thread_link=thread_link,
            detail="ticket waiting for human input",
        )
    )
for ticket in self._store.list_tickets(status=TicketStatus.READY):
    ticket_link, thread_link = ticket_links(self._store, ticket.id)
    items.append(
        AttentionItem(
            priority=3,
            source="ready_ticket",
            ticket_id=ticket.id,
            target_id=ticket.id,
            needs=_NEEDS_BY_SOURCE["ready_ticket"],
            why="ticket finished and needs review",
            actions=_ACTIONS_BY_SOURCE["ready_ticket"],
            ticket_link=ticket_link,
            thread_link=thread_link,
            detail="ticket ready for review",
        )
    )
```

在 `failed_effects()` 之后追加：

```python
def status_sections(self) -> StatusSections:
    return StatusSections(
        attention=self.needs_attention(),
        source_health=self.source_health(),
        queue=QueueCounts(
            queued_tickets=self._store.count_tickets(status=TicketStatus.QUEUED),
            running_assignments=self._store.count_assignments(
                status=AssignmentStatus.RUNNING
            ),
            due_effects=self._store.count_due_effects(self._clock.now_ms()),
            failed_effects=len(self._store.list_failed_effects()),
            pending_approvals=len(self._store.list_pending_approvals()),
        ),
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_views.py \
  > /tmp/p6-m5-t1-green.txt 2>&1; tail -5 /tmp/p6-m5-t1-green.txt
.venv/bin/ruff check stableagent/supervisor/views.py tests/unit/test_supervisor_views.py
.venv/bin/ruff format --check stableagent/supervisor/views.py \
  tests/unit/test_supervisor_views.py
```

Expected：`10 passed`；ruff 两项均通过。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/views.py tests/unit/test_supervisor_views.py
git commit -m "feat: enrich supervisor first screen with targets and links"
```

---

### Task 2：Ticket 详情与时间线 object_id（M5-2）

**Files:**
- Modify: `stableagent/supervisor/views.py`
- Test: `tests/unit/test_supervisor_views.py`

**Interfaces:**
- Consumes: Task 1 的 `ticket_links` / `ticket_url`；`SupervisorStore.list_assignments` /
  `list_subscriptions` / `get_ticket` / `list_reports` / `list_inputs` /
  `list_decisions`。
- Produces:
  - `TimelineEvent(at, kind, summary, object_id=None)`
  - `AssignmentSummary(id, attempt, status, started_at, ended_at, failure)`
  - `SubscriptionView(id, thread_key, mode, created_at, ended_at)`
  - `TicketDetail(ticket, mandate_id, assignments, subscriptions, ticket_link,
    thread_link, timeline)`
  - `SupervisorViews.ticket_detail(ticket_id) -> TicketDetail`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_views.py` 顶部 import 增加 `AssignmentSummary` 不必要；测试只
读模型字段。文件末尾追加：

```python
def test_ticket_detail_lists_assignments_subscriptions_and_timeline(
    store: SupervisorStore,
) -> None:
    ticket = _ticket(store, "tk-1", TicketStatus.RUNNING)
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=ticket.id, attempt=1, status=AssignmentStatus.RUNNING)
    )
    store.upsert_thread(
        Thread(
            key="issue:o/r#1",
            repository="o/r",
            kind=ThreadKind.ISSUE,
            external_ref="#1",
            last_trigger_at=1,
        )
    )
    store.insert_subscription(
        Subscription(
            id="sb-1",
            ticket_id=ticket.id,
            thread_key="issue:o/r#1",
            mode=SubscriptionMode.OWN,
            created_at=1,
        )
    )
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id="as-1",
            kind=ReportKind.PROGRESS,
            summary="local-restricted exit=0",
            created_at=20,
        )
    )

    detail = SupervisorViews(store).ticket_detail(ticket.id)

    assert detail.ticket.id == ticket.id
    assert detail.mandate_id == ticket.mandate_id
    assert [(item.id, item.attempt, item.status) for item in detail.assignments] == [
        ("as-1", 1, AssignmentStatus.RUNNING)
    ]
    assert [item.thread_key for item in detail.subscriptions] == ["issue:o/r#1"]
    assert detail.ticket_link == "https://github.com/o/r/issues/1"
    assert [event.kind for event in detail.timeline] == ["report"]
    assert detail.timeline[0].object_id == "rp-1"
    assert "SECRET-TASK" not in json.dumps(detail.model_dump())


def test_ticket_detail_unknown_ticket_raises_key_error(store: SupervisorStore) -> None:
    with pytest.raises(KeyError):
        SupervisorViews(store).ticket_detail("tk-missing")


def test_thread_timeline_events_carry_object_ids(store: SupervisorStore) -> None:
    trigger = Trigger(
        id="tg-1",
        external_id="issue:1",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"body": "DO-NOT-LEAK"},
    )
    store.insert_trigger(trigger)
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=["tg-1"],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            policy_version="p6-v1",
            request_digest="d-1",
            created_at=12,
        )
    )

    events = SupervisorViews(store).thread_timeline("issue:acme/widgets#1")

    assert [event.object_id for event in events] == ["tg-1", "dc-1"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_views.py \
  > /tmp/p6-m5-t2-red.txt 2>&1; tail -20 /tmp/p6-m5-t2-red.txt
```

Expected：`AttributeError: 'SupervisorViews' object has no attribute 'ticket_detail'`
以及 `object_id` 断言失败。

- [ ] **Step 3: 实现详情模型与 object_id**

`TimelineEvent` 增加尾字段：

```python
class TimelineEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: int
    kind: Literal["trigger", "decision", "report", "input"]
    summary: str
    object_id: str | None = None
```

新增模型：

```python
class AssignmentSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    attempt: int
    status: AssignmentStatus
    started_at: int | None
    ended_at: int | None
    failure: str | None


class SubscriptionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    thread_key: str
    mode: SubscriptionMode
    created_at: int
    ended_at: int | None


class TicketDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticket: TicketSummary
    mandate_id: str
    assignments: list[AssignmentSummary]
    subscriptions: list[SubscriptionView]
    ticket_link: str | None
    thread_link: str | None
    timeline: list[TimelineEvent]
```

`thread_timeline` 中 trigger 与 decision 的事件补 id：

```python
events.append(
    TimelineEvent(
        at=trigger.occurred_at,
        kind="trigger",
        summary=f"{trigger.kind.value}:{trigger.actor}",
        object_id=trigger.id,
    )
)
...
decisions[decision.id] = TimelineEvent(
    at=decision.created_at,
    kind="decision",
    summary=decision.outcome.value,
    object_id=decision.id,
)
```

`ticket_timeline` 中 report / input / decision 的事件补 id：

```python
events = [
    TimelineEvent(
        at=report.created_at,
        kind="report",
        summary=report.summary,
        object_id=report.id,
    )
    for report in self._store.list_reports(ticket_id)
]
events.extend(
    TimelineEvent(
        at=ticket_input.created_at,
        kind="input",
        summary=f"{ticket_input.kind}:{ticket_input.state}",
        object_id=ticket_input.id,
    )
    for ticket_input in self._store.list_inputs(ticket_id)
)
events.extend(
    TimelineEvent(
        at=decision.created_at,
        kind="decision",
        summary=decision.outcome.value,
        object_id=decision.id,
    )
    for decision in self._store.list_decisions(mandate_id=ticket.mandate_id)
)
```

在 `ticket_timeline` 之后追加：

```python
def ticket_detail(self, ticket_id: str) -> TicketDetail:
    ticket = self._store.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    ticket_link, thread_link = ticket_links(self._store, ticket_id)
    return TicketDetail(
        ticket=TicketSummary(
            id=ticket.id,
            title=ticket.title,
            status=ticket.status,
            created_at=ticket.created_at,
        ),
        mandate_id=ticket.mandate_id,
        assignments=[
            AssignmentSummary(
                id=assignment.id,
                attempt=assignment.attempt,
                status=assignment.status,
                started_at=assignment.started_at,
                ended_at=assignment.ended_at,
                failure=assignment.failure,
            )
            for assignment in self._store.list_assignments(ticket_id)
        ],
        subscriptions=[
            SubscriptionView(
                id=subscription.id,
                thread_key=subscription.thread_key,
                mode=subscription.mode,
                created_at=subscription.created_at,
                ended_at=subscription.ended_at,
            )
            for subscription in self._store.list_subscriptions(ticket_id=ticket_id)
        ],
        ticket_link=ticket_link,
        thread_link=thread_link,
        timeline=self.ticket_timeline(ticket_id),
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_views.py \
  > /tmp/p6-m5-t2-green.txt 2>&1; tail -5 /tmp/p6-m5-t2-green.txt
.venv/bin/ruff check stableagent/supervisor/views.py tests/unit/test_supervisor_views.py
.venv/bin/ruff format --check stableagent/supervisor/views.py \
  tests/unit/test_supervisor_views.py
```

Expected：`13 passed`。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/views.py tests/unit/test_supervisor_views.py
git commit -m "feat: add supervisor ticket detail and timeline ids"
```

---

### Task 3：通知模板、限长与纯函数配置（M5-3）

**Files:**
- Create: `stableagent/supervisor/notifications.py`
- Test: `tests/unit/test_supervisor_notifications.py`

**Interfaces:**
- Consumes: `Ticket`、`Report`、`ReportKind`、`TicketStatus`。
- Produces:
  - `NotificationKind`（`ACCEPTED` / `RESULT` / `ATTENTION` / `FAILURE`）
  - `STATUS_KINDS: dict[TicketStatus, NotificationKind]`
  - `REPORT_KINDS: dict[NotificationKind, ReportKind | None]`
  - `NotificationConfig(enabled=True, max_chars=400, since_ms=0)`
  - `latest_summary(reports, kind) -> str | None`
  - `render_notification(ticket, kind, reports, *, max_chars, url) -> str`

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_supervisor_notifications.py`：

```python
"""WP-6.5：生命周期通知是纯函数；正文只取标题、结构化摘要与稳定链接。"""

from __future__ import annotations

from stableagent.orchestration.model import Report, ReportKind, Ticket
from stableagent.supervisor.notifications import (
    NotificationKind,
    latest_summary,
    render_notification,
)


def _ticket(*, title: str = "Fix parser") -> Ticket:
    return Ticket(
        id="tk-1",
        title=title,
        task="SECRET-TASK",
        created_at=1,
        mandate_id="md-1",
    )


def _report(kind: ReportKind, summary: str, *, created_at: int, report_id: str) -> Report:
    return Report(
        id=report_id,
        ticket_id="tk-1",
        assignment_id="as-1",
        kind=kind,
        summary=summary,
        created_at=created_at,
    )


def test_render_accepted_uses_title_and_link_only() -> None:
    body = render_notification(
        _ticket(),
        NotificationKind.ACCEPTED,
        [],
        max_chars=400,
        url="https://github.com/o/r/issues/1",
    )

    assert body == "[Accepted] Fix parser\nhttps://github.com/o/r/issues/1"
    assert "SECRET-TASK" not in body


def test_render_result_uses_latest_ready_summary() -> None:
    reports = [
        _report(ReportKind.READY, "older", created_at=2, report_id="rp-1"),
        _report(ReportKind.READY, "newer", created_at=3, report_id="rp-2"),
    ]

    body = render_notification(
        _ticket(), NotificationKind.RESULT, reports, max_chars=400, url=None
    )

    assert "newer" in body and "older" not in body


def test_latest_summary_ignores_other_report_kinds() -> None:
    reports = [
        _report(ReportKind.PROGRESS, "progress", created_at=9, report_id="rp-1"),
        _report(ReportKind.FAILED, "failed", created_at=8, report_id="rp-2"),
    ]

    assert latest_summary(reports, ReportKind.READY) is None


def test_render_truncates_to_max_chars_with_ellipsis() -> None:
    body = render_notification(
        _ticket(title="x" * 80),
        NotificationKind.ACCEPTED,
        [],
        max_chars=32,
        url=None,
    )

    assert len(body) == 32
    assert body.endswith("…")


def test_render_omits_summary_when_expected_report_is_missing() -> None:
    body = render_notification(
        _ticket(),
        NotificationKind.FAILURE,
        [],
        max_chars=400,
        url="https://github.com/o/r/issues/1",
    )

    assert body == "[Failed] Fix parser\nhttps://github.com/o/r/issues/1"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_notifications.py \
  > /tmp/p6-m5-t3-red.txt 2>&1; tail -20 /tmp/p6-m5-t3-red.txt
```

Expected：`ModuleNotFoundError: No module named 'stableagent.supervisor.notifications'`。

- [ ] **Step 3: 实现纯函数与配置**

新建 `stableagent/supervisor/notifications.py`：

```python
"""生命周期通知：固定模板、限长与草拟（规格 WP-6.5）。

正文只由 Ticket 标题、对应种类的 Report `summary` 与稳定链接拼成；绝不读取
`Ticket.task`、`Trigger.payload`、`TicketInput.payload` 或日志（L4/L7）。
本模块不 import `runtime` / `integrations`，发送仍由 outbox + ApprovalGate 完成。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from stableagent.orchestration.model import Report, ReportKind, Ticket, TicketStatus


class NotificationKind(str, Enum):
    ACCEPTED = "accepted"
    RESULT = "result"
    ATTENTION = "attention"
    FAILURE = "failure"


STATUS_KINDS: dict[TicketStatus, NotificationKind] = {
    TicketStatus.QUEUED: NotificationKind.ACCEPTED,
    TicketStatus.RUNNING: NotificationKind.ACCEPTED,
    TicketStatus.WAITING: NotificationKind.ATTENTION,
    TicketStatus.READY: NotificationKind.RESULT,
    TicketStatus.FAILED: NotificationKind.FAILURE,
}

REPORT_KINDS: dict[NotificationKind, ReportKind | None] = {
    NotificationKind.ACCEPTED: None,
    NotificationKind.RESULT: ReportKind.READY,
    NotificationKind.ATTENTION: ReportKind.WAITING_FOR_HUMAN,
    NotificationKind.FAILURE: ReportKind.FAILED,
}

_HEADERS = {
    NotificationKind.ACCEPTED: "Accepted",
    NotificationKind.RESULT: "Ready for review",
    NotificationKind.ATTENTION: "Waiting for you",
    NotificationKind.FAILURE: "Failed",
}


class NotificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    max_chars: int = Field(default=400, ge=32)
    since_ms: int = Field(default=0, ge=0)


def latest_summary(reports: list[Report], kind: ReportKind) -> str | None:
    matching = [report for report in reports if report.kind is kind]
    if not matching:
        return None
    latest = max(matching, key=lambda report: (report.created_at, report.id))
    return latest.summary


def render_notification(
    ticket: Ticket,
    kind: NotificationKind,
    reports: list[Report],
    *,
    max_chars: int,
    url: str | None,
) -> str:
    lines = [f"[{_HEADERS[kind]}] {ticket.title}"]
    expected = REPORT_KINDS[kind]
    if expected is not None:
        summary = latest_summary(reports, expected)
        if summary:
            lines.append(summary)
    if url:
        lines.append(url)
    body = "\n".join(lines)
    if len(body) > max_chars:
        return body[: max_chars - 1] + "…"
    return body
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_notifications.py \
  > /tmp/p6-m5-t3-green.txt 2>&1; tail -5 /tmp/p6-m5-t3-green.txt
.venv/bin/ruff check stableagent/supervisor/notifications.py \
  tests/unit/test_supervisor_notifications.py
.venv/bin/ruff format --check stableagent/supervisor/notifications.py \
  tests/unit/test_supervisor_notifications.py
```

Expected：`5 passed`。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/notifications.py \
  tests/unit/test_supervisor_notifications.py
git commit -m "feat: add supervisor notification templates and limits"
```

---

### Task 4：通知草拟器、digest 与审批去重（M5-4）

**Files:**
- Modify: `stableagent/supervisor/views.py`
- Modify: `stableagent/supervisor/notifications.py`
- Modify: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_supervisor_notifications.py`

**Interfaces:**
- Consumes: `ApprovalService.submit(ticket_id, action, digest, reason,
  assignment_id=None)`；`SupervisorStore.list_subscriptions` / `get_thread` /
  `get_mandate` / `list_assignments` / `list_reports`。
- Produces:
  - `SupervisorStore.list_tickets_for_notifications(*, since_ms, limit=200) -> list[Ticket]`
  - `views.comment_url(thread) -> str | None`
  - `NotificationDraft(ticket_id, assignment_id, kind, payload, digest)`
  - `notification_digest(payload) -> str`
  - `notification_effect(draft, *, effect_id, now) -> Effect`
  - `NotificationDrafter(store, approvals, config, *, repository, clock)`
  - `NotificationDrafter.build(ticket_id) -> NotificationDraft | None`
  - `NotificationDrafter.draft_due() -> int`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_notifications.py` 顶部 import 扩写为：

```python
from pathlib import Path

import pytest

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus
from stableagent.orchestration.model import (
    Assignment,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketStatus,
)
from stableagent.supervisor.model import (
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
)
from stableagent.supervisor.notifications import (
    NotificationConfig,
    NotificationDrafter,
    NotificationKind,
    latest_summary,
    render_notification,
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds
```

文件末尾追加：

```python
@pytest.fixture
def env(tmp_path: Path):
    clock = FixedClock()
    ids = SequenceIds()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    return store, approvals, clock


def _seed_ticket(
    store: SupervisorStore,
    *,
    ticket_id: str = "tk-1",
    status: TicketStatus = TicketStatus.RUNNING,
    created_at: int = 1,
    kind: ThreadKind = ThreadKind.ISSUE,
    external_ref: str = "#1",
    subscription: bool = True,
    assignment: bool = True,
) -> Ticket:
    suffix = ticket_id.removeprefix("tk-")
    mandate_id = f"md-{suffix}"
    store.insert_mandate(
        Mandate(
            id=mandate_id,
            origin_kind="github",
            scope="github:o/r:issue:1",
            granted_at=1,
            repository="o/r",
            actions=["comment_issue"],
        )
    )
    ticket = Ticket(
        id=ticket_id,
        title="Fix parser",
        task="SECRET-TASK",
        created_at=created_at,
        status=status,
        mandate_id=mandate_id,
    )
    store.insert_ticket(ticket)
    if assignment:
        store.insert_assignment(Assignment(id=f"as-{suffix}", ticket_id=ticket_id, attempt=1))
    if subscription:
        key = (
            f"issue:o/r#{external_ref.lstrip('#')}"
            if kind is ThreadKind.ISSUE
            else f"pr:o/r#{external_ref.lstrip('#')}"
        )
        store.upsert_thread(
            Thread(
                key=key,
                repository="o/r",
                kind=kind,
                external_ref=external_ref,
                last_trigger_at=1,
            )
        )
        store.insert_subscription(
            Subscription(
                id=f"sb-{suffix}",
                ticket_id=ticket_id,
                thread_key=key,
                mode=SubscriptionMode.OWN,
                created_at=1,
            )
        )
    return ticket


def test_draft_due_submits_one_pending_comment_approval(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store)
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        repository="o/r",
        clock=clock,
    )

    assert drafter.draft_due() == 1

    pending = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)
    assert len(pending) == 1
    approval = pending[0]
    assert approval.action is Action.COMMENT_ISSUE
    assert approval.reason == "notification:accepted"
    assert approval.assignment_id == "as-1"
    draft = drafter.build("tk-1")
    assert draft is not None
    assert draft.digest == approval.resource_digest
    assert draft.payload == {
        "ticket_id": "tk-1",
        "assignment_id": "as-1",
        "stable_id": "github:o/r:issue:1",
        "repository": "o/r",
        "url": "https://github.com/o/r/issues/1",
        "body": "[Accepted] Fix parser\nhttps://github.com/o/r/issues/1",
    }
    assert "SECRET-TASK" not in draft.payload["body"]


def test_draft_due_is_idempotent_across_steps(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store)
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(),
        repository="o/r",
        clock=clock,
    )

    assert drafter.draft_due() == 1
    assert drafter.draft_due() == 0
    assert len(approvals.for_ticket("tk-1")) == 1


def test_denied_notification_silences_the_ticket(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store)
    drafter = NotificationDrafter(
        store, approvals, NotificationConfig(), repository="o/r", clock=clock
    )
    assert drafter.draft_due() == 1
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    approvals.decide(approval.id, False, "alice")

    assert drafter.draft_due() == 0
    assert approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING) == []


def test_draft_due_skips_without_live_subscription_or_assignment(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store, ticket_id="tk-nosub", subscription=False)
    _seed_ticket(store, ticket_id="tk-noas", assignment=False)
    drafter = NotificationDrafter(
        store, approvals, NotificationConfig(), repository="o/r", clock=clock
    )

    assert drafter.draft_due() == 0


def test_draft_due_respects_since_ms(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store, created_at=1)
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(since_ms=100),
        repository="o/r",
        clock=clock,
    )

    assert drafter.draft_due() == 0


def test_draft_due_requires_github_mandate_with_comment_action(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store)
    store._conn.execute("UPDATE mandates SET actions = '[]' WHERE id = 'md-1'")
    drafter = NotificationDrafter(
        store, approvals, NotificationConfig(), repository="o/r", clock=clock
    )

    assert drafter.draft_due() == 0
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_notifications.py \
  > /tmp/p6-m5-t4-red.txt 2>&1; tail -20 /tmp/p6-m5-t4-red.txt
```

Expected：`ImportError: cannot import name 'NotificationDrafter'`。

- [ ] **Step 3: 实现 comment_url、存储查询、草拟器与 effect**

`stableagent/supervisor/views.py` 在 `thread_url` 之后追加：

```python
def comment_url(thread: Thread) -> str | None:
    """GitHub 评论要落在 Issue/PR 上；Review 回落到其 PR，仓库级 Thread 不草拟。"""
    base = f"https://github.com/{thread.repository}"
    if thread.kind is ThreadKind.ISSUE:
        return f"{base}/issues/{thread.external_ref.lstrip('#')}"
    if thread.kind is ThreadKind.PULL_REQUEST:
        return f"{base}/pull/{thread.external_ref.lstrip('#')}"
    if thread.kind is ThreadKind.REVIEW:
        tail = thread.key.split("#", 1)[1] if "#" in thread.key else ""
        number = tail.split(":", 1)[0]
        return f"{base}/pull/{number}" if number.isdigit() else None
    return None
```

`stableagent/supervisor/store.py` 的 `orchestration.model` import 增加 `Ticket`，并在
`count_due_effects` 之后追加：

```python
    def list_tickets_for_notifications(
        self, *, since_ms: int, limit: int = 200
    ) -> list[Ticket]:
        rows = self._conn.execute(
            "SELECT id FROM tickets WHERE status IN"
            " ('queued','running','waiting','ready','failed') AND created_at >= ?"
            " ORDER BY created_at ASC, id ASC LIMIT ?",
            (since_ms, limit),
        ).fetchall()
        tickets = []
        for row in rows:
            ticket = self.get_ticket(row["id"])
            if ticket is not None:
                tickets.append(ticket)
        return tickets
```

`stableagent/supervisor/notifications.py` 顶部 import 扩写为：

```python
from dataclasses import dataclass
from typing import Any

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus, resource_digest
from stableagent.integrations.github_effects import github_resource
from stableagent.orchestration.model import (
    Clock,
    Effect,
    EffectKind,
    Report,
    ReportKind,
    Ticket,
    TicketStatus,
)
from stableagent.supervisor.model import SubscriptionMode
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import comment_url
```

> 方向锁说明：`notifications.py` 只 import `integrations.github_effects` 的**纯函数**
> `github_resource`（不 import client / handler），用于与 handler 算出同一个 digest；
> 这与既有 `control.approval` 依赖同一资源形状一致，不新增网络或执行依赖。

在文件末尾追加：

```python
@dataclass(frozen=True)
class NotificationDraft:
    ticket_id: str
    assignment_id: str
    kind: NotificationKind
    payload: dict[str, Any]
    digest: str


def notification_digest(payload: dict[str, Any]) -> str:
    return resource_digest(
        Action.COMMENT_ISSUE, github_resource(Action.COMMENT_ISSUE, payload)
    )


def notification_effect(draft: NotificationDraft, *, effect_id: str, now: int) -> Effect:
    return Effect(
        id=effect_id,
        kind=EffectKind.GITHUB_COMMENT,
        idempotency_key=f"notify:{draft.ticket_id}:{draft.kind.value}",
        payload=dict(draft.payload),
        due_at=now,
    )


class NotificationDrafter:
    def __init__(
        self,
        store: SupervisorStore,
        approvals: ApprovalService,
        config: NotificationConfig,
        *,
        repository: str,
        clock: Clock,
    ) -> None:
        self._store = store
        self._approvals = approvals
        self._config = config
        self._repository = repository
        self._clock = clock

    def build(self, ticket_id: str) -> NotificationDraft | None:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            return None
        kind = STATUS_KINDS.get(ticket.status)
        if kind is None:
            return None
        mandate = self._store.get_mandate(ticket.mandate_id)
        if (
            mandate is None
            or mandate.origin_kind != "github"
            or Action.COMMENT_ISSUE.value not in mandate.actions
        ):
            return None
        assignments = self._store.list_assignments(ticket_id)
        if not assignments:
            return None
        assignment = max(assignments, key=lambda item: item.attempt)
        url = self._ticket_comment_url(ticket_id)
        if url is None:
            return None
        body = render_notification(
            ticket,
            kind,
            self._store.list_reports(ticket_id),
            max_chars=self._config.max_chars,
            url=url,
        )
        payload = {
            "ticket_id": ticket.id,
            "assignment_id": assignment.id,
            "stable_id": mandate.scope,
            "repository": self._repository,
            "url": url,
            "body": body,
        }
        return NotificationDraft(
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=kind,
            payload=payload,
            digest=notification_digest(payload),
        )

    def draft_due(self) -> int:
        if not self._config.enabled:
            return 0
        drafted = 0
        for ticket in self._store.list_tickets_for_notifications(
            since_ms=self._config.since_ms
        ):
            if self._silenced(ticket.id):
                continue
            draft = self.build(ticket.id)
            if draft is None or self._already_drafted(ticket.id, draft.digest):
                continue
            self._approvals.submit(
                ticket.id,
                Action.COMMENT_ISSUE,
                draft.digest,
                f"notification:{draft.kind.value}",
                assignment_id=draft.assignment_id,
            )
            drafted += 1
        return drafted

    def _ticket_comment_url(self, ticket_id: str) -> str | None:
        subscriptions = sorted(
            self._store.list_subscriptions(ticket_id=ticket_id, live_only=True),
            key=lambda item: (item.mode is not SubscriptionMode.OWN, item.created_at, item.id),
        )
        for subscription in subscriptions:
            thread = self._store.get_thread(subscription.thread_key)
            if thread is None:
                continue
            url = comment_url(thread)
            if url is not None:
                return url
        return None

    def _silenced(self, ticket_id: str) -> bool:
        return any(
            approval.action is Action.COMMENT_ISSUE
            and approval.status is ApprovalStatus.DENIED
            for approval in self._approvals.for_ticket(ticket_id)
        )

    def _already_drafted(self, ticket_id: str, digest: str) -> bool:
        return any(
            approval.action is Action.COMMENT_ISSUE
            and approval.resource_digest == digest
            for approval in self._approvals.for_ticket(ticket_id)
        )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_notifications.py \
  > /tmp/p6-m5-t4-green.txt 2>&1; tail -5 /tmp/p6-m5-t4-green.txt
.venv/bin/ruff check stableagent/supervisor/notifications.py \
  stableagent/supervisor/store.py stableagent/supervisor/views.py \
  tests/unit/test_supervisor_notifications.py
.venv/bin/ruff format --check stableagent/supervisor/notifications.py \
  stableagent/supervisor/store.py stableagent/supervisor/views.py \
  tests/unit/test_supervisor_notifications.py
```

Expected：`11 passed`。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/notifications.py stableagent/supervisor/store.py \
  stableagent/supervisor/views.py tests/unit/test_supervisor_notifications.py
git commit -m "feat: draft lifecycle notifications behind approvals"
```

---

### Task 5：批准与 effect 同事务、失败 effect 重试、stop 撤销（M5-5）

**Files:**
- Modify: `stableagent/control/approval.py`
- Modify: `stableagent/supervisor/service.py`
- Test: `tests/unit/test_approval_service.py`
- Test: `tests/unit/test_supervisor_service.py`

**Interfaces:**
- Consumes: `Store.enqueue_effect(effect)`（不开事务）、`Store.get_effect` /
  `update_effect`、`ApprovalService.for_ticket` / `decide` / `revoke_pending`。
- Produces:
  - `ApprovalService.decide(approval_id, approve, by, *, effect: Effect | None = None)`
  - `SupervisorService(..., approvals: ApprovalService | None = None)`
  - `SupervisorService.retry_effect(effect_id, *, actor) -> Effect`
  - `SupervisorService.stop` 在同一事务内撤销 pending 审批。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_approval_service.py` 顶部 import 扩写为：

```python
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Effect,
    EffectKind,
    EffectState,
    Ticket,
    TicketStatus,
)
```

文件末尾追加：

```python
def test_approve_enqueues_effect_in_the_same_transaction(store, clock) -> None:
    approvals = ApprovalService(store, clock=clock, ids=SequenceIds())
    approval = approvals.submit(TICKET, Action.COMMENT_ISSUE, "d-1", "notification:accepted")
    effect = Effect(
        id="ef-1",
        kind=EffectKind.GITHUB_COMMENT,
        idempotency_key="notify:tk-0001:accepted",
        payload={"ticket_id": TICKET},
        due_at=clock.now_ms(),
    )

    decided = approvals.decide(approval.id, True, "alice", effect=effect)

    assert decided.status is ApprovalStatus.APPROVED
    stored = store.get_effect("ef-1")
    assert stored is not None and stored.state is EffectState.PENDING


def test_approve_rolls_back_when_effect_enqueue_fails(store, clock, monkeypatch) -> None:
    approvals = ApprovalService(store, clock=clock, ids=SequenceIds())
    approval = approvals.submit(TICKET, Action.COMMENT_ISSUE, "d-1", "notification:accepted")
    effect = Effect(
        id="ef-1",
        kind=EffectKind.GITHUB_COMMENT,
        idempotency_key="notify:tk-0001:accepted",
        due_at=clock.now_ms(),
    )

    def boom(_effect):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "enqueue_effect", boom)
    with pytest.raises(RuntimeError):
        approvals.decide(approval.id, True, "alice", effect=effect)

    assert approvals.get(approval.id).status is ApprovalStatus.PENDING


def test_deny_never_enqueues_the_supplied_effect(store, clock) -> None:
    approvals = ApprovalService(store, clock=clock, ids=SequenceIds())
    approval = approvals.submit(TICKET, Action.COMMENT_ISSUE, "d-1", "notification:accepted")
    effect = Effect(
        id="ef-1",
        kind=EffectKind.GITHUB_COMMENT,
        idempotency_key="notify:tk-0001:accepted",
        due_at=clock.now_ms(),
    )

    decided = approvals.decide(approval.id, False, "alice", effect=effect)

    assert decided.status is ApprovalStatus.DENIED
    assert store.get_effect("ef-1") is None
```

`tests/unit/test_supervisor_service.py` 顶部 import 扩写为：

```python
from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus
from stableagent.orchestration.model import (
    Effect,
    EffectKind,
    EffectState,
    Report,
    ReportKind,
    TicketStatus,
)
```

把 `_build_service` 的返回改为（同事务审批注入）：

```python
    approvals = ApprovalService(store, clock=clock, ids=ids)
    return SupervisorService(
        store,
        policy,
        router,
        clock=clock,
        ids=ids,
        audit=audit,
        approvals=approvals,
    )
```

文件末尾追加：

```python
def test_stop_revokes_pending_approvals(service, store, clock) -> None:
    ticket = _accept(service, store)
    approvals = ApprovalService(store, clock=clock, ids=SequenceIds())
    approvals.submit(
        ticket.id, Action.COMMENT_ISSUE, "d-1", "notification:accepted"
    )

    service.stop(ticket.id, actor="alice")

    assert approvals.for_ticket(ticket.id, status=ApprovalStatus.PENDING) == []


def test_retry_effect_only_accepts_failed_rows(service, store, clock) -> None:
    store.enqueue_effect(
        Effect(
            id="ef-failed",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="notify:tk:accepted",
            payload={"ticket_id": "tk-1"},
            state=EffectState.FAILED,
            attempts=3,
            due_at=1,
            last_error="boom",
        )
    )
    store.enqueue_effect(
        Effect(
            id="ef-pending",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="notify:tk:result",
            payload={"ticket_id": "tk-1"},
            state=EffectState.PENDING,
            due_at=1,
        )
    )
    clock.advance(10)

    updated = service.retry_effect("ef-failed", actor="alice")

    assert updated.state is EffectState.PENDING
    assert updated.attempts == 0
    assert updated.due_at == clock.now_ms()
    assert updated.last_error is None
    assert updated.idempotency_key == "notify:tk:accepted"
    with pytest.raises(RouterStateError):
        service.retry_effect("ef-pending", actor="alice")
    with pytest.raises(RouterNotFoundError):
        service.retry_effect("ef-missing", actor="alice")
    with pytest.raises(RouterPermissionError):
        service.retry_effect("ef-failed", actor="mallory")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_approval_service.py \
  tests/unit/test_supervisor_service.py > /tmp/p6-m5-t5-red.txt 2>&1
tail -20 /tmp/p6-m5-t5-red.txt
```

Expected：`TypeError: decide() got an unexpected keyword argument 'effect'` 与
`TypeError: SupervisorService.__init__() got an unexpected keyword argument 'approvals'`。

- [ ] **Step 3: 实现控制层写路径**

`stableagent/control/approval.py` 把 `decide` 替换为：

```python
    def decide(
        self,
        approval_id: str,
        approve: bool,
        by: str,
        *,
        effect: Effect | None = None,
    ) -> ApprovalRequest:
        """pending → approved/denied 一次性翻转；approve 时可在同一事务内入队 effect。

        `Store.enqueue_effect` 自己不开事务，所以这里只开一个 BEGIN IMMEDIATE：任一步
        失败，审批状态与 effect 一起回滚，不存在"已批准、未入队"的窗口（L3）。
        """
        current = self.get(approval_id)
        if current is None:
            raise UnknownApprovalError(approval_id)
        if current.status is not ApprovalStatus.PENDING:
            raise ApprovalDecidedError(approval_id, current.status.value)
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        now = self._clock.now_ms()
        with self._store.transaction():
            self._store.update_approval(
                approval_id, status=status.value, decided_by=by, decided_at=now
            )
            if approve and effect is not None:
                self._store.enqueue_effect(effect)
            if not approve:
                self._fail_running_assignment(current.assignment_id, now)
        return current.model_copy(update={"status": status, "decided_by": by, "decided_at": now})
```

`stableagent/supervisor/service.py`：

顶部 `orchestration.model` import 增加 `EffectState`，并在 control 侧新增：

```python
from stableagent.control.approval import ApprovalService
```

`__init__` 增加可选依赖：

```python
    def __init__(
        self,
        store: SupervisorStore,
        policy: StaticPolicy,
        router: RouterService,
        *,
        clock: Clock,
        ids: IdFactory,
        audit: AuditWriter | None = None,
        approvals: ApprovalService | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._router = router
        self._clock = clock
        self._ids = ids
        self._audit = audit
        self._approvals = approvals
```

`stop` 在 `ticket = self._require_ticket(ticket_id)` 之后、`if ticket.status is
TicketStatus.CANCELLED` 之前插入：

```python
            if self._approvals is not None:
                self._approvals.revoke_pending(ticket_id)
```

在 `reevaluate` 之前追加：

```python
    def retry_effect(self, effect_id: str, *, actor: str) -> Effect:
        """只把 FAILED effect 改回 PENDING；原幂等键与 payload 不变（at-least-once）。"""
        self._require_operator(actor)
        effect = self._store.get_effect(effect_id)
        if effect is None:
            raise RouterNotFoundError(f"effect 不存在：{effect_id}")
        if effect.state is not EffectState.FAILED:
            raise RouterStateError(f"retry_effect 只接受 FAILED：{effect.state.value}")
        now = self._clock.now_ms()
        updated = effect.model_copy(
            update={
                "state": EffectState.PENDING,
                "attempts": 0,
                "due_at": now,
                "last_error": None,
            }
        )
        with self._store.transaction():
            self._store.update_effect(updated)
        return updated
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_approval_service.py \
  tests/unit/test_supervisor_service.py > /tmp/p6-m5-t5-green.txt 2>&1
tail -5 /tmp/p6-m5-t5-green.txt
.venv/bin/ruff check stableagent/control/approval.py \
  stableagent/supervisor/service.py tests/unit/test_approval_service.py \
  tests/unit/test_supervisor_service.py
.venv/bin/ruff format --check stableagent/control/approval.py \
  stableagent/supervisor/service.py tests/unit/test_approval_service.py \
  tests/unit/test_supervisor_service.py
```

Expected：两文件全部通过，新增 5 例（审批 3、service 2），0 failed。

- [ ] **Step 5: 提交**

```bash
git add stableagent/control/approval.py stableagent/supervisor/service.py \
  tests/unit/test_approval_service.py tests/unit/test_supervisor_service.py
git commit -m "feat: approve notifications atomically and retry failed effects"
```

---

### Task 6：Runtime 草拟接线与暂停恢复补路由（M5-6）

**Files:**
- Modify: `stableagent/supervisor/runtime.py`
- Test: `tests/unit/test_supervisor_runtime.py`
- Create: `tests/integration/test_superagent_notifications.py`

**Interfaces:**
- Consumes: Task 4 的 `NotificationConfig` / `NotificationDrafter`；Task 5 的
  `ApprovalService`；既有 `Runtime.start/step/resume`。
- Produces:
  - `read_runtime_status(store: SupervisorStore, clock: Clock) -> RuntimeStatus`
  - `RuntimeConfig.notifications: NotificationConfig | None = None`
  - `RuntimeStep.drafted: int = 0`
  - `Runtime(..., drafter: NotificationDrafter | None = None)`
  - `build_runtime` 在配置存在时装配 approvals + drafter。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_runtime.py` 顶部 import 扩写为：

```python
from stableagent.supervisor.notifications import NotificationConfig
```

`env` fixture 中的 `StaticPolicyConfig` 与 `RouterConfig` 改为：

```python
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.COMMENT_ISSUE}),
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
```

```python
        config=RuntimeConfig(
            router=RouterConfig(
                repository="o/r",
                operators=frozenset({"alice"}),
                actions=("read_repository", "comment_issue"),
            ),
            owner="test-runtime",
            lock_ttl_ms=1_000,
            stop_grace_ms=10,
            step_interval_ms=0,
            notifications=NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        ),
```

文件末尾追加：

```python
async def test_step_drafts_one_notification_approval(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()

    step = await bundle.runtime.step()

    assert step.drafted == 1
    pending = store.list_pending_approvals()
    assert [(row["ticket_id"], row["reason"]) for row in pending] == [
        (store.list_tickets()[0].id, "notification:accepted")
    ]


async def test_paused_step_does_not_draft(env) -> None:
    bundle, store, clock, source, _ = env
    store.set_runtime_paused(True, actor="alice", now=clock.now_ms(), reason="maint")
    bundle.runtime.start()
    source.feed(_event())

    step = await bundle.runtime.step()

    assert step.paused is True and step.drafted == 0
    assert store.list_pending_approvals() == []


async def test_external_resume_routes_pending_then_drafts(env) -> None:
    bundle, store, clock, source, _ = env
    store.set_runtime_paused(True, actor="alice", now=clock.now_ms(), reason="maint")
    bundle.runtime.start()
    source.feed(_event())
    assert (await bundle.runtime.step()).drafted == 0
    store.set_runtime_paused(False, actor="alice", now=clock.now_ms())

    step = await bundle.runtime.step()

    assert (step.paused, step.routed, step.drafted) == (False, 1, 1)
    assert len(store.list_tickets()) == 1


async def test_drafted_counts_only_new_approvals(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()
    assert (await bundle.runtime.step()).drafted == 1

    assert (await bundle.runtime.step()).drafted == 0
    assert len(store.list_pending_approvals()) == 1
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py \
  > /tmp/p6-m5-t6-red.txt 2>&1; tail -20 /tmp/p6-m5-t6-red.txt
```

Expected：`ValidationError: Extra inputs are not permitted`（`notifications` 尚未进
`RuntimeConfig`）或 `AttributeError: ... drafted`。

- [ ] **Step 3: 接线 Runtime 与组成根**

`stableagent/supervisor/runtime.py` 顶部 import 增加：

```python
from stableagent.control.approval import ApprovalService
from stableagent.supervisor.notifications import NotificationConfig, NotificationDrafter
```

`RuntimeConfig` 增加尾字段：

```python
    notifications: NotificationConfig | None = None
```

`RuntimeStep` 增加尾字段（必须在 `source_error` 之后，保持默认值顺序）：

```python
    source_error: str | None = None
    drafted: int = 0
```

`Runtime.__init__` 增加参数与状态：

```python
    def __init__(
        self,
        *,
        store: SupervisorStore,
        source: Source,
        router: RouterService,
        orchestration: OrchestrationService,
        scheduler: Scheduler,
        effects: EffectDispatcher,
        config: RuntimeConfig,
        clock: Clock,
        drafter: NotificationDrafter | None = None,
    ) -> None:
        self._store = store
        self._source = source
        self._router = router
        self._orchestration = orchestration
        self._scheduler = scheduler
        self._effects = effects
        self._config = config
        self._clock = clock
        self._drafter = drafter
        self._started = False
        self._stop_requested = False
        self._was_paused = False
```

`start()` 在读取 `control` 后记录暂停位：

```python
        control = self._store.get_runtime_control()
        routed = 0 if control.paused else self._route_pending()
        self._was_paused = control.paused
        self._started = True
```

`step()` 的暂停分支与路由段替换为：

```python
        control = self._store.get_runtime_control()
        if control.paused:
            self._was_paused = True
            return RuntimeStep(
                paused=True,
                received=len(batch.events),
                routed=0,
                duplicates=batch.duplicates,
                enqueued=0,
                dispatched=0,
                external=0,
                running=len(self._scheduler.running_assignments()),
                source_error=source_error,
                drafted=0,
            )
        self._renew_lock()
        routed = 0
        if self._was_paused:
            routed += self._route_pending()
            self._was_paused = False
        duplicates = 0
        for event in batch.events:
            reception = self._router.receive(event.trigger)
            if reception.duplicate:
                duplicates += 1
            else:
                routed += 1
        tick = await self._scheduler.tick()
        drafted = 0
        if self._drafter is not None:
            drafted = self._drafter.draft_due()
        external = await self._effects.dispatch_due()
        return RuntimeStep(
            paused=False,
            received=len(batch.events),
            routed=routed,
            duplicates=duplicates + batch.duplicates,
            enqueued=tick.enqueued,
            dispatched=tick.dispatched,
            external=external,
            running=tick.running,
            source_error=source_error,
            drafted=drafted,
        )
```

`status()` 替换为委托：

```python
    def status(self) -> RuntimeStatus:
        return read_runtime_status(self._store, self._clock)
```

`resume()` 在返回前清掉暂停位：

```python
    def resume(self, *, actor: str, reason: str | None = None) -> RuntimeControl:
        control = self._store.set_runtime_paused(
            False, actor=actor, now=self._clock.now_ms(), reason=reason
        )
        if self._started:
            self._renew_lock()
            self._route_pending()
        self._was_paused = False
        return control
```

在 `Runtime` 类之前新增自由函数：

```python
def read_runtime_status(store: SupervisorStore, clock: Clock) -> RuntimeStatus:
    now = clock.now_ms()
    lock = store.get_runtime_lock()
    control = store.get_runtime_control()
    started = lock is not None and lock.expires_at > now
    return RuntimeStatus(
        started=started,
        paused=control.paused,
        owner=lock.owner if started and lock is not None else None,
        paused_at=control.changed_at if control.paused else None,
        paused_by=control.actor if control.paused else None,
        reason=control.reason if control.paused else None,
        source_health=tuple(store.list_source_cursors()),
        queued_tickets=store.count_tickets(status=TicketStatus.QUEUED),
        running_assignments=store.count_assignments(status=AssignmentStatus.RUNNING),
        due_effects=store.count_due_effects(now),
        failed_effects=len(store.list_failed_effects()),
        pending_approvals=len(store.list_pending_approvals()),
    )
```

`build_runtime` 中在构造 `RouterService` 之后新增：

```python
    approvals = ApprovalService(store, clock=clock, ids=ids)
```

`SupervisorService` 构造增加 `approvals=approvals`；`Runtime` 构造前新增：

```python
    drafter = None
    if config.notifications is not None:
        drafter = NotificationDrafter(
            store,
            approvals,
            config.notifications,
            repository=config.router.repository,
            clock=clock,
        )
```

`Runtime` 构造增加 `drafter=drafter`。

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_runtime.py \
  > /tmp/p6-m5-t6-green.txt 2>&1; tail -5 /tmp/p6-m5-t6-green.txt
.venv/bin/ruff check stableagent/supervisor/runtime.py \
  tests/unit/test_supervisor_runtime.py
.venv/bin/ruff format --check stableagent/supervisor/runtime.py \
  tests/unit/test_supervisor_runtime.py
```

Expected：`20 passed`（M4 的 16 例 + 新增 4 例）。

- [ ] **Step 5: 写并跑集成证据**

新建 `tests/integration/test_superagent_notifications.py`：

```python
"""WP-6.5 第 6 条：通知只在批准后写一次，拒绝静默，stop 撤销旧草稿。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.control.approval import ApprovalService
from stableagent.control.errors import ApprovalDecidedError
from stableagent.control.model import Action, ApprovalStatus
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
)
from stableagent.orchestration.model import (
    Assignment,
    EffectKind,
    Mandate,
    Ticket,
    TicketStatus,
)
from stableagent.supervisor.model import (
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
)
from stableagent.supervisor.notifications import (
    NotificationConfig,
    NotificationDrafter,
    notification_effect,
)
from stableagent.supervisor.router import RouterConfig
from stableagent.supervisor.runtime import RuntimeConfig, build_runtime
from stableagent.supervisor.source import GitHubSource
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _issue_item(number: int = 1) -> GitHubReadItem:
    return GitHubReadItem(
        kind=GitHubReadKind.ISSUE,
        occurred_at=1,
        resource={
            "number": number,
            "user": {"login": "alice"},
            "body": "@agent please add rate limiter",
        },
    )


class RecordingExecutor:
    def __init__(self) -> None:
        self.launched: list[str] = []

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)

    async def deliver(self, assignment, ticket, inputs) -> None:
        return None

    async def cancel(self, assignment) -> None:
        return None

    async def recover(self, assignment, ticket) -> None:
        return None


def _build(
    *,
    store: SupervisorStore,
    client: FakeGitHubReadClient,
    handlers,
    clock: FixedClock,
    ids: SequenceIds,
    owner: str,
):
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.COMMENT_ISSUE}),
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    source = GitHubSource(store, client, repository="o/r", bot_login="agent", clock=clock)
    return build_runtime(
        store=store,
        policy=policy,
        source=source,
        executor=RecordingExecutor(),
        effect_handlers=handlers,
        config=RuntimeConfig(
            router=RouterConfig(
                repository="o/r",
                operators=frozenset({"alice"}),
                actions=("read_repository", "comment_issue"),
            ),
            owner=owner,
            lock_ttl_ms=1_000,
            stop_grace_ms=10,
            step_interval_ms=0,
            notifications=NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        ),
        clock=clock,
        ids=ids,
    )


def _seed_ticket(store: SupervisorStore, *, ticket_id: str, status: TicketStatus) -> Ticket:
    suffix = ticket_id.removeprefix("tk-")
    mandate_id = f"md-{suffix}"
    store.insert_mandate(
        Mandate(
            id=mandate_id,
            origin_kind="github",
            scope="github:o/r:issue:1",
            granted_at=1,
            repository="o/r",
            actions=["comment_issue"],
        )
    )
    ticket = Ticket(
        id=ticket_id,
        title="Fix parser",
        task="SECRET-TASK",
        created_at=1,
        status=status,
        mandate_id=mandate_id,
    )
    store.insert_ticket(ticket)
    store.insert_assignment(Assignment(id=f"as-{suffix}", ticket_id=ticket_id, attempt=1))
    store.upsert_thread(
        Thread(
            key="issue:o/r#1",
            repository="o/r",
            kind=ThreadKind.ISSUE,
            external_ref="#1",
            last_trigger_at=1,
        )
    )
    store.insert_subscription(
        Subscription(
            id=f"sb-{suffix}",
            ticket_id=ticket_id,
            thread_key="issue:o/r#1",
            mode=SubscriptionMode.OWN,
            created_at=1,
        )
    )
    return ticket


@pytest.fixture
def env(tmp_path: Path):
    clock, ids = FixedClock(), SequenceIds()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    try:
        yield store, clock, ids
    finally:
        store.close()


async def test_draft_approve_dispatch_once_across_restart(env) -> None:
    store, clock, ids = env
    client = FakeGitHubReadClient([_issue_item()])
    calls: list[str] = []

    async def comment_handler(effect) -> None:
        calls.append(effect.id)

    handlers = {EffectKind.GITHUB_COMMENT: comment_handler}
    first = _build(
        store=store, client=client, handlers=handlers, clock=clock, ids=ids, owner="runtime-a"
    )
    first.runtime.start()
    assert (await first.runtime.step()).drafted == 1

    ticket = store.list_tickets()[0]
    approvals = ApprovalService(store, clock=clock, ids=ids)
    pending = approvals.for_ticket(ticket.id, status=ApprovalStatus.PENDING)
    assert [row.reason for row in pending] == ["notification:accepted"]
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        repository="o/r",
        clock=clock,
    )
    draft = drafter.build(ticket.id)
    assert draft is not None
    effect = notification_effect(draft, effect_id="ef-comment-1", now=clock.now_ms())
    assert effect.idempotency_key == f"notify:{ticket.id}:accepted"

    approvals.decide(pending[0].id, True, "alice", effect=effect)

    assert (await first.runtime.step()).external == 1
    assert calls == ["ef-comment-1"]

    await first.runtime.stop()
    restarted = _build(
        store=store, client=client, handlers=handlers, clock=clock, ids=ids, owner="runtime-b"
    )
    restarted.runtime.start()
    await restarted.runtime.step()

    assert calls == ["ef-comment-1"]


async def test_deny_silences_and_stop_revokes_old_draft(env) -> None:
    store, clock, ids = env
    _seed_ticket(store, ticket_id="tk-1", status=TicketStatus.RUNNING)
    _seed_ticket(store, ticket_id="tk-2", status=TicketStatus.RUNNING)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    bundle = _build(
        store=store,
        client=FakeGitHubReadClient([]),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    bundle.runtime.start()

    assert (await bundle.runtime.step()).drafted == 2

    denied = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    approvals.decide(denied.id, False, "alice")
    assert (await bundle.runtime.step()).drafted == 0
    assert approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING) == []

    revoked = approvals.for_ticket("tk-2", status=ApprovalStatus.PENDING)[0]
    bundle.service.stop("tk-2", actor="alice")
    assert approvals.for_ticket("tk-2", status=ApprovalStatus.PENDING) == []
    with pytest.raises(ApprovalDecidedError):
        approvals.decide(revoked.id, True, "alice")

    await bundle.runtime.stop()


def test_each_status_drafts_at_most_once(env) -> None:
    store, clock, ids = env
    for ticket_id, status in (
        ("tk-1", TicketStatus.WAITING),
        ("tk-2", TicketStatus.READY),
        ("tk-3", TicketStatus.FAILED),
    ):
        _seed_ticket(store, ticket_id=ticket_id, status=status)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        repository="o/r",
        clock=clock,
    )

    assert drafter.draft_due() == 3
    assert drafter.draft_due() == 0
    reasons = {
        approval.reason
        for ticket_id in ("tk-1", "tk-2", "tk-3")
        for approval in approvals.for_ticket(ticket_id)
    }
    assert reasons == {
        "notification:attention",
        "notification:result",
        "notification:failure",
    }
```

```bash
.venv/bin/pytest -q --no-cov tests/integration/test_superagent_notifications.py \
  > /tmp/p6-m5-t6-integration.txt 2>&1; tail -5 /tmp/p6-m5-t6-integration.txt
.venv/bin/ruff check tests/integration/test_superagent_notifications.py
.venv/bin/ruff format --check tests/integration/test_superagent_notifications.py
```

Expected：`3 passed`。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/runtime.py tests/unit/test_supervisor_runtime.py \
  tests/integration/test_superagent_notifications.py
git commit -m "feat: draft notifications between tick and external dispatch"
```

---

### Task 7：Console 命令面与 stdlib CLI（M5-7）

**Files:**
- Create: `stableagent/supervisor/console.py`
- Create: `stableagent/supervisor/cli.py`
- Modify: `stableagent/supervisor/router.py`
- Modify: `stableagent/supervisor/service.py`
- Modify: `stableagent/supervisor/runtime.py`
- Test: `tests/unit/test_supervisor_console.py`

**Interfaces:**
- Consumes: Task 1~6 的视图、草拟器、`ApprovalService.decide(effect=...)`、
  `SupervisorService.retry_effect/stop/reevaluate`、`OrchestrationService.retry_ticket`、
  `read_runtime_status`。
- Produces:
  - `require_operator(operators: frozenset[str], actor: str) -> None`
  - `CliError(code: int, message: str)`
  - `ConsoleConfig(repository, operators, actors, actions, allowed_*, network_profile,
    base_ref, requires_approval, default_validity_ms, policy_version, notifications)`
  - `StatusView(sections, runtime)`
  - `Console(store, *, clock, ids, config, audit=None)`，方法
    `status/ticket/thread/approve/deny/stop/retry/reevaluate/pause/resume`
  - `render_status/render_ticket/render_thread`
  - `main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_supervisor_console.py`：

```python
"""WP-6.5：CLI 只做 operator 门禁、--confirm、退出码与渲染；业务写复用既有服务。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus
from stableagent.orchestration.model import (
    Assignment,
    Effect,
    EffectKind,
    EffectState,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketStatus,
)
from stableagent.supervisor.cli import main
from stableagent.supervisor.console import (
    CliError,
    Console,
    ConsoleConfig,
    render_thread,
)
from stableagent.supervisor.model import (
    SourceCursor,
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.notifications import NotificationConfig, NotificationDrafter
from stableagent.supervisor.router import RouterPermissionError
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _config(**overrides):
    data = {
        "repository": "o/r",
        "operators": frozenset({"alice"}),
        "actors": frozenset({"alice"}),
        "actions": ("read_repository", "comment_issue"),
        "default_validity_ms": 60_000,
        "notifications": NotificationConfig(enabled=True, max_chars=400),
    }
    data.update(overrides)
    return ConsoleConfig(**data)


def _seed_ticket(
    store: SupervisorStore,
    *,
    ticket_id: str = "tk-1",
    status: TicketStatus = TicketStatus.RUNNING,
) -> Ticket:
    suffix = ticket_id.removeprefix("tk-")
    mandate_id = f"md-{suffix}"
    store.insert_mandate(
        Mandate(
            id=mandate_id,
            origin_kind="github",
            scope="github:o/r:issue:1",
            granted_at=1,
            repository="o/r",
            actions=["comment_issue"],
        )
    )
    ticket = Ticket(
        id=ticket_id,
        title="Fix parser",
        task="SECRET-TASK",
        created_at=1,
        status=status,
        mandate_id=mandate_id,
    )
    store.insert_ticket(ticket)
    store.insert_assignment(Assignment(id=f"as-{suffix}", ticket_id=ticket_id, attempt=1))
    store.upsert_thread(
        Thread(
            key="issue:o/r#1",
            repository="o/r",
            kind=ThreadKind.ISSUE,
            external_ref="#1",
            last_trigger_at=1,
        )
    )
    store.insert_subscription(
        Subscription(
            id=f"sb-{suffix}",
            ticket_id=ticket_id,
            thread_key="issue:o/r#1",
            mode=SubscriptionMode.OWN,
            created_at=1,
        )
    )
    return ticket


@pytest.fixture
def env(tmp_path: Path):
    clock = FixedClock()
    ids = SequenceIds()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    return store, approvals, clock, ids


def _console(store, clock, ids, **overrides) -> Console:
    return Console(store, clock=clock, ids=ids, config=_config(**overrides))


def _draft(store, approvals, clock) -> None:
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(enabled=True, max_chars=400),
        repository="o/r",
        clock=clock,
    )
    assert drafter.draft_due() == 1


def test_non_operator_is_rejected_before_confirm(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    console = _console(store, clock, ids)

    with pytest.raises(RouterPermissionError):
        console.approve(approval.id, actor="mallory", confirm="wrong")

    assert approvals.get(approval.id).status is ApprovalStatus.PENDING


def test_confirm_mismatch_writes_nothing(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    console = _console(store, clock, ids)

    with pytest.raises(CliError) as captured:
        console.approve(approval.id, actor="alice", confirm="ap-other")

    assert captured.value.code == 2
    assert approvals.get(approval.id).status is ApprovalStatus.PENDING


def test_approve_notification_enqueues_matching_effect(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    console = _console(store, clock, ids)

    decided = console.approve(approval.id, actor="alice", confirm=approval.id)

    assert decided.status is ApprovalStatus.APPROVED
    effects = [
        effect
        for effect in store.due_effects(clock.now_ms())
        if effect.kind is EffectKind.GITHUB_COMMENT
    ]
    assert [effect.idempotency_key for effect in effects] == ["notify:tk-1:accepted"]
    assert effects[0].payload["body"].startswith("[Accepted] Fix parser")


def test_approve_rejects_a_changed_draft_without_writing(env) -> None:
    store, approvals, clock, ids = env
    ticket = _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    store.update_ticket(ticket.model_copy(update={"title": "changed"}))
    console = _console(store, clock, ids)

    with pytest.raises(CliError) as captured:
        console.approve(approval.id, actor="alice", confirm=approval.id)

    assert captured.value.code == 3
    assert approvals.get(approval.id).status is ApprovalStatus.PENDING
    assert store.due_effects(clock.now_ms()) == []


def test_generic_approval_flips_without_draft_recompute(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    approval = approvals.submit("tk-1", Action.COMMENT_ISSUE, "legacy", "high_risk")
    console = _console(store, clock, ids)

    decided = console.approve(approval.id, actor="alice", confirm=approval.id)

    assert decided.status is ApprovalStatus.APPROVED
    assert store.due_effects(clock.now_ms()) == []


def test_deny_silences_future_notifications(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    console = _console(store, clock, ids)

    console.deny(approval.id, actor="alice", confirm=approval.id)

    drafter = NotificationDrafter(
        store, approvals, NotificationConfig(), repository="o/r", clock=clock
    )
    assert drafter.draft_due() == 0


def test_stop_revokes_pending_approvals_and_cancels(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    console = _console(store, clock, ids)

    stopped = console.stop("tk-1", actor="alice", confirm="tk-1")

    assert stopped.status is TicketStatus.CANCELLED
    assert approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING) == []


def test_retry_dispatches_failed_effect_then_failed_ticket(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="notify:tk-1:accepted",
            payload={"ticket_id": "tk-1"},
            state=EffectState.FAILED,
            attempts=3,
            due_at=1,
            last_error="boom",
        )
    )
    _seed_ticket(store, ticket_id="tk-2", status=TicketStatus.FAILED)
    console = _console(store, clock, ids)

    effect = console.retry("ef-1", actor="alice", confirm="ef-1")
    assignment = console.retry("tk-2", actor="alice", confirm="tk-2")

    assert effect.state is EffectState.PENDING
    assert assignment.attempt == 2
    assert store.get_ticket("tk-2").status is TicketStatus.QUEUED
    with pytest.raises(CliError) as captured:
        console.retry("missing", actor="alice", confirm="missing")
    assert captured.value.code == 1


def test_pause_and_resume_require_literal_confirm(env) -> None:
    store, _, clock, ids = env
    console = _console(store, clock, ids)

    assert console.pause(actor="alice", confirm="pause").paused is True
    assert console.resume(actor="alice", confirm="resume").paused is False
    with pytest.raises(CliError) as captured:
        console.resume(actor="alice", confirm="pause")
    assert captured.value.code == 2


def test_cli_main_status_and_exit_codes(tmp_path: Path, capsys) -> None:
    db = tmp_path / "p6.db"
    config_path = tmp_path / "console.json"
    config_path.write_text(
        json.dumps(
            {
                "repository": "o/r",
                "operators": ["alice"],
                "actors": ["alice"],
                "actions": ["read_repository", "comment_issue"],
                "default_validity_ms": 60000,
                "notifications": {"enabled": True, "max_chars": 400, "since_ms": 0},
            }
        ),
        encoding="utf-8",
    )
    store = SupervisorStore(db, clock=FixedClock())
    _seed_ticket(store, status=TicketStatus.READY)
    store.close()
    base = ["--db", str(db), "--config", str(config_path), "--operator", "alice"]

    assert main([*base, "status"]) == 0
    output = capsys.readouterr().out
    assert "attention" in output and "ready_ticket" in output
    assert main([*base, "stop", "tk-1"]) == 2
    assert main([*base[:5], "mallory", "stop", "tk-1", "--confirm", "tk-1"]) == 4
    assert main([*base, "ticket", "tk-missing"]) == 1


def test_cli_main_covers_ticket_thread_retry_and_controls(tmp_path: Path, capsys) -> None:
    db = tmp_path / "p6.db"
    config_path = tmp_path / "console.json"
    config_path.write_text(
        json.dumps(
            {
                "repository": "o/r",
                "operators": ["alice"],
                "actors": ["alice"],
                "actions": ["read_repository", "comment_issue"],
                "default_validity_ms": 60000,
                "notifications": {"enabled": True, "max_chars": 400, "since_ms": 0},
            }
        ),
        encoding="utf-8",
    )
    store = SupervisorStore(db, clock=FixedClock())
    _seed_ticket(store, status=TicketStatus.READY)
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id="tk-1",
            assignment_id="as-1",
            kind=ReportKind.READY,
            summary="ready for review",
            created_at=2,
        )
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"ticket_id": "tk-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )
    store.insert_trigger(
        Trigger(
            id="tg-1",
            external_id="issue:1",
            kind=TriggerKind.ISSUE,
            actor="alice",
            thread_keys=["issue:o/r#1"],
            occurred_at=1,
            received_at=1,
            payload={"request": {"text": "task", "mentioned": True}},
        )
    )
    store.insert_approval(
        id="ap-1",
        ticket_id="tk-1",
        action="comment_issue",
        resource_digest="legacy",
        reason="high_risk",
        requested_at=1,
    )
    store.insert_approval(
        id="ap-2",
        ticket_id="tk-1",
        action="comment_issue",
        resource_digest="legacy-2",
        reason="high_risk",
        requested_at=1,
    )
    store.put_source_cursor(SourceCursor(source="github", cursor="9"))
    store.close()
    base = ["--db", str(db), "--config", str(config_path), "--operator", "alice"]

    assert main([*base, "ticket", "tk-1"]) == 0
    ticket_output = capsys.readouterr().out
    assert "assignment as-1" in ticket_output
    assert "timeline 2 report rp-1" in ticket_output
    assert main([*base, "thread", "issue:o/r#1"]) == 0
    assert "trigger" in capsys.readouterr().out
    assert main([*base, "approve", "ap-1", "--confirm", "ap-1"]) == 0
    assert main([*base, "deny", "ap-2", "--confirm", "ap-2"]) == 0
    assert main([*base, "approve", "ap-missing", "--confirm", "ap-missing"]) == 1
    assert render_thread([]) == "thread empty"
    assert main([*base, "retry", "ef-1", "--confirm", "ef-1"]) == 0
    assert main([*base, "reevaluate", "tg-1", "--confirm", "tg-1"]) == 0
    assert main([*base, "pause", "--confirm", "pause"]) == 0
    assert main([*base, "resume", "--confirm", "resume"]) == 0

    missing = tmp_path / "missing.json"
    assert main(["--db", str(db), "--config", str(missing), "--operator", "alice", "status"]) == 1
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"repository": "o/r", "bogus": 1}), encoding="utf-8")
    assert main(["--db", str(db), "--config", str(bad), "--operator", "alice", "status"]) == 1
    bad_action = tmp_path / "bad-action.json"
    bad_action.write_text(
        json.dumps({"repository": "o/r", "operators": ["alice"], "actions": ["merge"]}),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--db",
                str(db),
                "--config",
                str(bad_action),
                "--operator",
                "alice",
                "status",
            ]
        )
        == 1
    )
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_console.py \
  > /tmp/p6-m5-t7-red.txt 2>&1; tail -20 /tmp/p6-m5-t7-red.txt
```

Expected：`ModuleNotFoundError: No module named 'stableagent.supervisor.cli'`。

- [ ] **Step 3: 共享 operator 门禁并实现 console.py**

`stableagent/supervisor/router.py` 在 `RouterConfig` 之后追加：

```python
def require_operator(operators: frozenset[str], actor: str) -> None:
    allowed = {name.casefold() for name in operators}
    if actor.casefold() not in allowed:
        raise RouterPermissionError(f"actor 不是 operator：{actor}")
```

`stableagent/supervisor/service.py` 的 `_require_operator` 替换为：

```python
    def _require_operator(self, actor: str) -> None:
        require_operator(self._router.config.operators, actor)
```

并在既有 router import 中补 `require_operator`。

`stableagent/supervisor/runtime.py` 的 `RuntimeController._require_operator` 替换为：

```python
    def _require_operator(self, actor: str) -> None:
        require_operator(self._runtime.config.router.operators, actor)
```

并在既有 router import 中补 `require_operator`。

新建 `stableagent/supervisor/console.py`：

```python
"""CLI 监管控制台：门禁、--confirm 与渲染；业务规则在既有服务层（规格 WP-6.5）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stableagent.control.approval import ApprovalService
from stableagent.control.audit import AuditWriter
from stableagent.control.errors import ApprovalDecidedError, UnknownApprovalError
from stableagent.control.model import Action, ApprovalRequest, ApprovalStatus
from stableagent.control.policy import P5_POLICY_VERSION, StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import (
    Assignment,
    Clock,
    Effect,
    IdFactory,
    Ticket,
    TicketStatus,
)
from stableagent.orchestration.service import OrchestrationService
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import Decision, RuntimeControl
from stableagent.supervisor.notifications import (
    NotificationConfig,
    NotificationDrafter,
    NotificationDraft,
    notification_effect,
)
from stableagent.supervisor.router import RouterConfig, RouterService, require_operator
from stableagent.supervisor.runtime import RuntimeStatus, read_runtime_status
from stableagent.supervisor.service import SupervisorService
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import (
    StatusSections,
    SupervisorViews,
    TicketDetail,
    TimelineEvent,
)


class CliError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.detail = message


class ConsoleConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str
    operators: frozenset[str] = frozenset()
    actors: frozenset[str] = frozenset()
    actions: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    allowed_mcp: tuple[str, ...] = ()
    network_profile: str | None = None
    base_ref: str | None = None
    requires_approval: bool = False
    default_validity_ms: int = Field(default=86_400_000, ge=1)
    policy_version: str = P5_POLICY_VERSION
    notifications: NotificationConfig | None = None


class StatusView(BaseModel):
    model_config = ConfigDict(frozen=True)

    sections: StatusSections
    runtime: RuntimeStatus


class Console:
    def __init__(
        self,
        store: SupervisorStore,
        *,
        clock: Clock,
        ids: IdFactory,
        config: ConsoleConfig,
        audit: AuditWriter | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._config = config
        try:
            grantable = frozenset(Action(item) for item in config.actions)
        except ValueError as error:
            raise CliError(1, f"未知 action：{error}") from error
        policy = StaticPolicy(
            StaticPolicyConfig(
                repository=config.repository,
                actors=config.actors,
                grantable_actions=grantable,
                allowed_commands=list(config.allowed_commands),
                allowed_mcp=list(config.allowed_mcp),
                default_validity_ms=config.default_validity_ms,
                policy_version=config.policy_version,
            ),
            clock=clock,
            ids=ids,
        )
        router = RouterService(
            store,
            policy,
            ObserveOnlyAssessor(),
            RouterConfig(
                repository=config.repository,
                operators=config.operators,
                actions=config.actions,
                allowed_paths=config.allowed_paths,
                allowed_commands=config.allowed_commands,
                allowed_mcp=config.allowed_mcp,
                network_profile=config.network_profile,
                base_ref=config.base_ref,
                requires_approval=config.requires_approval,
            ),
            clock=clock,
            ids=ids,
            audit=audit,
        )
        self._approvals = ApprovalService(store, clock=clock, ids=ids)
        self._service = SupervisorService(
            store,
            policy,
            router,
            clock=clock,
            ids=ids,
            audit=audit,
            approvals=self._approvals,
        )
        self._orchestration = OrchestrationService(store, clock=clock, ids=ids)
        self._views = SupervisorViews(store, clock=clock)
        self._drafter = (
            None
            if config.notifications is None
            else NotificationDrafter(
                store,
                self._approvals,
                config.notifications,
                repository=config.repository,
                clock=clock,
            )
        )

    def status(self) -> StatusView:
        return StatusView(
            sections=self._views.status_sections(),
            runtime=read_runtime_status(self._store, self._clock),
        )

    def ticket(self, ticket_id: str) -> TicketDetail:
        return self._views.ticket_detail(ticket_id)

    def thread(self, thread_key: str) -> list[TimelineEvent]:
        return self._views.thread_timeline(thread_key)

    def approve(self, approval_id: str, *, actor: str, confirm: str) -> ApprovalRequest:
        self._require_operator(actor)
        self._confirm(confirm, approval_id)
        approval = self._existing_approval(approval_id)
        effect = None
        if approval.reason.startswith("notification:"):
            draft = self._build_notification(approval.ticket_id)
            if draft is None or draft.digest != approval.resource_digest:
                raise CliError(3, "通知草稿已变化，请重新 status 后再批准")
            effect = notification_effect(
                draft, effect_id=self._ids.new_id("ef"), now=self._clock.now_ms()
            )
        return self._approvals.decide(approval_id, True, actor, effect=effect)

    def deny(self, approval_id: str, *, actor: str, confirm: str) -> ApprovalRequest:
        self._require_operator(actor)
        self._confirm(confirm, approval_id)
        self._existing_approval(approval_id)
        return self._approvals.decide(approval_id, False, actor)

    def stop(
        self,
        ticket_id: str,
        *,
        actor: str,
        confirm: str,
        reason: str | None = None,
    ) -> Ticket:
        self._require_operator(actor)
        self._confirm(confirm, ticket_id)
        return self._service.stop(ticket_id, actor=actor, reason=reason)

    def retry(self, target_id: str, *, actor: str, confirm: str) -> Effect | Assignment:
        self._require_operator(actor)
        self._confirm(confirm, target_id)
        if self._store.get_effect(target_id) is not None:
            return self._service.retry_effect(target_id, actor=actor)
        ticket = self._store.get_ticket(target_id)
        if ticket is None:
            raise CliError(1, f"未知 effect 或 ticket：{target_id}")
        if ticket.status is not TicketStatus.FAILED:
            raise CliError(1, f"ticket 不是 FAILED：{ticket.status.value}")
        return self._orchestration.retry_ticket(target_id)

    def reevaluate(self, trigger_id: str, *, actor: str, confirm: str) -> Decision:
        self._require_operator(actor)
        self._confirm(confirm, trigger_id)
        return self._service.reevaluate(trigger_id, actor=actor)

    def pause(self, *, actor: str, confirm: str, reason: str | None = None) -> RuntimeControl:
        self._require_operator(actor)
        self._confirm(confirm, "pause")
        return self._store.set_runtime_paused(
            True, actor=actor, now=self._clock.now_ms(), reason=reason
        )

    def resume(self, *, actor: str, confirm: str, reason: str | None = None) -> RuntimeControl:
        self._require_operator(actor)
        self._confirm(confirm, "resume")
        return self._store.set_runtime_paused(
            False, actor=actor, now=self._clock.now_ms(), reason=reason
        )

    def _require_operator(self, actor: str) -> None:
        require_operator(self._config.operators, actor)

    @staticmethod
    def _confirm(actual: str, expected: str) -> None:
        if actual != expected:
            raise CliError(2, f"--confirm 必须等于 {expected}")

    def _existing_approval(self, approval_id: str) -> ApprovalRequest:
        approval = self._approvals.get(approval_id)
        if approval is None:
            raise UnknownApprovalError(approval_id)
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalDecidedError(approval_id, approval.status.value)
        return approval

    def _build_notification(self, ticket_id: str) -> NotificationDraft | None:
        if self._drafter is None:
            return None
        return self._drafter.build(ticket_id)


def render_status(view: StatusView) -> str:
    runtime = view.runtime
    lines = [
        f"runtime started={runtime.started} paused={runtime.paused} owner={runtime.owner or '-'}",
        "queue"
        f" queued_tickets={view.sections.queue.queued_tickets}"
        f" running_assignments={view.sections.queue.running_assignments}"
        f" due_effects={view.sections.queue.due_effects}"
        f" failed_effects={view.sections.queue.failed_effects}"
        f" pending_approvals={view.sections.queue.pending_approvals}",
    ]
    for cursor in view.sections.source_health:
        lines.append(
            f"source {cursor.source} health={cursor.health.value}"
            f" cursor={cursor.cursor or '-'} failures={cursor.consecutive_failures}"
        )
    if not view.sections.attention:
        lines.append("attention none")
    for item in view.sections.attention:
        lines.append(
            f"attention {item.priority} {item.source} target={item.target_id}"
            f" ticket={item.ticket_id or '-'} — {item.detail}"
            f" [{'/'.join(item.actions)}]"
        )
        if item.ticket_link:
            lines.append(f"  link {item.ticket_link}")
    return "\n".join(lines)


def render_ticket(detail: TicketDetail) -> str:
    ticket = detail.ticket
    lines = [
        f"ticket {ticket.id} status={ticket.status.value} title={ticket.title}",
        f"mandate {detail.mandate_id}",
    ]
    if detail.ticket_link:
        lines.append(f"link {detail.ticket_link}")
    for assignment in detail.assignments:
        lines.append(
            f"assignment {assignment.id} attempt={assignment.attempt}"
            f" status={assignment.status.value} failure={assignment.failure or '-'}"
        )
    for subscription in detail.subscriptions:
        lines.append(
            f"subscription {subscription.id} thread={subscription.thread_key}"
            f" mode={subscription.mode.value} ended_at={subscription.ended_at or '-'}"
        )
    for event in detail.timeline:
        lines.append(f"timeline {event.at} {event.kind} {event.object_id or '-'} {event.summary}")
    return "\n".join(lines)


def render_thread(events: list[TimelineEvent]) -> str:
    if not events:
        return "thread empty"
    return "\n".join(
        f"timeline {event.at} {event.kind} {event.object_id or '-'} {event.summary}"
        for event in events
    )


def render_approval(approval: ApprovalRequest, *, approved: bool) -> str:
    verb = "approved" if approved else "denied"
    return f"{verb} {approval.id} ticket={approval.ticket_id}"


def render_retry(result: Effect | Assignment) -> str:
    if isinstance(result, Effect):
        return f"retried effect {result.id} state={result.state.value}"
    return f"retried ticket {result.ticket_id} assignment={result.id}"


def render_decision(decision: Decision) -> str:
    return f"reevaluated {decision.id} outcome={decision.outcome.value}"


def render_control(control: RuntimeControl) -> str:
    return "paused" if control.paused else "resumed"
```

- [ ] **Step 4: 实现 cli.py**

新建 `stableagent/supervisor/cli.py`：

```python
"""stdlib CLI 入口：只解析参数、调用 Console、映射退出码（规格 WP-6.5）。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from stableagent.control.errors import ApprovalDecidedError, UnknownApprovalError
from stableagent.orchestration.errors import UnknownTicketError
from stableagent.orchestration.model import SystemClock, Uuid4Id
from stableagent.supervisor.console import (
    CliError,
    Console,
    ConsoleConfig,
    render_approval,
    render_control,
    render_decision,
    render_retry,
    render_status,
    render_thread,
    render_ticket,
)
from stableagent.supervisor.router import RouterError, RouterPermissionError
from stableagent.supervisor.store import SupervisorStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m stableagent.supervisor.cli",
        description="Supervisor 监管控制台（只读首屏 + 显式确认写命令）",
    )
    parser.add_argument("--db", required=True, help="SQLite 路径")
    parser.add_argument("--config", required=True, help="JSON 配置路径")
    parser.add_argument("--operator", required=True, help="执行人（大小写不敏感）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="首屏：注意力、Source 健康、队列与运行态")
    ticket = commands.add_parser("ticket", help="Ticket 详情与完整时间线")
    ticket.add_argument("ticket_id")
    thread = commands.add_parser("thread", help="Thread 时间线")
    thread.add_argument("thread_key")

    approve = commands.add_parser("approve", help="批准一条待审事项")
    approve.add_argument("approval_id")
    approve.add_argument("--confirm", required=True)
    deny = commands.add_parser("deny", help="拒绝一条待审事项")
    deny.add_argument("approval_id")
    deny.add_argument("--confirm", required=True)
    stop = commands.add_parser("stop", help="停止一个 Ticket")
    stop.add_argument("ticket_id")
    stop.add_argument("--confirm", required=True)
    stop.add_argument("--reason")
    retry = commands.add_parser("retry", help="重试失败 effect 或 FAILED Ticket")
    retry.add_argument("target_id")
    retry.add_argument("--confirm", required=True)
    reevaluate = commands.add_parser("reevaluate", help="重评估一条 Trigger")
    reevaluate.add_argument("trigger_id")
    reevaluate.add_argument("--confirm", required=True)
    pause = commands.add_parser("pause", help="暂停处理")
    pause.add_argument("--confirm", required=True)
    pause.add_argument("--reason")
    resume = commands.add_parser("resume", help="恢复处理")
    resume.add_argument("--confirm", required=True)
    resume.add_argument("--reason")
    return parser


def _load_config(path: str) -> ConsoleConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliError(1, f"配置读取失败：{error}") from error
    if not isinstance(raw, dict):
        raise CliError(1, "配置必须是 JSON 对象")
    try:
        return ConsoleConfig.model_validate(raw)
    except ValidationError as error:
        raise CliError(1, f"配置无效：{error}") from error


def _run(console: Console, args: argparse.Namespace) -> str:
    if args.command == "status":
        return render_status(console.status())
    if args.command == "ticket":
        return render_ticket(console.ticket(args.ticket_id))
    if args.command == "thread":
        return render_thread(console.thread(args.thread_key))
    if args.command == "approve":
        return render_approval(
            console.approve(args.approval_id, actor=args.operator, confirm=args.confirm),
            approved=True,
        )
    if args.command == "deny":
        return render_approval(
            console.deny(args.approval_id, actor=args.operator, confirm=args.confirm),
            approved=False,
        )
    if args.command == "stop":
        ticket = console.stop(
            args.ticket_id,
            actor=args.operator,
            confirm=args.confirm,
            reason=args.reason,
        )
        return f"stopped {ticket.id} status={ticket.status.value}"
    if args.command == "retry":
        return render_retry(console.retry(args.target_id, actor=args.operator, confirm=args.confirm))
    if args.command == "reevaluate":
        return render_decision(
            console.reevaluate(args.trigger_id, actor=args.operator, confirm=args.confirm)
        )
    if args.command == "pause":
        return render_control(
            console.pause(actor=args.operator, confirm=args.confirm, reason=args.reason)
        )
    if args.command == "resume":
        return render_control(
            console.resume(actor=args.operator, confirm=args.confirm, reason=args.reason)
        )
    raise CliError(1, f"未知命令：{args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_error:
        return int(exit_error.code or 0)
    clock = SystemClock()
    store: SupervisorStore | None = None
    try:
        config = _load_config(args.config)
        store = SupervisorStore(args.db, clock=clock)
        console = Console(store, clock=clock, ids=Uuid4Id(), config=config)
        output = _run(console, args)
    except RouterPermissionError as error:
        print(f"[4] {error.detail}", file=sys.stderr)
        return 4
    except CliError as error:
        print(str(error), file=sys.stderr)
        return error.code
    except (
        RouterError,
        UnknownApprovalError,
        ApprovalDecidedError,
        UnknownTicketError,
        KeyError,
    ) as error:
        print(f"[1] {error}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 运行测试确认通过**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_console.py \
  > /tmp/p6-m5-t7-green.txt 2>&1; tail -5 /tmp/p6-m5-t7-green.txt
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py \
  tests/unit/test_supervisor_runtime.py > /tmp/p6-m5-t7-regress.txt 2>&1
tail -5 /tmp/p6-m5-t7-regress.txt
.venv/bin/ruff check stableagent/supervisor/console.py stableagent/supervisor/cli.py \
  stableagent/supervisor/router.py stableagent/supervisor/service.py \
  stableagent/supervisor/runtime.py tests/unit/test_supervisor_console.py
.venv/bin/ruff format --check stableagent/supervisor/console.py \
  stableagent/supervisor/cli.py stableagent/supervisor/router.py \
  stableagent/supervisor/service.py stableagent/supervisor/runtime.py \
  tests/unit/test_supervisor_console.py
```

Expected：console `11 passed`；service + runtime 既有用例 0 failed。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/console.py stableagent/supervisor/cli.py \
  stableagent/supervisor/router.py stableagent/supervisor/service.py \
  stableagent/supervisor/runtime.py tests/unit/test_supervisor_console.py
git commit -m "feat: add supervisor console and confirmed cli"
```

---

### Task 8：CI FOCUS 35、控制台指南与 M5 出口（M5-8）

**Files:**
- Modify: `tests/unit/test_ci_focus_gate.py`
- Modify: `.github/workflows/ci.yml`
- Create: `docs/guides/supervisor-console.md`
- Modify: `docs/acceptance/p6-supervisor-beta.md`
- Modify: `README.md` / `docs/README.md` / `docs/acceptance/README.md` /
  `docs/Roadmap.md` / `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1~7 的全部产物。
- Produces: 35 文件心脏覆盖率闸门、控制台运维指南、M5-1~M5-8 账本与出口读数。

- [ ] **Step 1: 写失败测试（FOCUS 契约）**

`tests/unit/test_ci_focus_gate.py`：

- 在 `FOCUS_FILES` 末尾追加两项：

```python
    "supervisor/notifications.py",
    "supervisor/console.py",
```

- 测试函数与断言改为：

```python
def test_focus_covers_the_thirty_five_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 35
    assert "35 文件" in text  # 步骤名与注释同步
    assert text.count('".py"') == 0  # 不许用通配兜底
```

- [ ] **Step 2: 运行测试确认失败，再改 CI**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py \
  > /tmp/p6-m5-t8-red.txt 2>&1; tail -10 /tmp/p6-m5-t8-red.txt
```

Expected：`assert 33 == 35` 或 `"35 文件" not in text`。

`.github/workflows/ci.yml`：

- 步骤名改为
  `心脏模块覆盖率闸门（35 文件：loop/checkpoint/errors/context/evidence/orchestration/control/execution/integrations/supervisor 各 ≥90%）`。
- `FOCUS` 元组在 `"supervisor/runtime.py"` 之后追加：

```python
              "supervisor/notifications.py",
              "supervisor/console.py",
```

再次运行：

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py \
  > /tmp/p6-m5-t8-green.txt 2>&1; tail -5 /tmp/p6-m5-t8-green.txt
```

Expected：`1 passed`。

- [ ] **Step 3: 写控制台指南**

新建 `docs/guides/supervisor-console.md`：

```markdown
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
```

- [ ] **Step 4: 回填账本与状态面**

`docs/acceptance/p6-supervisor-beta.md`：

- §1 的 `### M5：最小监管面与注意力协议（规格 WP-6.5；待 M4 收口后展开）` 改为收口
  状态行，并按 M1~M4 的格式补齐 `- [x] **M5-1**` ~ `- [x] **M5-8**` 八条（每条写
  对应提交与实测用例数）。
- §3 的 views 行改为：

```bash
.venv/bin/pytest -q tests/unit/test_supervisor_views.py \
  tests/unit/test_supervisor_notifications.py tests/unit/test_supervisor_console.py \
  tests/unit/test_ci_focus_gate.py
```

- §4 追加 M5 的 Task/Step 计数（以实际 `- [ ] **Step` 条数为准）。
- 把 §12 末尾既有的 `### M5 自检记录（writing-plans）` 与
  `### M5 出口读数（2026-11-09，落盘 /tmp/p6-m5-*.txt 后读回）` 回填：记录全量/
  三解释器/覆盖率/静态/边界四零/计划偏差，全部以读回文件为准，不新增重复小节。

状态面：

```markdown
| P6 | Single-repository Super-Agent Beta | 🟡 active — M5 done (8/8); milestones 5/6 |
```

`docs/README.md` 与 `docs/acceptance/README.md` 的 P6 状态行同步为
`P6 进行中（M5 完成，8/8；里程碑 5/6）`；`docs/README.md` 的状态表与指南索引改为
M5 完成口径并链接 `guides/supervisor-console.md`；`docs/Roadmap.md` 的"当前进度"改为
M1~M5 完成，`下一步：M6 Beta 演练与发布门禁（规格 WP-6.6）`；`CHANGELOG.md` 在
`## [Unreleased]` 下新增 `### 实现（P6 M5 Console，2026-11-09）`，写首屏/详情/
通知草拟/确认写命令/FOCUS 35 与门禁读数。

- [ ] **Step 5: 全量门禁**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m5-full.txt 2>&1
tail -5 /tmp/p6-m5-full.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff check stableagent tests examples benchmarks \
  > /tmp/p6-m5-ruff.txt 2>&1; cat /tmp/p6-m5-ruff.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff format --check stableagent tests examples benchmarks \
  > /tmp/p6-m5-format.txt 2>&1; tail -3 /tmp/p6-m5-format.txt
env -u NO_PROXY -u no_proxy .venv/bin/mypy > /tmp/p6-m5-mypy.txt 2>&1
tail -3 /tmp/p6-m5-mypy.txt
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m5-l1.txt; cat /tmp/p6-m5-l1.txt
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py \
  > /tmp/p6-m5-c1.txt; cat /tmp/p6-m5-c1.txt
grep -rn "stableagent.supervisor" stableagent/core stableagent/orchestration \
  stableagent/control stableagent/execution stableagent/integrations \
  > /tmp/p6-m5-reverse.txt; cat /tmp/p6-m5-reverse.txt
git diff --check > /tmp/p6-m5-diffcheck.txt; cat /tmp/p6-m5-diffcheck.txt
.venv/bin/python -m stableagent.supervisor.cli --help > /tmp/p6-m5-help.txt 2>&1
head -3 /tmp/p6-m5-help.txt
```

Expected：全量全绿；`notifications.py` 与 `console.py` 行覆盖率 ≥90%；L1/C1/reverse/
diffcheck 四个文件均为空；`--help` 输出 usage 且退出 0。

- [ ] **Step 6: 三解释器与覆盖率读数**

```bash
env -u NO_PROXY -u no_proxy .matrix/py311/bin/pytest -q > /tmp/p6-m5-py311.txt 2>&1
tail -3 /tmp/p6-m5-py311.txt
env -u NO_PROXY -u no_proxy .matrix/py312/bin/pytest -q > /tmp/p6-m5-py312.txt 2>&1
tail -3 /tmp/p6-m5-py312.txt
grep -E "supervisor/(notifications|console)\.py" /tmp/p6-m5-full.txt
```

Expected：3.11 / 3.12 与 3.10 同为 `passed, skipped`；两个新文件覆盖率 ≥90%。

- [ ] **Step 7: 提交**

```bash
git add tests/unit/test_ci_focus_gate.py .github/workflows/ci.yml \
  docs/guides/supervisor-console.md docs/acceptance/p6-supervisor-beta.md \
  docs/specs/p6-single-repository-super-agent-beta.md \
  README.md docs/README.md docs/acceptance/README.md docs/Roadmap.md CHANGELOG.md
git commit -m "docs: close p6 m5 with console and attention evidence"
```

---

### M5 自检记录（writing-plans）

- **规格覆盖：** WP-6.5 首屏/详情 → Task 1/2；通知模板与限长 → Task 3；草拟、digest、
  去重与静默 → Task 4；批准 + effect 同事务、失败 effect retry、stop 撤销 → Task 5；
  Runtime 接线、跨进程 resume 与集成证据 → Task 6；CLI 命令面、确认与退出码 → Task 7；
  FOCUS 35、指南与账本 → Task 8。
- **类型一致性：** `NotificationConfig` / `NotificationKind` / `NotificationDraft` /
  `NotificationDrafter` 只在 Task 3/4 定义；`QueueCounts` / `StatusSections` /
  `ticket_links` / `thread_url` 只在 Task 1 定义，Task 2/4 只追加消费；
  `ConsoleConfig` / `CliError` / `Console` / `StatusView` 只在 Task 7 定义。
  `RuntimeStep.drafted` 与 `RuntimeConfig.notifications` 在 Task 6 一次加齐。
- **无占位符：** 每个实现步骤给出完整代码；出口读数与用例总数在实测后回填，不预填。
- **自检修正：** 通知只对 `origin_kind="github"` 且 `mandate.actions` 含 `comment_issue`
  的 Ticket 草拟（与 spec 对齐）；`Review` 的展示链接按 spec 回落仓库 URL，但评论 URL
  独立回落其 PR；`retry` 先查 effect 再查 Ticket，两条路径都要求 `FAILED`；CLI 对非
  `notification:` 审批不重算 digest，避免 P5 既有审批被 M5 误伤。复审另修：两类测试
  种子按 ticket id 派生 mandate/assignment/subscription id，避免双 Ticket 主键冲突；
  CLI 非 operator 用例的 `--operator mallory` 参数切片修正；Task 7 增加命令矩阵用例，
  使 `console.py` 的渲染器与全部 `_run` 分支进入 FOCUS 90% 覆盖；Task 6 补齐 WP-6.5
  第 6 条要求的集成证据（批准后只派发一次且跨重启不重放、拒绝静默、每个状态至多草拟
  一次），Step 总数随之修正为 44。
- **边界：** `notifications.py` 只 import `github_effects.github_resource` 纯函数，不
  import client/handler；`console.py` 不 import `source` / `execution` / `integrations`；
  `cli.py` 不做网络；所有事务在 Store 方法或 `ApprovalService` 内（L3）；外部正文不进
  授权（L4）；不新增 `EffectKind`（L6）；配置与输出不含凭据（L7）。

### M5 出口读数（2026-11-09，落盘 `/tmp/p6-m5-*.txt` 后读回）

- **全量：** `/tmp/p6-m5-full.txt` → `874 passed, 6 skipped in 20.34s`；总覆盖率
  `98.53%`（门槛 70%）。
- **三解释器：** 3.10 `874 passed, 6 skipped`；`/tmp/p6-m5-py311.txt` 与
  `/tmp/p6-m5-py312.txt` 同为 `874 passed, 6 skipped`，总覆盖率均 `98.53%`。
- **心脏覆盖率：** `supervisor/notifications.py` `96%`（109 行、4 行未覆盖）、
  `supervisor/console.py` `96%`（163 行、6 行未覆盖），均 ≥90%；FOCUS 35 契约
  `1 passed`。
- **静态：** `/tmp/p6-m5-ruff.txt` → `All checks passed!`；
  `/tmp/p6-m5-format.txt` → `160 files already formatted`；
  `/tmp/p6-m5-mypy.txt` → `Success: no issues found in 72 source files`。
- **边界四零：** `/tmp/p6-m5-l1.txt`、`/tmp/p6-m5-c1.txt`、`/tmp/p6-m5-reverse.txt`、
  `/tmp/p6-m5-diffcheck.txt` 均为 0 字节——`stableagent/core/` 与
  `orchestration/service.py` 对 `234f620` 零 diff，既有包无反向 import。
- **CLI：** `/tmp/p6-m5-help.txt` 输出 `usage:` 且退出 0。
- **计划偏差：** 8 个 Task / 44 个 Step 全部完成；仅有三处格式对齐（多行 `frozenset`、
  `pause/resume` 签名与 CLI 断言折行）与两处未使用 import 清理，均不改变行为；
  其余与计划一致。

---

## 13. M6 任务级实施计划（writing-plans 产出，2026-11-10）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 给单仓库 Beta 一个可复现的离线演练、只读 Beta 数据报告与真实仓库 runbook，
并把 ADR-06 与 P6 退出清单收口到"待维护者回填真实 Beta 数据"的状态。

**Architecture：** 读侧在 `supervisor/store.py` 加 `BetaSnapshot` + `beta_snapshot()`（纯 SQL
聚合），在 `supervisor/views.py` 加 `BetaReport` + `beta_report(store)`（只读 pydantic 视图）；
`examples/06_superagent_beta.py` 用 `FakeGitHubReadClient` + 确定性时钟 + 脚本化 Worker 走
Source→Router→Scheduler→通知审批→operator resume 全链路，`run`/`report` 两个 phase；
真实仓库 Beta 由维护者按 `docs/guides/supervisor-beta.md` 执行，M6 不实现真实 GitHub HTTP
客户端。不新增 `EffectKind`、不新增 FOCUS 文件（`store.py`/`views.py` 已在 35 文件内）。

**Tech Stack：** Python 3.10/3.11/3.12、pydantic v2、SQLite（json_each）、pytest
（asyncio_mode=auto）、ruff、mypy；示例沿用 04/05 的逐行 JSON + 末行结果约定。

**Spec：** [P6 实施规格](../specs/p6-single-repository-super-agent-beta.md) WP-6.6 与
§2.5；设计决议见同文件 `WP-6.6 设计决议（2026-11-10，维护者确认）`。

**边界裁定（维护者已确认）：**

- 离线演练 + runbook：M6 不实现真实 GitHub HTTP 客户端；真实试跑、dry-run 数据与人工
  标注由维护者按 runbook 回填，P6 出口第 7 项在回填前保持未勾。
- Beta 报告是只读视图，不是新命令面：示例与 CLI 都不直接读 `_conn`；误报/漏报与人工
  接受/拒绝原因只进 runbook 人工记录表。
- 演练只覆盖规格原定场景（多 Trigger、两个并发 Ticket、FAILED/WAITING/COMPLETED 后评论
  恢复）；崩溃恢复与暂停语义已由 M4/M5 测试覆盖，本示例不重复。

**Global Constraints（每个 Task 都隐含遵守）：**

- L1：`stableagent/core/` 对 `234f620` 零 diff；约束 1：`orchestration/service.py` 零字符改动。
- L2：方向锁 `supervisor → integrations → execution → control → orchestration → core`；
  既有包不得 import `stableagent.supervisor`。
- L3：`Store.transaction()` 体内不得出现 `await`；`beta_snapshot()` 只读、不开写事务。
- L4：外部正文只进 digest/Report/TicketInput，不参与授权；`BetaReport` 不返回
  `Trigger.payload` / `TicketInput.payload` / 日志正文。
- L5：六个 revalidate 时机保持审计（resume 走 `launch` 复检，不改时机集合）。
- L6：不新增 `merge` / `enable_auto_merge` 路径；`EffectKind` 不加成员。
- L7：不读凭据；配置 `extra="forbid"`，不出现 token/secret 字段。
- L8：全部用例离线（Fake Source / 确定性时钟）；定向测试加 `--no-cov`；真实
  Docker/网络/GitHub 与推送必须获得维护者显式授权。

---

### Task 1：BetaSnapshot 与 BetaReport 只读聚合（M6-1）

**Files:**
- Modify: `stableagent/supervisor/store.py`
- Modify: `stableagent/supervisor/views.py`
- Test: `tests/unit/test_supervisor_views.py`

**Interfaces:**
- Consumes: `SupervisorStore._conn`（只读 SELECT）、`list_source_cursors()`；
  既有表 `triggers/decisions/tickets/assignments/approval_requests/effects/source_cursors`。
- Produces:
  - `BetaSnapshot`（frozen dataclass，11 字段，见下）
  - `SupervisorStore.beta_snapshot() -> BetaSnapshot`
  - `BetaReport`（frozen pydantic，同 11 字段，`source_health: tuple[SourceCursor, ...]`）
  - `beta_report(store: SupervisorStore) -> BetaReport`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_views.py` 第 37 行 import 改为：

```python
from stableagent.supervisor.views import SupervisorViews, beta_report, thread_url
```

在文件末尾追加（`Decision`/`Trigger`/`SourceCursor` 等顶部已 import）：

```python
def test_beta_report_aggregates_rehearsal_metrics(store: SupervisorStore) -> None:
    _ticket(store, "tk-a", TicketStatus.QUEUED)
    _ticket(store, "tk-b", TicketStatus.FAILED)
    store.insert_trigger(
        Trigger(
            id="tg-1",
            external_id="github:o/r:issue:1",
            kind=TriggerKind.ISSUE,
            actor="alice",
            thread_keys=["issue:o/r#1"],
            occurred_at=1,
            received_at=2,
            payload={"body": "DO-NOT-LEAK"},
        )
    )
    store.insert_trigger(
        Trigger(
            id="tg-2",
            external_id="github:o/r:ci_status:9",
            kind=TriggerKind.CI_STATUS,
            actor="github",
            thread_keys=["repo:o/r"],
            occurred_at=3,
            received_at=4,
            payload={},
        )
    )
    for decision_id, digest, outcome, strength, reasons, trigger_ids in (
        ("dc-1", "d-1", DecisionOutcome.ACCEPT, DecisionStrength.EXPLICIT, ["admitted"], ["tg-1"]),
        (
            "dc-2",
            "d-2",
            DecisionOutcome.OBSERVE,
            DecisionStrength.INFERRED,
            ["inferred_candidate"],
            [],
        ),
        (
            "dc-3",
            "d-3",
            DecisionOutcome.OBSERVE,
            DecisionStrength.INFERRED,
            ["ci_context_only"],
            ["tg-2"],
        ),
        (
            "dc-4",
            "d-4",
            DecisionOutcome.OBSERVE,
            DecisionStrength.INFERRED,
            ["already_owned"],
            [],
        ),
    ):
        store.insert_decision(
            Decision(
                id=decision_id,
                trigger_ids=trigger_ids,
                outcome=outcome,
                strength=strength,
                reason_codes=reasons,
                policy_version="p6-v1",
                request_digest=digest,
                created_at=5,
            )
        )
    store.insert_assignment(Assignment(id="as-1", ticket_id="tk-a", attempt=1))
    store.insert_assignment(Assignment(id="as-2", ticket_id="tk-a", attempt=2))
    store.insert_approval(
        id="ap-1",
        ticket_id="tk-a",
        action="comment_issue",
        resource_digest="ra-1",
        reason="notification:accepted",
        requested_at=1,
    )
    store.insert_approval(
        id="ap-2",
        ticket_id="tk-a",
        action="comment_issue",
        resource_digest="ra-2",
        reason="notification:result",
        requested_at=2,
        status="approved",
        decided_by="alice",
        decided_at=3,
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            state=EffectState.SUCCEEDED,
            attempts=2,
            due_at=1,
        )
    )
    store.enqueue_effect(
        Effect(
            id="ef-2",
            kind=EffectKind.LAUNCH_ASSIGNMENT,
            idempotency_key="k-2",
            state=EffectState.PENDING,
            due_at=1,
        )
    )
    store.put_source_cursor(SourceCursor(source="github", cursor="9", health=SourceHealth.OK))

    report = beta_report(store)

    assert report.triggers == {"issue": 1, "ci_status": 1}
    assert report.decisions == {"accept": 1, "observe": 3}
    assert report.decision_reasons == {
        "admitted": 1,
        "already_owned": 1,
        "ci_context_only": 1,
        "inferred_candidate": 1,
    }
    assert report.would_accept == 1
    assert report.tickets == {"queued": 1, "failed": 1}
    assert report.recovered_assignments == 1
    assert report.approvals == {"comment_issue:approved": 1, "comment_issue:pending": 1}
    assert report.notification_counts == {"tk-a": 2}
    assert report.effects == {"github_comment:succeeded": 1, "launch_assignment:pending": 1}
    assert report.retried_effects == 1
    assert [cursor.health for cursor in report.source_health] == [SourceHealth.OK]
    assert "DO-NOT-LEAK" not in report.model_dump_json()


def test_beta_report_on_empty_database_is_zero(store: SupervisorStore) -> None:
    report = beta_report(store)
    assert report.triggers == {}
    assert report.decisions == {}
    assert report.decision_reasons == {}
    assert report.would_accept == 0
    assert report.tickets == {}
    assert report.recovered_assignments == 0
    assert report.approvals == {}
    assert report.notification_counts == {}
    assert report.effects == {}
    assert report.retried_effects == 0
    assert report.source_health == ()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_views.py -k beta_report`
Expected: FAIL / ImportError —— `beta_report` 尚未定义。

- [ ] **Step 3: 实现 store 聚合**

`stableagent/supervisor/store.py` 顶部 import 区加 `from dataclasses import dataclass`
（放在 `import sqlite3` 之后、`from pathlib import Path` 之前，保持 ruff I001 顺序）。
在 `_row_to_runtime_control` 之后、`class SupervisorStore` 之前加：

```python
@dataclass(frozen=True)
class BetaSnapshot:
    triggers: dict[str, int]
    decisions: dict[str, int]
    decision_reasons: dict[str, int]
    would_accept: int
    tickets: dict[str, int]
    recovered_assignments: int
    approvals: dict[str, int]
    notification_counts: dict[str, int]
    effects: dict[str, int]
    retried_effects: int
    source_health: tuple[SourceCursor, ...]
```

在 `SupervisorStore.list_tickets_for_notifications` 之后（类末尾）加：

```python
    # ---------------------------------------------------------- Beta 只读报告
    def beta_snapshot(self) -> BetaSnapshot:
        """WP-6.6 决议 2：Beta 数据报告的事实源；只读聚合，不返回任何 payload。"""
        return BetaSnapshot(
            triggers=self._group_counts(
                "SELECT kind, COUNT(*) FROM triggers GROUP BY kind ORDER BY kind"
            ),
            decisions=self._group_counts(
                "SELECT outcome, COUNT(*) FROM decisions GROUP BY outcome ORDER BY outcome"
            ),
            decision_reasons=self._group_counts(
                "SELECT j.value, COUNT(*) FROM decisions d, json_each(d.reason_codes) j"
                " GROUP BY j.value ORDER BY j.value"
            ),
            would_accept=self._count(
                "SELECT COUNT(*) FROM decisions d WHERE d.outcome = 'observe'"
                " AND d.strength = 'inferred' AND EXISTS ("
                " SELECT 1 FROM json_each(d.reason_codes) WHERE value = 'inferred_candidate')"
            ),
            tickets=self._group_counts(
                "SELECT status, COUNT(*) FROM tickets GROUP BY status ORDER BY status"
            ),
            recovered_assignments=self._count(
                "SELECT COUNT(*) FROM assignments WHERE attempt > 1"
            ),
            approvals=self._group_counts(
                "SELECT action || ':' || status, COUNT(*) FROM approval_requests"
                " GROUP BY action, status ORDER BY action, status"
            ),
            notification_counts=self._group_counts(
                "SELECT ticket_id, COUNT(*) FROM approval_requests"
                " WHERE reason LIKE 'notification:%' GROUP BY ticket_id ORDER BY ticket_id"
            ),
            effects=self._group_counts(
                "SELECT kind || ':' || state, COUNT(*) FROM effects"
                " GROUP BY kind, state ORDER BY kind, state"
            ),
            retried_effects=self._count("SELECT COUNT(*) FROM effects WHERE attempts > 1"),
            source_health=tuple(self.list_source_cursors()),
        )

    def _group_counts(self, sql: str) -> dict[str, int]:
        return {str(row[0]): int(row[1]) for row in self._conn.execute(sql).fetchall()}

    def _count(self, sql: str) -> int:
        return int(self._conn.execute(sql).fetchone()[0])
```

- [ ] **Step 4: 实现 views 报告**

`stableagent/supervisor/views.py` 在 `StatusSections` 之后、`thread_url` 之前加：

```python
class BetaReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    triggers: dict[str, int]
    decisions: dict[str, int]
    decision_reasons: dict[str, int]
    would_accept: int
    tickets: dict[str, int]
    recovered_assignments: int
    approvals: dict[str, int]
    notification_counts: dict[str, int]
    effects: dict[str, int]
    retried_effects: int
    source_health: tuple[SourceCursor, ...]


def beta_report(store: SupervisorStore) -> BetaReport:
    snapshot = store.beta_snapshot()
    return BetaReport(
        triggers=snapshot.triggers,
        decisions=snapshot.decisions,
        decision_reasons=snapshot.decision_reasons,
        would_accept=snapshot.would_accept,
        tickets=snapshot.tickets,
        recovered_assignments=snapshot.recovered_assignments,
        approvals=snapshot.approvals,
        notification_counts=snapshot.notification_counts,
        effects=snapshot.effects,
        retried_effects=snapshot.retried_effects,
        source_health=snapshot.source_health,
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_views.py`
Expected: PASS（既有 13 例 + 新增 2 例 = 15 passed）。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/store.py stableagent/supervisor/views.py \
  tests/unit/test_supervisor_views.py
git commit -m "feat: add read-only beta report aggregation"
```

---

### Task 2：示例 06 run phase 离线演练（M6-2）

**Files:**
- Create: `examples/06_superagent_beta.py`
- Test: `tests/integration/test_superagent_beta.py`

**Interfaces:**
- Consumes: Task 1 的 `beta_report`（本 Task 暂不 import，report phase 在 Task 3 接）；
  `build_runtime` / `RuntimeBundle` / `RuntimeConfig` / `RouterConfig` / `GitHubSource` /
  `FakeGitHubReadClient` / `NotificationDrafter` / `notification_effect` /
  `ApprovalService.decide(..., effect=...)` / `SupervisorService.resume` /
  `OrchestrationService.complete_ticket`。
- Produces: `examples/06_superagent_beta.py --phase run`，末行
  `BETA_RESULT tickets=3 resumed=1 external_comments=1 duplicate_tickets=0 bot_triggers=0`；
  落盘 `<dir>/beta.db`。

- [ ] **Step 1: 写失败集成测试**

创建 `tests/integration/test_superagent_beta.py`：

```python
"""WP-6.6：示例 06 离线演练与只读 Beta 报告（全离线，无 token、无公网）。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from stableagent.orchestration.model import SystemClock
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import beta_report

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "06_superagent_beta.py"


async def _run_example(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(EXAMPLE),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    out = (await proc.stdout.read()).decode()
    return await proc.wait(), out


async def test_run_phase_rehearses_the_beta_scenario(tmp_path: Path) -> None:
    code, out = await _run_example("--phase", "run", "--dir", str(tmp_path / "demo"))
    assert code == 0, out
    assert (
        "BETA_RESULT tickets=3 resumed=1 external_comments=1 "
        "duplicate_tickets=0 bot_triggers=0" in out
    )
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert any(line.get("event") == "resume" for line in lines)

    store = SupervisorStore(tmp_path / "demo" / "beta.db", clock=SystemClock())
    try:
        tickets = store.list_tickets()
        assert len(tickets) == 3
        assert len({ticket.mandate_id for ticket in tickets}) == 3
        assert sorted(ticket.status.value for ticket in tickets) == ["failed", "ready", "waiting"]
        resumed = [
            ticket
            for ticket in tickets
            if any(a.attempt == 2 for a in store.list_assignments(ticket.id))
        ]
        assert len(resumed) == 1
        assert all(trigger.actor != "agent" for trigger in store.list_triggers())
        report = beta_report(store)
        assert report.would_accept == 1
        assert report.recovered_assignments == 1
        assert report.approvals.get("comment_issue:approved") == 1
        assert report.effects.get("github_comment:succeeded") == 1
        assert sum(report.notification_counts.values()) >= 3
    finally:
        store.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/integration/test_superagent_beta.py`
Expected: FAIL —— 示例文件不存在，子进程非 0 退出。

- [ ] **Step 3: 实现示例 run phase**

创建 `examples/06_superagent_beta.py`：

```python
"""P6 Beta 离线演练：多 Trigger、两个并发 Ticket、失败/等待人工/评论恢复各走一遍。

- 装置全是本地的：FakeGitHubReadClient + 确定性时钟 + 脚本化 Worker，不读 token、不连公网。
- `--phase run`：Issue/PR/CI Trigger 经 Source→Router 路由；两个 Ticket 并发执行，
  一个 FAILED、一个 WAITING、一个 READY→COMPLETED 后被后续评论经 operator resume 恢复；
  通知先草拟成审批，只批准一条，外部评论恰好一次。
- 事件按行输出 JSON；末行 `BETA_RESULT ...`。

用法：
    python examples/06_superagent_beta.py --phase run --dir .demo-beta
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
)
from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    Effect,
    EffectKind,
    ReportKind,
    Ticket,
    TicketAction,
    TicketInput,
    TicketStatus,
)
from stableagent.orchestration.service import OrchestrationService
from stableagent.supervisor.model import DecisionOutcome
from stableagent.supervisor.notifications import (
    NotificationConfig,
    NotificationDrafter,
    notification_effect,
)
from stableagent.supervisor.router import RouterConfig
from stableagent.supervisor.runtime import RuntimeBundle, RuntimeConfig, build_runtime
from stableagent.supervisor.source import GitHubSource
from stableagent.supervisor.store import SupervisorStore

REPOSITORY = "owner/repo"
BOT_LOGIN = "agent"
OPERATOR = "alice"
BASE = 1_700_000_000_000
TASK_A = "@agent fix the flaky parser test"
TASK_B = "@agent investigate the memory spike"
TASK_C = "@agent document the release checklist"
FOLLOW_UP = "@agent please also cover the empty-input case"
OUTCOMES = {TASK_A: "ready", TASK_B: "failed", TASK_C: "waiting"}


class DemoClock:
    """每次 now_ms() 前进 1ms：单进程内确定，不读墙钟。"""

    def __init__(self, start: int = BASE) -> None:
        self._now = start

    def now_ms(self) -> int:
        self._now += 1
        return self._now


class DemoIds:
    """确定性 ID：同前缀按序号递增。"""

    def __init__(self) -> None:
        self._seq: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self._seq[prefix] = self._seq.get(prefix, 0) + 1
        return f"{prefix}-{self._seq[prefix]:04d}"


def _emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True))


def _items() -> list[GitHubReadItem]:
    return [
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE,
            occurred_at=BASE + 1,
            resource={
                "number": 1,
                "user": {"login": OPERATOR},
                "body": TASK_A,
                "html_url": "https://github.com/owner/repo/issues/1",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE,
            occurred_at=BASE + 2,
            resource={
                "number": 2,
                "user": {"login": OPERATOR},
                "body": TASK_B,
                "html_url": "https://github.com/owner/repo/issues/2",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE,
            occurred_at=BASE + 3,
            resource={
                "number": 4,
                "user": {"login": OPERATOR},
                "body": TASK_C,
                "html_url": "https://github.com/owner/repo/issues/4",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.PULL_REQUEST,
            occurred_at=BASE + 4,
            resource={
                "number": 3,
                "user": {"login": OPERATOR},
                "html_url": "https://github.com/owner/repo/pull/3",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.CI_STATUS,
            occurred_at=BASE + 5,
            resource={"id": 900, "actor": "github-actions", "sha": "abc123", "state": "success"},
        ),
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE_COMMENT,
            occurred_at=BASE + 6,
            resource={
                "id": 501,
                "issue_number": 1,
                "user": {"login": BOT_LOGIN},
                "body": "I am working on it",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE,
            occurred_at=BASE + 1,
            resource={"number": 1, "user": {"login": OPERATOR}, "body": TASK_A},
        ),
    ]


class ScriptedExecutor:
    """脚本化 Worker：launch 即按任务剧本落 Report 并翻态；不读外部正文做授权。"""

    def __init__(self, service: OrchestrationService) -> None:
        self._service = service
        self.launched: list[str] = []

    async def launch(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        self.launched.append(assignment.id)
        outcome = OUTCOMES[ticket.task]
        if outcome == "failed":
            self._service.add_report(
                ticket_id=ticket.id,
                assignment_id=assignment.id,
                kind=ReportKind.FAILED,
                summary="Memory spike reproduced; needs a human decision",
            )
            self._service.finish_assignment(
                assignment.id, AssignmentAction.FAIL, failure="needs_human_decision"
            )
            self._service.transition_ticket(ticket.id, TicketAction.FAIL)
        elif outcome == "waiting":
            self._service.add_report(
                ticket_id=ticket.id,
                assignment_id=assignment.id,
                kind=ReportKind.WAITING_FOR_HUMAN,
                summary="Which release window should the checklist target?",
            )
            self._service.transition_ticket(ticket.id, TicketAction.WAIT)
        else:
            self._service.add_report(
                ticket_id=ticket.id,
                assignment_id=assignment.id,
                kind=ReportKind.READY,
                summary="Parser test deflaked; ready for review",
            )
            self._service.finish_assignment(assignment.id, AssignmentAction.END)
            self._service.transition_ticket(ticket.id, TicketAction.DELIVER)

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        return None

    async def cancel(self, assignment: Assignment) -> None:
        return None

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None:
        return None


def _build(
    root: Path, client: FakeGitHubReadClient, comments: list[dict[str, Any]]
) -> tuple[
    SupervisorStore, DemoClock, DemoIds, RuntimeBundle, ScriptedExecutor, OrchestrationService
]:
    root.mkdir(parents=True, exist_ok=True)
    clock = DemoClock()
    ids = DemoIds()
    store = SupervisorStore(root / "beta.db", clock=clock)
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository=REPOSITORY,
            actors=frozenset({OPERATOR}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.COMMENT_ISSUE}),
            default_validity_ms=3_600_000,
        ),
        clock=clock,
        ids=ids,
    )
    source = GitHubSource(store, client, repository=REPOSITORY, bot_login=BOT_LOGIN, clock=clock)
    orchestration = OrchestrationService(store, clock=clock, ids=ids)
    executor = ScriptedExecutor(orchestration)

    async def comment_handler(effect: Effect) -> None:
        comments.append(dict(effect.payload))

    bundle = build_runtime(
        store=store,
        policy=policy,
        source=source,
        executor=executor,
        effect_handlers={EffectKind.GITHUB_COMMENT: comment_handler},
        config=RuntimeConfig(
            router=RouterConfig(
                repository=REPOSITORY,
                operators=frozenset({OPERATOR}),
                actions=("read_repository", "comment_issue"),
            ),
            owner="beta-demo",
            lock_ttl_ms=60_000,
            stop_grace_ms=50,
            step_interval_ms=0,
            notifications=NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        ),
        clock=clock,
        ids=ids,
        scheduler_max_concurrency=2,
    )
    return store, clock, ids, bundle, executor, orchestration


async def _drain(bundle: RuntimeBundle, *, rounds: int = 4) -> None:
    for _ in range(rounds):
        await bundle.runtime.step()
        await asyncio.sleep(0)


async def _run_phase(root: Path) -> int:
    client = FakeGitHubReadClient(_items())
    comments: list[dict[str, Any]] = []
    store, clock, ids, bundle, executor, orchestration = _build(root, client, comments)
    try:
        bundle.runtime.start()
        await _drain(bundle)
        tickets = {ticket.task: ticket for ticket in store.list_tickets()}
        assert set(tickets) == {TASK_A, TASK_B, TASK_C}, tickets
        ticket_a, ticket_b, ticket_c = tickets[TASK_A], tickets[TASK_B], tickets[TASK_C]
        assert ticket_a.status is TicketStatus.READY, ticket_a
        assert ticket_b.status is TicketStatus.FAILED, ticket_b
        assert ticket_c.status is TicketStatus.WAITING, ticket_c
        assert len(executor.launched) == 3, executor.launched
        _emit("routed", tickets=len(tickets), launched=len(executor.launched))

        approvals = ApprovalService(store, clock=clock, ids=ids)
        pending = approvals.for_ticket(ticket_b.id, status=ApprovalStatus.PENDING)
        failure = next(item for item in pending if item.reason == "notification:failure")
        drafter = NotificationDrafter(
            store,
            approvals,
            NotificationConfig(enabled=True, max_chars=400, since_ms=0),
            repository=REPOSITORY,
            clock=clock,
        )
        draft = drafter.build(ticket_b.id)
        assert draft is not None
        effect = notification_effect(draft, effect_id=ids.new_id("ef"), now=clock.now_ms())
        approvals.decide(failure.id, True, OPERATOR, effect=effect)
        await bundle.runtime.step()
        assert len(comments) == 1, comments
        _emit("approval", approval=failure.id, status="approved", external_comments=len(comments))

        orchestration.complete_ticket(ticket_a.id)
        client.items.append(
            GitHubReadItem(
                kind=GitHubReadKind.ISSUE_COMMENT,
                occurred_at=BASE + 10,
                resource={
                    "id": 502,
                    "issue_number": 1,
                    "user": {"login": OPERATOR},
                    "body": FOLLOW_UP,
                    "html_url": "https://github.com/owner/repo/issues/1#issuecomment-502",
                },
            )
        )
        await _drain(bundle)
        follow = store.get_trigger_by_external("github", "github:owner/repo:issue_comment:502")
        assert follow is not None
        decisions = store.list_decisions(trigger_id=follow.id)
        assert any(
            decision.outcome is DecisionOutcome.OBSERVE
            and "resume_requires_operator" in decision.reason_codes
            for decision in decisions
        ), decisions
        _emit("follow_up", trigger=follow.id, decision="resume_requires_operator")

        bundle.service.resume(ticket_a.id, trigger_id=follow.id, actor=OPERATOR)
        await _drain(bundle)
        resumed_ticket = store.get_ticket(ticket_a.id)
        assert resumed_ticket is not None and resumed_ticket.status is TicketStatus.READY
        attempts = [item.attempt for item in store.list_assignments(ticket_a.id)]
        assert attempts == [1, 2], attempts
        _emit("resume", ticket=ticket_a.id, attempts=attempts)

        await bundle.runtime.stop()
        all_tickets = store.list_tickets()
        resumed = sum(
            1
            for ticket in all_tickets
            if any(item.attempt > 1 for item in store.list_assignments(ticket.id))
        )
        duplicate_tickets = len(all_tickets) - len({ticket.mandate_id for ticket in all_tickets})
        bot_triggers = sum(1 for trigger in store.list_triggers() if trigger.actor == BOT_LOGIN)
        print(
            f"BETA_RESULT tickets={len(all_tickets)} resumed={resumed} "
            f"external_comments={len(comments)} duplicate_tickets={duplicate_tickets} "
            f"bot_triggers={bot_triggers}"
        )
        ok = (len(all_tickets), resumed, len(comments), duplicate_tickets, bot_triggers) == (
            3,
            1,
            1,
            0,
            0,
        )
        return 0 if ok else 1
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("run",), default="run")
    parser.add_argument("--dir", default=".demo-beta")
    args = parser.parse_args(argv)
    root = Path(args.dir).resolve()
    return asyncio.run(_run_phase(root))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q --no-cov tests/integration/test_superagent_beta.py`
Expected: PASS（1 passed）。若 Worker 翻态时序不稳，先核对 `_drain` 的 `sleep(0)`
是否让脚本化 launch 任务跑完，再调整 rounds，不改断言。

- [ ] **Step 5: 提交**

```bash
git add examples/06_superagent_beta.py tests/integration/test_superagent_beta.py
git commit -m "feat: rehearse single-repository beta offline"
```

---

### Task 3：示例 06 report phase（M6-3）

**Files:**
- Modify: `examples/06_superagent_beta.py`
- Test: `tests/integration/test_superagent_beta.py`

**Interfaces:**
- Consumes: Task 1 `beta_report`；Task 2 落盘的 `<dir>/beta.db`。
- Produces: `--phase report` 末行 `BETA_REPORT <json>`（`BetaReport.model_dump(mode="json")`，
  `sort_keys=True`）；只读，不写业务数据、不发外部消息。

- [ ] **Step 1: 写失败测试**

`tests/integration/test_superagent_beta.py` 末尾追加：

```python
async def test_report_phase_reads_the_same_database(tmp_path: Path) -> None:
    code, out = await _run_example("--phase", "run", "--dir", str(tmp_path / "demo"))
    assert code == 0, out
    code, out = await _run_example("--phase", "report", "--dir", str(tmp_path / "demo"))
    assert code == 0, out
    line = next(item for item in out.splitlines() if item.startswith("BETA_REPORT "))
    payload = json.loads(line.removeprefix("BETA_REPORT "))
    assert payload["tickets"] == {"failed": 1, "ready": 1, "waiting": 1}
    assert payload["would_accept"] == 1
    assert payload["recovered_assignments"] == 1
    assert payload["retried_effects"] == 0
    assert payload["effects"]["github_comment:succeeded"] == 1
    assert payload["approvals"]["comment_issue:approved"] == 1
    assert payload["triggers"]["issue_comment"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/integration/test_superagent_beta.py -k report_phase`
Expected: FAIL —— `--phase report` 不在 choices，子进程退出码 2。

- [ ] **Step 3: 实现 report phase**

`examples/06_superagent_beta.py`：

顶部 import 区加（保持 ruff I001 顺序，放在 `from stableagent.supervisor.store import
SupervisorStore` 之后）：

```python
from stableagent.supervisor.views import beta_report
```

模块 docstring 的用法段补一行：

```python
    python examples/06_superagent_beta.py --phase report --dir .demo-beta
```

在 `_run_phase` 之后、`main` 之前加：

```python
def _report_phase(root: Path) -> int:
    store = SupervisorStore(root / "beta.db", clock=DemoClock())
    try:
        report = beta_report(store)
        print(
            "BETA_REPORT "
            + json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        )
        return 0
    finally:
        store.close()
```

`main` 改为：

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("run", "report"), default="run")
    parser.add_argument("--dir", default=".demo-beta")
    args = parser.parse_args(argv)
    root = Path(args.dir).resolve()
    if args.phase == "run":
        return asyncio.run(_run_phase(root))
    return _report_phase(root)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q --no-cov tests/integration/test_superagent_beta.py`
Expected: PASS（2 passed）。

- [ ] **Step 5: 提交**

```bash
git add examples/06_superagent_beta.py tests/integration/test_superagent_beta.py
git commit -m "feat: add read-only beta report phase"
```

---

### Task 4：真实仓库 Beta runbook（M6-4）

**Files:**
- Create: `docs/guides/supervisor-beta.md`

**Interfaces:**
- Consumes: 示例 06、`python -m stableagent.supervisor.cli`、`beta_report`、
  [迁移指南](supervisor-migration.md)、[控制台指南](supervisor-console.md)。
- Produces: 维护者执行真实 Beta 的操作手册与人工数据记录表；明确真实 GitHub 只读
  客户端尚未实现这一前置缺口。

- [ ] **Step 1: 写 runbook**

创建 `docs/guides/supervisor-beta.md`：

```markdown
# 单仓库 Super-Agent Beta runbook（M6）

## 定位与前置缺口

本 runbook 定义维护者在**专用仓库**上执行受控 Beta 的步骤与门禁。当前仓库只有
`GitHubReadClient` / `GitHubClient` 协议与 fake，**真实 GitHub HTTP 客户端尚未实现**
（P5 账本已记录该遗留）。因此：

- 现在可复现的是离线演练：`examples/06_superagent_beta.py`（见下）。
- 真实仓库试跑必须先接入一个实现 `GitHubReadClient.list_events` 的只读客户端，并把
  写侧 `GitHubClient` 接到真实 API；接入前不得宣称完成真实 Beta。
- 真实试跑、dry-run 数据与人工标注由维护者执行并回填
  [P6 验收账本](../acceptance/p6-supervisor-beta.md)；回填前 P6 出口第 7 项保持未勾。

## 离线演练（现在即可跑）

```bash
python examples/06_superagent_beta.py --phase run --dir .demo-beta
python examples/06_superagent_beta.py --phase report --dir .demo-beta
```

`run` 末行 `BETA_RESULT tickets=3 resumed=1 external_comments=1 duplicate_tickets=0
bot_triggers=0`；`report` 末行 `BETA_REPORT <json>`。演练覆盖多 Issue/PR/CI Trigger、
两个并发 Ticket、一个 FAILED、一个 WAITING、一个 COMPLETED 后经 operator resume 恢复，
并断言同 Thread 不重复建票、外部评论恰好一次、bot 自事件被过滤。

## 安全边界（不可协商）

- 单仓库、专用 bot 账号；`actors` allowlist 只放维护者；`operators` 只放维护者。
- `mode=observe`：推断候选只记录、不执行；显式请求才建 Ticket。
- 不启用 auto merge；`grantable_actions` 排除 `enable_auto_merge`。
- 最小 GitHub token：只读阶段只需 repo 读权限；写阶段才加 issue comment / PR 创建权限。
- token 只留在客户端 headers，不进 prompt/日志/Report/effect payload（ADR-05 安全声明 5）。
- 所有外部消息先草拟成 `COMMENT_ISSUE` 审批，人工批准才发；拒绝即静默。

## 阶段一：record-only / dry-run

1. 备份状态库：`cp state.db state.db.bak-$(date +%Y%m%d)`。
2. 配置 `console.json`（`extra="forbid"`，无 token 字段）：

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

3. 以 `observe` 启动 Runtime，只读轮询，不派发任何写 effect；观察 Source 健康与 cursor。
4. 用只读命令巡检：

   ```bash
   python -m stableagent.supervisor.cli --db state.db --config console.json --operator alice status
   ```

5. 每日导出 Beta 报告并归档：

   ```bash
   python examples/06_superagent_beta.py --phase report --dir .demo-beta
   ```

   真实库的报告用同一 `beta_report(store)` 读取（示例 report phase 读 `.demo-beta/beta.db`；
   真实库可写一段只读脚本调用 `beta_report`，不得直接读 `_conn`）。

## 阶段二：explicit 请求创建 PR

1. 确认阶段一数据无误报泛滥、通知数克制后，才允许显式请求触发写链路。
2. 写命令一律带 `--confirm`，operator 门禁先于确认：

   ```bash
   python -m stableagent.supervisor.cli --db state.db --config console.json --operator alice \
     approve <approval-id> --confirm <approval-id>
   ```

3. PR 创建仍走 P5 审批门与 worktree 复检；不开放 auto merge。
4. 每个 Ticket 的确认/结果/attention/失败通知各最多一条；普通进度与 CI 更新静默。

## 数据记录表（维护者人工填写）

库可推导的字段由 `BetaReport` 给出；下表只记库无法推导的人工标注。

| 日期 | 候选数 | would-accept | 人工接受 | 人工拒绝 | 拒绝原因 | 误报 | 漏报 | 每 Ticket 通知数 | 恢复次数 | PR 重复数 |
|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | |

- `候选数 / would-accept / 每 Ticket 通知数 / 恢复次数` 同时抄录 `BetaReport` 的
  `decisions`、`would_accept`、`notification_counts`、`recovered_assignments` 以核对。
- **PR 重复数必须为 0**；任何非 0 立即停止写阶段并回滚。
- 误报/漏报与接受/拒绝原因只能人工标注，不从库推导。

## 回滚与降级

1. 停止 Runtime（优雅停止，不取消 Worker）：等待宽限期后释放运行锁。
2. 暂停 intake：`pause --confirm pause`；必要时 `stop <ticket-id> --confirm <ticket-id>`。
3. 恢复备份：`cp state.db.bak-<date> state.db`（先确认无进程持锁）。
4. 降级到 P5：P6 只新增表与索引，P5 二进制可直接打开 P6 库读旧表；步骤见
   [supervisor 迁移指南](supervisor-migration.md)。
5. 撤销 bot token，归档状态库与本报告。

## 发布门禁

- `observe` 数据是 P7 允许任何规则进入 `auto` 的唯一依据（ADR-06 决策 11）。
- 真实 Beta 数据回填账本前，P6 出口第 7 项不勾；其余出口项由离线门禁证明。
```

- [ ] **Step 2: 校验链接与格式**

Run: `.venv/bin/ruff format --check docs 2>/dev/null; ls docs/guides/supervisor-beta.md`
Expected: 文件存在；runbook 内相对链接 `supervisor-migration.md` /
`supervisor-console.md` / `../acceptance/p6-supervisor-beta.md` 均指向既有文件。

- [ ] **Step 3: 提交**

```bash
git add docs/guides/supervisor-beta.md
git commit -m "docs: add single-repository beta runbook"
```

---

### Task 5：ADR-06 门禁决策与状态面收口（M6-5）

**Files:**
- Modify: `docs/design-decisions/06-super-agent-router.md`
- Modify: `docs/acceptance/p6-supervisor-beta.md`
- Modify: `README.md`、`docs/README.md`、`docs/acceptance/README.md`、`docs/Roadmap.md`、
  `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1~4 的交付事实（不预填实测读数）。
- Produces: ADR-06 决策 11；账本 §1 M6 收口行与 M6-1~M6-6 条目；P6 退出清单勾选
  1~6、8，第 7 项保持未勾；状态面同步到 M6 完成。

- [ ] **Step 1: ADR-06 增补决策 11**

`docs/design-decisions/06-super-agent-router.md` 在 `### 10. 迁移与兼容` 段之后、
`## 被否决方案` 之前插入：

```markdown
### 11. Beta 演练与发布门禁：离线先行，observe 数据是 auto 的唯一依据

- M6 交付可复现的离线演练（`examples/06_superagent_beta.py`）与只读 `BetaReport`
  （`views.beta_report` ← `store.beta_snapshot`）；真实 GitHub HTTP 客户端不在 M6 实现，
  真实仓库 Beta 由维护者按 [Beta runbook](../guides/supervisor-beta.md) 执行并回填账本。
- `BetaReport` 字段固定：triggers/decisions/decision_reasons/would_accept/tickets/
  recovered_assignments/approvals/notification_counts/effects/retried_effects/source_health；
  只读、不返回 payload。误报/漏报与人工接受/拒绝原因只进 runbook 人工记录表。
- 发布门禁：`observe` 数据是 P7 允许任何规则进入 `auto` 的唯一依据；PR 重复数必须为 0；
  真实 Beta 数据回填前 P6 出口第 7 项保持未勾。
```

- [ ] **Step 2: 账本 M6 收口与退出清单**

`docs/acceptance/p6-supervisor-beta.md`：

- §1 的 `### M6：Beta 演练与发布门禁（规格 WP-6.6；待 M5 收口后展开）` 改为收口状态行
  （仿 M5：`> **状态：M6 收口（Task 1~6 全部完成）。** ...`），并补
  `- [x] **M6-1**` ~ `- [x] **M6-6**` 六条，每条写对应提交与实测用例数（提交哈希与
  用例数在执行后回填，不预填）。
- §2 退出清单：第 1~6 项与第 8 项勾 `[x]`；第 7 项保持 `[ ]`，并在该项后追加
  `（真实 Beta 数据由维护者按 runbook 回填前不勾，见 §13 决议 1）`。
- §3 阶段验证命令追加一行：

```bash
.venv/bin/pytest -q tests/integration/test_superagent_beta.py
```

- §4 计数追加 `M6 展开为 **6 个 Task / 27 个 Step**`。

- [ ] **Step 3: 状态面同步**

```markdown
| P6 | Single-repository Super-Agent Beta | 🟡 active — M6 done (6/6); milestones 6/6 |
```

- `README.md` 第 369 行 P6 状态行改为上表口径。
- `docs/README.md` 第 52 行改为 `P6 进行中（M6 完成，6/6；里程碑 6/6）`；第 10 行
  指南索引追加 `[Beta runbook](guides/supervisor-beta.md)`。
- `docs/acceptance/README.md` 第 12 行改为 `Active（M6 完成 6/6；里程碑 6/6）`。
- `docs/Roadmap.md` "当前进度" 段改为 M1~M6 完成，`下一步：P7 多 Provider 与平台扩展`
  口径，并链接 `guides/supervisor-beta.md`；保留"真实 Beta 数据待维护者回填"说明。
- `CHANGELOG.md` 在 `## [Unreleased]` 下新增 `### 实现（P6 M6 Beta 演练，2026-11-10）`，
  写离线演练示例 06、只读 BetaReport、Beta runbook、ADR-06 决策 11 与门禁读数（读数实测后回填）。

- [ ] **Step 4: 提交**

```bash
git add docs/design-decisions/06-super-agent-router.md docs/acceptance/p6-supervisor-beta.md \
  README.md docs/README.md docs/acceptance/README.md docs/Roadmap.md CHANGELOG.md
git commit -m "docs: close p6 m6 beta gate and status"
```

---

### Task 6：全量门禁、三解释器与出口回填（M6-6）

**Files:**
- Modify: `docs/acceptance/p6-supervisor-beta.md`（§13 末尾出口读数回填）

**Interfaces:**
- Consumes: Task 1~5 全部交付。
- Produces: §13 末尾 `### M6 出口读数` 实测回填；最终提交。

- [ ] **Step 1: 全量门禁（落盘 /tmp 后读回）**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m6-full.txt 2>&1
tail -5 /tmp/p6-m6-full.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff check stableagent tests examples benchmarks \
  > /tmp/p6-m6-ruff.txt 2>&1; cat /tmp/p6-m6-ruff.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff format --check stableagent tests examples benchmarks \
  > /tmp/p6-m6-format.txt 2>&1; tail -3 /tmp/p6-m6-format.txt
env -u NO_PROXY -u no_proxy .venv/bin/mypy > /tmp/p6-m6-mypy.txt 2>&1
tail -3 /tmp/p6-m6-mypy.txt
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m6-l1.txt; cat /tmp/p6-m6-l1.txt
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py \
  > /tmp/p6-m6-c1.txt; cat /tmp/p6-m6-c1.txt
grep -rn "stableagent.supervisor" stableagent/core stableagent/orchestration \
  stableagent/control stableagent/execution stableagent/integrations \
  > /tmp/p6-m6-reverse.txt; cat /tmp/p6-m6-reverse.txt
git diff --check > /tmp/p6-m6-diffcheck.txt; cat /tmp/p6-m6-diffcheck.txt
```

Expected：全量全绿；`store.py`/`views.py` 行覆盖率仍 ≥90%；L1/C1/reverse/diffcheck
四个文件均为空。

- [ ] **Step 2: 三解释器与心脏覆盖率**

```bash
env -u NO_PROXY -u no_proxy .matrix/py311/bin/pytest -q > /tmp/p6-m6-py311.txt 2>&1
tail -3 /tmp/p6-m6-py311.txt
env -u NO_PROXY -u no_proxy .matrix/py312/bin/pytest -q > /tmp/p6-m6-py312.txt 2>&1
tail -3 /tmp/p6-m6-py312.txt
grep -E "supervisor/(store|views)\.py" /tmp/p6-m6-full.txt
.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py
```

Expected：3.11/3.12 与 3.10 同为 `passed, skipped`；`store.py`/`views.py` ≥90%；
FOCUS 仍 35 文件、`1 passed`。

- [ ] **Step 3: 示例两 phase 实跑**

```bash
.venv/bin/python examples/06_superagent_beta.py --phase run --dir /tmp/p6-m6-demo \
  > /tmp/p6-m6-run.txt 2>&1; tail -2 /tmp/p6-m6-run.txt
.venv/bin/python examples/06_superagent_beta.py --phase report --dir /tmp/p6-m6-demo \
  > /tmp/p6-m6-report.txt 2>&1; tail -1 /tmp/p6-m6-report.txt
```

Expected：run 末行 `BETA_RESULT tickets=3 resumed=1 external_comments=1
duplicate_tickets=0 bot_triggers=0`；report 末行 `BETA_REPORT {...}` 且
`tickets` 为 `{"failed":1,"ready":1,"waiting":1}`。

- [ ] **Step 4: 回填出口读数与提交**

在 §13 末尾追加 `### M6 自检记录（writing-plans）`（已在计划编写时落盘）之后的
`### M6 出口读数（2026-11-10，落盘 /tmp/p6-m6-*.txt 后读回）`，按 M5 格式记录全量/
三解释器/覆盖率/静态/边界四零/示例两 phase/计划偏差，全部以读回文件为准。核对 §4
的 Task/Step 计数与实际一致。

```bash
git add docs/acceptance/p6-supervisor-beta.md
git commit -m "docs: backfill p6 m6 exit readings"
```

---

### M6 自检记录（writing-plans）

- **规格覆盖：** WP-6.6 第 1 条离线集成场景 → Task 2/3；第 2 条受控真实 Beta → Task 4
  runbook（真实客户端缺口显式声明）；第 3 条 Beta 数据 → Task 1 BetaReport + Task 4
  人工记录表；第 4 条 ADR-06 记录 → Task 5 决策 11。设计决议 1（离线先行）→ Task 2/4/5；
  决议 2（只读报告字段）→ Task 1；决议 3（原定场景）→ Task 2；决议 4（runbook 与门禁）
  → Task 4/5。
- **类型一致性：** `BetaSnapshot`/`beta_snapshot` 只在 Task 1 定义，`BetaReport`/
  `beta_report` 只在 Task 1 定义；Task 2 的示例不 import `beta_report`（Task 3 才接），
  Task 3 复用 Task 1 的 `beta_report`；`ScriptedExecutor`/`_build`/`_drain` 只在 Task 2
  定义，Task 3 只追加 `_report_phase` 与 main 分支。`BETA_RESULT`/`BETA_REPORT` 末行
  约定在 Task 2/3 一致。
- **无占位符：** 每个实现步骤给出完整代码；提交哈希、用例总数与出口读数在实测后回填，
  不预填。§1 M6 条目与 CHANGELOG 读数明确标注"执行后回填"。
- **边界：** `beta_snapshot()` 只读 SELECT、不开写事务（L3）；`BetaReport` 不返回
  payload（L4，测试用 `DO-NOT-LEAK` 钉住）；示例不读凭据、配置无 token（L7）；全部
  用例离线（L8）；不新增 `EffectKind`（L6）；`store.py`/`views.py` 已在 FOCUS 35 内，
  不新增心脏文件；`orchestration/service.py` 与 `core/` 零改动（L1/约束 1）。
- **时序裁定：** 脚本化 Worker 的 launch 由 Scheduler 在 tick 内 `drive` 成 asyncio
  task，`_drain` 每轮 `step()` 后 `await asyncio.sleep(0)` 让无内部 await 的 launch
  跑完，再下一轮草拟通知；固定 rounds=4 收敛到 READY/FAILED/WAITING，避免并发时序
  断言脆弱。WAITING Ticket 的 Assignment 保持 RUNNING（Worker 驻留语义），但 asyncio
  task 已结束，`runtime.stop()` 的 `wait_idle` 不阻塞。

### M6 出口读数（2026-11-10，落盘 `/tmp/p6-m6-*.txt` 后读回）

- **全量：** `/tmp/p6-m6-full.txt` → `878 passed, 6 skipped in 21.00s`；总覆盖率
  `98.54%`（门槛 70%）。
- **三解释器：** 3.10 `878 passed, 6 skipped`；`/tmp/p6-m6-py311.txt` 与
  `/tmp/p6-m6-py312.txt` 同为 `878 passed, 6 skipped`，总覆盖率均 `98.54%`。
- **心脏覆盖率：** `supervisor/store.py` `100%`（184 行、0 行未覆盖）、
  `supervisor/views.py` `95%`（178 行、9 行未覆盖），均 ≥90%；FOCUS 35 契约
  `1 passed`（`/tmp/p6-m6-focus.txt`，无新增心脏文件）。
- **静态：** `/tmp/p6-m6-ruff.txt` → `All checks passed!`；
  `/tmp/p6-m6-format.txt` → `162 files already formatted`；
  `/tmp/p6-m6-mypy.txt` → `Success: no issues found in 72 source files`。
- **边界四零：** `/tmp/p6-m6-l1.txt`、`/tmp/p6-m6-c1.txt`、`/tmp/p6-m6-reverse.txt`、
  `/tmp/p6-m6-diffcheck.txt` 均为 0 字节（收口提交 `be6b86b` 后对最终 HEAD 复核）——
  `stableagent/core/` 与 `orchestration/service.py` 对 `234f620` 零 diff，既有包无
  反向 import。
- **示例两 phase：** `/tmp/p6-m6-run.txt` 末行 `BETA_RESULT tickets=3 resumed=1
  external_comments=1 duplicate_tickets=0 bot_triggers=0`；`/tmp/p6-m6-report.txt`
  末行 `BETA_REPORT {...}`，`tickets={"failed":1,"ready":1,"waiting":1}`、
  `would_accept=1`、`recovered_assignments=1`、`retried_effects=0`、
  `effects["github_comment:succeeded"]=1`、`approvals["comment_issue:approved"]=1`、
  `notification_counts` 三 Ticket 各 2（合计 6）。
- **计划偏差：** 6 个 Task / 27 个 Step 全部完成。门禁提前到 Task 5 提交前运行，
  以便 CHANGELOG/docs/README 用实测读数回填（代码树与最终 HEAD 一致，仅文档差异）；
  §4 计数在计划提交时由误写的 24 校正为 27；Roadmap 下一步措辞对齐 P7 实际标题
  "生产化与生态扩展"；另有 `recovered_assignments` 单行折行与两处文件末尾换行的
  格式修正，均不改变行为。其余与计划一致。
