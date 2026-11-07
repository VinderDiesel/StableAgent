# ADR-05：受控执行与仓库集成（P5）

- 状态：Accepted（2026-11-03 经维护者确认）
- 日期：2026-11-03（草案与确认同日）
- 关联：[公共契约](00-contracts.md)、[ADR-02 Checkpoint](02-checkpoint.md)、[ADR-03 Context](03-context-engineering.md)、[ADR-04 Ticket Orchestration](04-ticket-orchestration.md)、[P5 实施规格](../specs/p5-guarded-execution-repository-integration.md)、[P5 验收账本](../acceptance/p5-guarded-execution.md)
- 代码入口：`stableagent/control/`、`stableagent/execution/`、`stableagent/integrations/`（本 ADR 批准的三个新包）

## 背景

P4 收口后（DoD 60/60、run 41596839282），系统能在单进程内持久化地编排 Ticket 与
Assignment，但 Worker 只能跑离线脚本 Provider，没有任何真实文件系统、命令执行或外部
写入。P5 要在**不改变 P4 调度语义**的前提下补三层能力：结构化授权（Policy/Mandate/
Approval）、隔离执行（worktree + 受限命令 + 可选 Docker）、显式 GitHub 闭环（请求 →
Ticket → PR/评论回写）。规格第 2 节给了模块形状，第 3 节给了 6 个工作包，但若干实现
口径留白；本 ADR 逐条定死，作为 [P5 验收账本](../acceptance/p5-guarded-execution.md) 的
决策来源。

## 约束

1. **P4 语义不变**：`orchestration.Scheduler` / `Store` 既有表语义 / `Effect` 协议 /
   `OrchestrationService` 十命令不得因 P5 改签名或改行为；P5 的执行入口是
   `WorkerExecutor` 协议的另一个实现。
2. **Core 零扩展**：P4 已用掉"唯一被批准的 Core 扩展"（`aresume(extra_input)`）。P5
   期间 `stableagent/core/` 必须零 diff；授权、审批、worktree 一律在 core 之外实施。
3. **离线门禁**：CI 与全量测试不得依赖 Docker、网络或真实 GitHub（沿用 L8 口径）。
4. **安全叙事纪律**：不得把 worktree 或本地受限模式宣传为安全沙箱；Docker 缺失时
   安全边界显式缺席，而不是假装存在。

## 决策

### 1. 包边界与依赖方向：三个新包，单向依赖，不顶层重导出

```text
integrations → execution → control → orchestration → core
```

- `control/`（policy/approval/audit）、`execution/`（worktree/command/sandbox/
  environment）、`integrations/`（github/mcp）各自独立成包；`orchestration`、`core`、
  `context`、`tools` 四个既有包**禁止** import 任何新包（`test_orchestration_boundaries.py`
  的 AST 扫描清单扩展为三新包 × 四旧包全部方向断言）。
- 持久化事实源只有两处：P4 的 `mandates` 表（决策 2）与新表 `approval_requests`、
  `authorization_audit`（决策 3），都归 `orchestration.Store` 的同一 SQLite 文件与同一
  `transaction()` 入口——授权数据与 Ticket 数据同库，恢复时不出现两库交错。
- `control/__init__.py` 定义 `CONTROL_SCHEMA_VERSION = "0.1.0"`，与 P4 同样**不进顶层
  重导出**；`execution/` 与 `integrations/` 无持久形态，不设 schema 版本常量。

### 2. Mandate：在原表兼容加列，旧行不迁移语义

- P4 的 `Mandate` 只有 6 个字段（`policy_version="p4-local-v1"`）。P5 在同一张
  `mandates` 表**兼容加列**（先例：P4 Task 12 的加列迁移）共 11 列：`repository`、
  `base_ref`、`allowed_paths`、`allowed_commands`、`allowed_mcp`、`network_profile`、
  `expires_at`、`requires_approval`、`request_origin`、`request_digest`（后两列存 GitHub
  请求的 URL/source ID 与不可变文本摘要）与 `actions`（授权的 action 集合，JSON 数组）。
- 旧行读出时新字段取默认值（`= None` / 空 allowlist，语义即"P4 本地 Mandate 无外部
  授权"），**不回写、不重写** `policy_version`；新建的 P5 Mandate 一律
  `policy_version="p5-v1"`。
- 规则落地为代码而非文档：**只有 `control.policy` 模块构造 P5 Mandate**。P4 的
  `OrchestrationService.create_ticket` 保持原状（本地 Mandate 路径，行为与 P4 逐字节
  兼容）；P5 显式请求走 `control` 自己的入口，在同一 `Store.transaction()` 里原子写
  Mandate + Ticket + 初始 Input + Assignment + launch effect（规格 WP-5.5 第 2 条）。

### 3. Approval 与审计：同库两新表，写入过事务不变式

- `approval_requests`（id、ticket_id、assignment_id、action、resource_digest、reason、
  requested_at、status、decided_by、decided_at）与 `authorization_audit`（每一次
  admit/authorize/revalidate/approve/deny 一行，含 `policy_version` 快照）经
  `schema_migrations` 追加版本 `p5-control-v1` 建表。
- **"事务体内禁止 await"**（ADR-04 决策 3）同样约束新表：授权判定与审批状态变更都在
  同步段落库，GitHub/命令 I/O 一律在提交后进行。
- `approved` 绑定 `(action, resource_digest)`；digest 由参数规范化 JSON 排序后哈希得到，
  参数或资源任一变化即旧批准失效（判定为纯函数，可单测）。
- 审批接口：`ApprovalService.decide(approval_id, approve|deny, by)` 供 CLI/API 调用；
  无面板（P6）。取消 Ticket 时同事务撤销其 pending approval。

### 4. Policy 判定位置：执行层 Gate，不进 Scheduler、不进 Core

- `RepositoryWorkerExecutor` 实现 P4 的 `WorkerExecutor` 协议，包住 `AgentExecutor`：
  launch/recover/deliver 前先 `policy.revalidate()`，失败即 park 该 Assignment 不启动
  Worker（复用 ADR-04"提交前不启动 Worker"不变式，规格第 2 节流程）。
- Worker 侧的工具准入由 `PolicyToolFilter` 实现：不在 Mandate allowlist 的工具**不出现在
  capability view**（P3 的 `tool_selector` 接缝），调用它得到拒绝 + 审计行。这保证授权
  在 Agent 视野之外仍然成立——即使 prompt 幻觉出一个工具名。
- `ControlConfig.permission_hook` / `require_approval_tools` 维持现状：它们目前没有任何
  消费方，是声明字段而非执行边界；P5 不接线、不删除（删 Core 也违反零 diff），文档
  明确它们**不是安全机制**。
- 六个 revalidate 时机固定并逐个有审计行：Assignment launch、recover、retry、
  prepare_worktree 前、push/create PR/comment 前、`call_mcp_tool` 前。

### 5. EffectKind 扩展：加四类，外部写全部走 outbox

- P4 的 `EffectKind` 从 3 类扩为 7 类：`PREPARE_WORKTREE`、`GITHUB_COMMENT`、
  `GITHUB_PULL_REQUEST`、`GITHUB_REQUEST_REVIEW`。`effects.kind` 是无 CHECK 约束的
  TEXT（store.py:81），加枚举成员是非破坏性扩展；幂等键沿用 `UNIQUE(kind,
  idempotency_key)`，GitHub 写的 key 取"外部稳定 id + action/resource digest"。
- **merge 与 enable_auto_merge 不进入 EffectKind**：枚举里存在即可能被历史脏行驱动，
  默认拒绝在 Policy 层表达（Decision 4 的 action 集合里没有它们，admit 时拒绝并审计）。
- `prepare_worktree` 作为 effect 驱动（规格 2.3）：崩溃恢复后重复 prepare 必须命中
  同一 worktree（幂等复用，见决策 6）。

### 6. worktree / 命令 / 环境的硬口径

- 分支名与 worktree 目录名 = `sa-<ticket短id>-<slug>`，slug 只允许 `[a-z0-9-]`，由
  Ticket title 规范化生成；request 文本、Issue 标题原文**永不**直接进入路径、分支或
  命令参数。
- 复用判定四要素全等才复用：仓库 remote、Ticket metadata、base ref、ownership 标记
  （`.stableagent-worktree.json`）；任一不符 → 拒绝复用并保留诊断，不自动清理。
- Ticket 结束默认保留 worktree；清理由显式 `execution.worktree.cleanup()` 加人工确认
  参数完成，P5 不做任何自动删除。
- `CommandRunner`：参数数组执行、禁 `shell=True` 与任何 shell 语法字符、cwd 锁在
  worktree、命令名精确匹配 allowlist、超时与输出上限必有、stdout/stderr 脱敏后入
  Report。环境变量白名单：`STABLEAGENT_TICKET_ID`、`ASSIGNMENT_ID`、`WORKTREE`、
  仓库 URL/分支，其余宿主环境一律不注入。

### 7. Sandbox 分层叙事：LocalRestricted 不是沙箱，Docker 才是边界

- `LocalRestrictedRunner` 的类文档、拒绝消息与 Report summary 禁用"sandbox"一词，
  统一写 `local-restricted`；规格 §1 安全层次表的口径原样进 README。
- `DockerRunner`：默认 `--network none`、非特权、无 docker socket 挂载、CPU/内存/pid
  上限、只挂载本 worktree、镜像允许配置且文档给出 digest pin 指南；网络只有
  Mandate `network_profile` 显式开 allowlist 才允许。
- 全部 Docker 测试在 `docker` marker + `STABLEAGENT_DOCKER=1` 双条件下才运行；默认
  跳过并在 pytest 输出可见原因。核心门禁（CI）不受 Docker 可用性影响。

### 8. MCP：stdio 先行，发现 ≠ 授权，输出不绕预算

- 第一版只做 stdio JSON-RPC：`McpTransport`（参数数组启动、最小环境、超时、关闭）、
  `McpSession`（initialize / tools/list / tools/call + JSON-RPC id 校验）、
  `McpToolAdapter`（映射为既有 `Tool`）。HTTP/SSE transport 留 P7，接口命名不阻塞。
- `tools/list` 的结果只进诊断与审计，**不进** Agent 的 capability view；Agent 可见的
  MCP 工具 = `tools/list ∩ Mandate.allowed_mcp`（决策 4 的 `PolicyToolFilter` 同一处
  执行，工具发现与授权分离在代码上是同一个集合交集）。
- MCP 输出按 `ToolResult` → Evidence → ContextEngine 正常链路走，超长结果受 P3 压缩
  预算约束，不得出现绕过 token budget 的快捷路径。
- `integrations/mcp.py` 不 import `orchestration` 以外的执行/授权层——它只产出 `Tool`。

### 9. GitHub 闭环：显式请求 only，幂等 only

- `ExplicitGitHubRequest` 由 API/CLI 提交（含 repository、URL、actor、文本、是否直接
  `@agent`、接收时间、原始稳定 ID）；P5 不监听 webhook/轮询（P6）。
- 准入四道门顺序固定：actor 在 allowlist → 明确 mention → 单仓库匹配 → 重复检测
  （同稳定 ID 返回同 Ticket 或明确 duplicate 结果）。每道门产 `Admission` + 审计行。
- `GitHubClient` 是注入式协议，单测全 fake；错误按可重试/不可重试/需人工三类归一化
  （不复用 P2 Provider 分类原样搬运），GitHub 状态码保留进 Report details。
- 真实仓库 smoke test 必须显式 opt-in 环境变量，不进 CI。

### 10. 版本与兼容

- `CONTRACT_VERSION` 保持 `1.1.0`：P5 全部是非破坏性新增（新包、新表、EffectKind 加
  成员、Mandate 加默认列），无任何既有签名变化。
- 编排库 `ORCHESTRATION_SCHEMA_VERSION` 不动；`control` 的建表走 `schema_migrations`
  新行 `p5-control-v1`，`CONTROL_SCHEMA_VERSION = "0.1.0"` 预发布口径与 P4 相同。

## 被否决方案

| 方案 | 否决原因 |
|---|---|
| 把 Policy 判定接进 `core/loop.py` 的工具执行点 | 违反约束 2（Core 零 diff）；且 loop 不感知 Mandate，注入即扩展 Core 语义 |
| P5 授权数据独立于 P4 SQLite 另开一个库 | 授权行与 Ticket 行必须同事务写（决策 2 的原子创建），两库做不到"提交前不启动 Worker" |
| 把 `permission_hook` 实装为执行边界 | 它无消费方、语义是自由字符串；实装等于变相 Core 扩展，且给使用者"已有安全边界"的错觉 |
| 以 worktree 充当沙箱（"目录隔离=安全"） | 规格 §1 明令禁止该叙事；目录隔离不约束网络、资源与恶意代码 |
| GitHub 集成先做 webhook 监听 | 引入常驻 Source/Router（P6 非目标），且审批闭环没建好前事件流会绕过 Policy |
| MCP 首版做 HTTP/SSE transport | 依赖网络、CI 不可离线；stdio 已能验证协议核心与授权接缝 |
| ApprovalRequest 只存内存、进程重启即失效 | "需要审批的动作不得先执行后补记录"（规格 2.2）要求批准在崩溃恢复后仍然可判定 |
| `merge`/`enable_auto_merge` 进 EffectKind 但默认拒绝 | 枚举存在即历史脏行可驱动；不进枚举才是结构性拒绝 |

## 后果与代价

- 边界测试从"1 个方向 4 个包"扩为"3 新包 × 4 旧包"矩阵，AST 扫描略慢（可忽略）。
- `mandates` 表加 10 列后 P4 的 `Mandate` 读出分支需要缺省处理，模型多一层
  `policy_version` 判别；换来的是旧库文件零重写直接可用。
- `RepositoryWorkerExecutor` 成为 P5 最重的组合件（revalidate + park + effect 驱动 +
  工具过滤），复杂度集中在一个可完整单测的类里，换 Scheduler 与 Core 的继续零改动。
- Docker 测试在大多数机器上跳过：DockerRunner 的行为契约靠参数构造断言保证，真实
  边界验证只能在有 Docker 的机器上做显式 opt-in。

## 迁移与兼容

- 旧 P4 库：启动时 `schema_migrations` 补 `p5-control-v1` 建两表 + `mandates` 加列
  （`ALTER TABLE ... ADD COLUMN` 全部带默认值）；旧行 `policy_version` 仍为
  `p4-local-v1`，P5 的 `RepositoryWorkerExecutor` 遇到它直接拒绝（本地 Mandate 无外部
  授权面），P4 本地路径行为不变。
- `EffectKind` 三个新外部写成员对旧 outbox 无影响；旧行 kind 值仍只有 3 类。
- 公开 API：三个新包不进顶层 `__init__` 重导出（沿用 ADR-04 决策 1 的口径）。

## 测试与证据（实测，2026-11-07）

全仓 `677 passed, 6 skipped, 0 failed`，行覆盖率 99.04%；6 条 skip 均为 Docker / Redis
显式 opt-in 用例（`STABLEAGENT_DOCKER`、`STABLEAGENT_REDIS_URL` 未设置），不是失败。下述
用例全部离线确定性（注入 ID/clock、本地裸 git 远端、`FakeGitHubClient`、无公网/真实 GitHub），
账本条目以 `A1`–`L8` 引用。P0–P4 的既有用例同场全跑，零回归。

- 心脏覆盖率闸门：`.github/workflows/ci.yml` 的 FOCUS 为 **25 个文件**（P0–P4 的 18 个 +
  `control/{policy,approval}.py`、`execution/{worktree,command,sandbox}.py`、
  `integrations/{github,mcp}.py`），每个 ≥90%；`tests/unit/test_ci_focus_gate.py`（1）锁死
  路径清单、`25 文件` 步骤名与"不许 `.py` 通配兜底"。本地同源复现（CI 内联脚本原样执行）
  的逐文件读数：

  ```text
  control/approval.py 99.0%      control/policy.py 100.0%     core/errors.py 98.3%
  core/loop.py 96.8%             core/checkpoint/manager.py 100.0%
  core/checkpoint/recovery.py 99.0%  core/checkpoint/storage.py 99.3%
  context/assembly.py 100.0%     context/budget.py 100.0%     context/capability.py 100.0%
  context/compression.py 100.0%  context/engine.py 100.0%     context/tokens.py 100.0%
  execution/command.py 100.0%    execution/sandbox.py 100.0%  execution/worktree.py 100.0%
  integrations/github.py 100.0%  integrations/mcp.py 97.9%
  orchestration/executor.py 100.0%  orchestration/model.py 100.0%
  orchestration/outbox.py 98.7%  orchestration/scheduler.py 100.0%
  orchestration/service.py 98.3% orchestration/store.py 100.0%  tools/evidence.py 100.0%
  ```

  最低项为 `core/loop.py` 96.8%，25/25 通过；`BAD=[]`、`MISSING=[]`（脚本 exit 0）。
- L1 Core 零 diff 与约束 1：`git diff 234f620..HEAD -- stableagent/core/` 与
  `git diff 234f620..HEAD -- stableagent/orchestration/service.py` 均输出 0 行（实测）。
  `tests/unit/test_orchestration_boundaries.py::test_loop_py_is_byte_identical_to_p4_base`
  继续守着同一事实。
- L2 依赖方向：`tests/unit/test_control_boundaries.py` 的
  `test_no_legacy_package_imports_p5_layers`（扫描全部非 P5 包）与
  `test_control_never_imports_execution_or_integrations`（反方向），加上
  `tests/unit/test_orchestration_boundaries.py::test_core_layers_never_import_orchestration`。
- L3 事务禁 await：`tests/unit/test_execution_boundaries.py::test_no_transaction_body_awaits`
  （全 `stableagent/` AST 扫描）配 `test_the_await_scan_finds_a_planted_violation`（扫描器
  非空转），运行时由 `tests/unit/test_outbox.py::test_handler_never_sees_an_open_transaction`
  与 `EffectDispatcher.dispatch_due` 的 `assert not store.in_transaction` 守着。
- L5 六个 revalidate 时机：`tests/unit/test_repository_executor.py`（10）覆盖
  `launch|recover|retry`；`tests/unit/test_repository_executor.py::test_prepare_revalidates_before_the_manager`
  与 `test_expired_mandate_parks_before_prepare` 覆盖 `worktree`；
  `tests/unit/test_github_integration.py::test_write_effects_revalidate_and_check_approval`
  覆盖 `external_write`；`tests/unit/test_policy_tool_filter.py` 的
  `test_guarded_tool_denies_without_calling_the_session` 与
  `test_guarded_tool_allows_and_audits_when_granted` 覆盖 `mcp_call`（审计 `action` 槽为
  `mcp_call`）。
- G1/G2：`tests/integration/test_github_example.py`（2）在子进程里跑
  `examples/05_guarded_github_task.py`——run 面断言远端分支、`fix off-by-one` 提交与
  审计顺序（`revalidate/worktree` → `authorize/write_repository` →
  `authorize/create_pull_request`）；reject 面按序得到 `actor_not_allowlisted`、
  `mention_required`、`command_not_allowed`、`approval_pending`、
  `auto_merge_not_grantable`，外部调用计数全 0，末行 `REJECT_RESULT cases=5 ok=True`。
- 解释器矩阵：`uv venv --python 3.10|3.11|3.12` 三套环境（CPython 3.10.20 / 3.11.16 /
  3.12.14）各自 `677 passed, 6 skipped, 0 failed`、覆盖率 99.04%；三份 skip 清单按行排序后
  `diff` 为空，即跳过的是同一组显式 opt-in 用例。

## 安全声明（不夸大）

1. `local-restricted` 命令执行器**不是安全边界**：它做 argv 精确 allowlist、cwd 收口、
   shell 语法拒绝与输出预算，但不限制进程能读写的系统资源；真正的隔离档是可选 Docker runner。
2. Docker runner 是**可选执行档**：未配置 `STABLEAGENT_DOCKER=1` 时相关用例跳过，核心门禁
   与 P5 的授权链路照常运行，不以"有容器"为前提。
3. worktree 是**变更隔离**，不是沙箱：它隔离分支与工作目录，不约束网络、CPU、内存或恶意代码。
4. MCP 只允许 Mandate 指定的 server/tool：`tools/list` 的 discovery 结果不等于授权，可见集是
   `tools/list ∩ Mandate.allowed_mcp`，每次调用前还有 `mcp_call` 复检。
5. 凭据不进 prompt、日志、Report、Ticket/AgentState、worktree metadata 或 effect payload；
   子进程环境只取 allowlist。真实 GitHub 传输仍需 operator 在进程内注入客户端，fake 不接收 token。
6. 真实 GitHub / Docker smoke 只能显式 opt-in：默认全仓门禁离线，不访问公网、不要求 Docker。
7. `merge` / `enable_auto_merge` 是**结构性缺席**：它们不在 `EffectKind`，也不在可授权集合，
   不是"默认关闭"的开关；构造期、`create_mandate` 与 `authorize` 三处各有拒绝。

## 前置 ADR 遗留项的处置

- P4 交接到 P5 的唯一遗留：`Ticket.mandate_id` 指向的最小本地 Mandate 由本 ADR
  决策 2 升级兼容。ADR-02 的内层 Provider 硬超时已归 P7（WP-7.2），P5 不动。
- 规格 §5 的 P6 交接约束（Source 不得绕过 Policy）在本 ADR 的包边界（决策 1）里已经
  结构性成立：P6 的 Source 只能落在 `integrations/`，而它 import `control` 是单向的。

## 阶段交接

P6 复用件：`AuthorizationPolicy`/`Mandate`（P6 Router 只能经它创建/恢复授权）、
`GitHubClient`、Ticket/PR comment 的 effect handlers、`RepositoryWorkerExecutor`。
P7 候选件：MCP HTTP/SSE transport、Provider 成本路由、凭据管理器。
