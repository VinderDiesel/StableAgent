# P5 验收账本：受控执行与仓库集成

> **状态：Active（2026-11-03 计划经维护者确认，ADR-05 Accepted）。** 本文件把
> [P5 实施规格](../specs/p5-guarded-execution-repository-integration.md) 的 6 个工作包与
> [ADR-05](../design-decisions/05-guarded-execution.md) 的 10 项决策展开为可勾选的验收账本。
> 执行从 M1 任务级计划（第 5 节，开工前追加）开始；当前 **DoD 0/46、里程碑步骤 1/40（M1 1/9）**，
> 分支 `feat/p5-m1-control-plane` 自 2026-11-04 起按 Task 推进。
>
> 使用方式（沿用 P4 账本约定）：本文件既是 P5 的实施计划（第 1 节里程碑步骤），也是 P5 的
> 验收账本（第 0、2 节）。每个可勾选事项写明来源、验证方法或命令、证据占位；全部勾选且第 3 节
> 命令在 CI 复现后 P5 才算收口。推进时把实际输出追加到对应条目，不重写已记录的历史输出。
>
> **计数口径**：DoD = 第 0 节 L1~L8（8 项）+ 第 2 节 A~H（38 项）= 46 项；
> 里程碑步骤 = M1 9 + M2 12 + M3 12 + M4 7 = 40 步（每里程碑最后一步为出口）。
> 任务级 Step 计划（TDD 粒度）在各里程碑开工前追加为独立小节，不计入 40。

## 0. 心脏边界（L1~L8：违反任意一条即 P5 不收口）

- [ ] **L1 Core 零 diff**：`git diff <P4 收口提交 234f620>..HEAD -- stableagent/core/` 恒为
      0 行（P4 已用掉唯一 Core 扩展；P5 不再申请）。验证：出口命令 + CI。
      来源：ADR-05 约束 2。
- [ ] **L2 依赖方向**：`core/context/tools/orchestration` 四包无任何对
      `control/execution/integrations` 的 import；方向只能是
      integrations → execution → control → orchestration → core。
      验证：`test_orchestration_boundaries.py` AST 矩阵扩展。来源：ADR-05 决策 1。
      进度（2026-11-04，Task 1 前半）：`tests/unit/test_control_boundaries.py` 落位（版本常量 +
      顶层不重导出 + 错误家族三用例）；四旧包 × 三新包的 AST 方向矩阵收口属 Task 8（步骤 1.8）。
- [ ] **L3 事务不变式（含新表）**：`Store.transaction()` 内禁止 `await`；授权判定、审批状态、
      Mandate+Ticket+effect 的原子写入全部在同步段完成，外部 I/O 在提交后。
      验证：源码扫描测试 + 崩溃注入用例。来源：ADR-04 决策 3、ADR-05 决策 3。
- [ ] **L4 外部文本不扩权**：Issue/prompt/工具输出/MCP 返回中的任何文本不能创建或扩大
      Mandate；只有 `control.policy` 能构造 `p5-v1` Mandate。
      验证：行为测试（把指令文本放进请求正文，断言授权集不变）+ 构造入口扫描。
      来源：规格 2.1 规则、ADR-05 决策 2。
- [ ] **L5 先判定后执行**：六个 revalidate 时机（launch/recover/retry/worktree/
      push+PR+comment/MCP 调用）逐一有审计行；需审批的动作不得先执行后补记录。
      来源：规格 2.1/2.2、ADR-05 决策 4。
- [ ] **L6 结构性默认拒绝**：`merge`/`enable_auto_merge` 不在 EffectKind 枚举与 Action
      授权集合中（不是"运行时开关拒绝"，是类型里不存在）。来源：ADR-05 决策 5、被否决表。
- [ ] **L7 凭据不落盘**：密钥不进 prompt、日志、Report、Ticket/AgentState、worktree 元数据
      与容器挂载；执行环境只有白名单变量。来源：规格 §1 安全层次、ADR-05 决策 6。
- [ ] **L8 离线门禁**：CI 与全量测试不依赖 Docker/网络/真实 GitHub；Docker 与真实仓库
      用例只能 marker+环境变量双条件下运行。来源：ADR-05 约束 3、决策 7。

## 1. 里程碑与步骤

### M1：结构化授权落地（规格 WP-5.1；ADR-05 决策 1/2/3/10）

- [x] **1.1** `control/` 包骨架：`CONTROL_SCHEMA_VERSION = "0.1.0"` 不进顶层重导出；
      `errors.py` 稳定 `.code` 错误家族。验证：包导入测试 + 顶层 `__init__` 无新增导出。
      证据（2026-11-04，Task 1，提交 `76538e7`）：`stableagent/control/__init__.py` 的 Phase 6 占位
      docstring 换为 P5 控制平面口径（"授权事实源在 Store、判定逻辑在本包、不顶层重导出"）+
      `CONTROL_SCHEMA_VERSION`；`errors.py` = `ControlError(ValueError)` 基类 + 8 个子类
      （`control.admission_rejected`/`unknown_mandate`/`unknown_approval`/`approval_decided`/
      `stale_approval`/`mandate_expired`/`not_granted`/`revalidation_failed`）。
      `pytest -q --no-cov tests/unit/test_control_boundaries.py` = `3 passed`；全量 `pytest -q` =
      `436 passed, 1 skipped`，`stableagent/control/*` 覆盖率 100%（3 + 32 stmts，0 miss）；
      `mypy` = `Success: no issues found in 37 source files`，`ruff check stableagent tests examples
      benchmarks` = `All checks passed!`，`ruff format --check` = `89 files already formatted`；
      L1 `git diff 234f620..HEAD -- stableagent/core/` = 0 行。
      Step 2 实测红因与计划偏差：占位包 `stableagent/control/` 自 2026-10-30 起已在库中，
      红为 `ImportError: cannot import name 'CONTROL_SCHEMA_VERSION'`（非计划预期的
      `ModuleNotFoundError: stableagent.control`）。
- [ ] **1.2** `schema_migrations` 追加 `p5-control-v1`：`mandates` 兼容加 10 列（全带默认值）、
      新建 `approval_requests`、`authorization_audit`。验证：旧 P4 库文件升级后可读、
      旧行语义不变（A1）。
- [ ] **1.3** 模型：`Action`/`Resource`/`Admission`/`Decision`/`ApprovalRequest` +
      `resource_digest` 纯函数（规范化 JSON 排序哈希）。验证：digest 稳定性单测。
- [ ] **1.4** Mandate 读写：`p5-v1` 新字段全量落库与还原；`p4-local-v1` 旧行缺省读取。
      验证：往返单测。来源：ADR-05 决策 2。
- [ ] **1.5** `StaticPolicy`：单仓库 + actor allowlist + 明确 mention + 预定义 action 集合 +
      默认拒绝集（任意 shell、全网访问、未列入的 MCP server/tool 均拒）。验证：判定矩阵单测。
- [ ] **1.6** 审计落库：admit/authorize/revalidate/approve/deny 每决策一行，含
      `policy_version` 快照。验证：决策序列 → 行数与字段断言（A3）。
- [ ] **1.7** `ApprovalService.decide()`：approved 绑定 `(action, resource_digest)`、参数变化
      即失效、denied → blocked/failed 不可绕过、Ticket 取消同事务撤 pending。验证：B1~B4 用例。
- [ ] **1.8** L2 边界测试扩展：三新包 × 四旧包 AST 方向矩阵。验证：故意 import 的红测 + 常驻。
- [ ] **1.9** **M1 出口**：`pytest -q` / `mypy` / `ruff check` / `ruff format --check` /
      L1 diff 0 行；账本 M1 步骤与 A、B 组证据回填。

### M2：隔离执行（规格 WP-5.2 + WP-5.3；ADR-05 决策 5/6/7）

- [ ] **2.1** `execution/` 包骨架 + `GitWorktreeManager`：参数数组调用 git、路径
      `Path.resolve()` 锁 root。验证：路径逃逸拒绝用例（C4）。
- [ ] **2.2** 分支/worktree 命名纯函数：`sa-<ticket短id>-<slug>`，slug 仅 `[a-z0-9-]`；
      request 文本永不直通。验证：命名往返 + 注入文本用例（C5）。
- [ ] **2.3** 复用判定：仓库 remote / Ticket metadata / base ref / ownership 四要素全等复用，
      不一致拒绝并保留诊断。验证：临时 git 仓库逐要素破坏测试（C3）。
- [ ] **2.4** `PREPARE_WORKTREE` EffectKind + outbox 驱动 prepare：重复 effect 幂等命中同一
      worktree。验证：崩溃注入重放（C2）。
- [ ] **2.5** 生命周期：Ticket 结束默认保留；清理需显式调用 + 人工确认参数，无自动删除。
      验证：结束后目录仍在 + cleanup 需 confirm 参数。
- [ ] **2.6** `CommandRunner`：命令名精确 allowlist、cwd 锁 worktree、超时、输出上限、
      截断与脱敏入 Report。验证：C7 全断言。
- [ ] **2.7** `environment.py` 变量白名单（TICKET_ID/ASSIGNMENT_ID/WORKTREE/仓库信息），
      宿主其余环境不注入。验证：子进程可见环境断言（C8、L7）。
- [ ] **2.8** `LocalRestrictedRunner` + 叙事纪律：类文档、拒绝消息、Report summary 禁
      "sandbox" 一词。验证：文案断言测试（D1）。
- [ ] **2.9** shell 拒绝：`shell=True`、shell 语法字符（`;|&$\``）一律拒绝且审计。
      验证：注入语料表（C6）。
- [ ] **2.10** `DockerRunner` 参数装配断言：`--network none`、非特权、无 socket、CPU/内存/pid/
      超时、仅挂本 worktree、镜像可配置 + digest pin 文档。验证：不启动容器、只断言 argv（D2）。
- [ ] **2.11** Docker 测试条件：`docker` marker + `STABLEAGENT_DOCKER=1` 双条件，默认跳过且
      原因可见。验证：无 Docker 环境全量绿（D3、L8）。
- [ ] **2.12** **M2 出口**：全量门禁 + 并行 Ticket 目录互不污染集成用例（C1）+ 账本回填。

### M3：MCP 与 GitHub 闭环（规格 WP-5.4 + WP-5.5；ADR-05 决策 4/8/9）

- [ ] **3.1** `McpTransport`：参数数组启动、最小环境、请求 ID、超时、关闭。
      验证：fake transport 生命周期用例。
- [ ] **3.2** `McpSession`：initialize/tools/list/tools/call + JSON-RPC id 与错误校验。
      验证：E1 六类（error/超时/进程退出/schema 错误）。
- [ ] **3.3** `McpToolAdapter` → 既有 `Tool`；输出走 ToolResult→Evidence→ContextEngine，
      超长受 P3 压缩预算。验证：E4 预算断言。
- [ ] **3.4** 发现 ≠ 授权：Agent 可见集 = `tools/list ∩ Mandate.allowed_mcp`；未列入者不可见、
      不可启动调用。验证：E3 + 离线 stdio fixture（E2）。
- [ ] **3.5** `ExplicitGitHubRequest` 模型 + 准入四道门（allowlist→mention→单仓库→重复检测）；
      同稳定 ID 返回同 Ticket 或明确 duplicate。验证：F1 + 每门审计行。
- [ ] **3.6** `control` 入口事务：Mandate+Ticket+初始 Input+Assignment+launch effect 同一
      `Store.transaction()` 原子写。验证：F2 + 事务内禁 await 扫描（L3）。
- [ ] **3.7** `RepositoryWorkerExecutor`：launch/recover/deliver 前 revalidate，失败 park 不启动；
      遇 `p4-local-v1` Mandate 直接拒绝外部执行面。验证：A4 时机矩阵 + 拒绝用例。
- [ ] **3.8** `PolicyToolFilter` 接 P3 `tool_selector` 接缝：拒绝调用有审计。
      验证：L5 + 幻觉工具名用例。
- [ ] **3.9** `GitHubClient` 协议 + fake 实现；错误可重试/不可重试/需人工三分类，状态码保留。
      验证：F4 分类表驱动测试。
- [ ] **3.10** comment / create PR / request review 三个 effect handler：执行前 revalidate +
      approval 检查；idempotency key = 外部稳定 id + action/resource digest。验证：F3。
- [ ] **3.11** 恢复不重复写：重启/超时/重复 effect 下 PR、评论、push 各仅一次。
      验证：F5 集成用例（fake client 计调用次数）。
- [ ] **3.12** **M3 出口**：全量门禁 + L6 类型检查（枚举无 merge 成员）+ 账本回填。

### M4：端到端与收口（规格 WP-5.6；ADR-05"测试与证据"）

- [ ] **4.1** `examples/05_guarded_github_task.py`：本地 git fixture + fake GitHub 走通
      请求→授权→worktree→修改→PR effect，全离线。验证：示例即测试（G1）。
- [ ] **4.2** 拒绝示例五连：非 allowlist、无 mention、越权命令、无审批 PR 写入、auto merge
      ——均结构化拒绝 + 审计 + 零外部副作用。验证：G2。
- [ ] **4.3** CI FOCUS 扩容：`control/policy.py`、`control/approval.py`、
      `execution/worktree.py`、`execution/command.py`、`execution/sandbox.py`、
      `integrations/github.py`、`integrations/mcp.py` 进心脏闸门 + 契约测试同步。
      验证：本地同源复现 BAD/MISSING 均空。
- [ ] **4.4** 本地 Python 3.10 / 3.11 / 3.12 等价矩阵（沿用 `.matrix/`）。
- [ ] **4.5** ADR-05"测试与证据"实测回填 + 安全说明定稿（不夸大本地受限模式，G4）。
- [ ] **4.6** README / CHANGELOG / Roadmap / docs 索引 / 规格退出清单同步（状态翻转时机沿用
      P4：远端 CI 绿前不写"收口"）。
- [ ] **4.7** **M4 出口**：维护者授权 → 合并推送 `main` → 远端 CI 3.10/3.12 双绿 →
      run ID 回填账本（H3）。

### 1.4 里程碑与 DoD 归属

| 里程碑 | 心脏边界 | DoD 分组 |
|---|---|---|
| M1 | L3、L4、L5（授权侧） | A1~A3、A5、B1~B4 |
| M2 | L7、L8（部分） | C1~C8、D1~D3、A4（worktree 时机接线） |
| M3 | L5、L6、L7（凭据侧） | E1~E4、F1~F6、A4（launch/recover/retry/push/MCP 接线） |
| M4 | L1、L2、L8（收口） | G1~G4、H1~H4 |

> 归属修正（2026-11-03，M1 任务级计划起草时定）：A4 的六个 revalidate **时机接线**分布在
> M2（prepare_worktree）与 M3（其余五处），M1 只交付 `revalidate` 判定与审计本身。

## 2. DoD（A~H：38 项；与 L1~L8 合计 46）

### A 授权与 Mandate 持久化（M1）

- [ ] **A1** 旧 P4 库升级后 `p4-local-v1` Mandate 行缺省可读、语义不变、不回写。
- [ ] **A2** 只有 `control.policy` 能构造 `p5-v1` Mandate（构造入口扫描 + 行为用例）。
- [ ] **A3** admit/authorize/revalidate/approve/deny 全部结构化落审计表，含 policy_version。
- [ ] **A4** 六个 revalidate 时机逐个先于动作执行，时机清单以测试矩阵固化。
- [ ] **A5** 默认拒绝集生效：auto merge、任意 shell、全网访问、未列入 MCP server/tool。

### B 审批（M1）

- [ ] **B1** `approved` 绑定 `(action, resource_digest)`；参数或资源变化后旧批准失效。
- [ ] **B2** `denied` 使 Assignment `blocked|failed`，不得换动作绕过。
- [ ] **B3** Ticket 取消同事务撤销 pending approval。
- [ ] **B4** 需审批动作先记录后执行，且批准在进程重启（崩溃恢复）后仍可判定。

### C worktree 与命令（M2）

- [ ] **C1** 多 Ticket 并行 worktree 目录与分支互不污染（并发集成用例）。
- [ ] **C2** 重复 prepare 幂等复用同一 worktree（含崩溃重放）。
- [ ] **C3** 复用四要素（remote/metadata/base ref/ownership）不一致时拒绝并保留诊断。
- [ ] **C4** 路径逃逸拒绝：resolve 后必须在配置 root 内。
- [ ] **C5** request 文本不直通分支名、路径、命令参数；slug 仅 `[a-z0-9-]`。
- [ ] **C6** `shell=True` 与 shell 语法字符一律拒绝且有审计。
- [ ] **C7** 命令 allowlist、cwd 锁 worktree、超时、输出上限、脱敏五项全部有测试。
- [ ] **C8** 执行环境仅白名单变量；宿主密钥、无关环境变量不注入。

### D Sandbox 分层（M2）

- [ ] **D1** `LocalRestrictedRunner` 的文档、拒绝消息、Report 不称"sandbox"（文案断言）。
- [ ] **D2** `DockerRunner` 默认参数：network none、非特权、无 socket、资源上限、仅挂本
      worktree、镜像配置带 pin 指南（argv 构造断言）。
- [ ] **D3** Docker 不可用时测试跳过、核心门禁不受影响。

### E MCP（M3）

- [ ] **E1** fake transport 覆盖 initialize/list/call/JSON-RPC error/超时/进程退出/schema 错误。
- [ ] **E2** 本地 stdio fixture 验证真实 framing；全程离线。
- [ ] **E3** 发现 ≠ 授权：非 allowlist 的 server/tool 不进 capability view、调用被拒且不启动。
- [ ] **E4** MCP 输出走 ToolResult→Evidence→ContextEngine，超长受压缩预算。

### F GitHub 显式闭环（M3）

- [ ] **F1** 重复显式请求（同稳定 ID）返回同一 Ticket 或明确 duplicate。
- [ ] **F2** Mandate+Ticket+Input+Assignment+launch effect 同一事务原子创建。
- [ ] **F3** comment/PR/request-review 三个写 effect 执行前均验证授权与幂等键。
- [ ] **F4** GitHub 错误三分类（可重试/不可重试/需人工）入 Report，状态码不吞。
- [ ] **F5** 恢复与重试路径不重复 PR、评论、push（fake client 调用计数断言）。
- [ ] **F6** merge/auto-merge 在 EffectKind 与授权集合中结构性不存在。

### G 示例与文档（M4）

- [ ] **G1** 端到端示例离线跑通"请求→授权→worktree→修改→PR effect"，示例即测试。
- [ ] **G2** 五类拒绝示例：非 allowlist、无 mention、越权命令、无审批写、auto merge。
- [ ] **G3** ADR-05"测试与证据"由占位改为实测；安全说明定稿。
- [ ] **G4** README/CHANGELOG/Roadmap/docs 索引与规格退出清单同步，不夸大本地受限模式。

### H 质量与收口（M4 出口）

- [ ] **H1** P0~P4 全量用例零回归；`stableagent/core/` 对 P4 收口提交零 diff（L1 复核）。
- [ ] **H2** 全库门禁绿：`pytest -q`、`mypy`、`ruff check`、`ruff format --check`，数字记账本。
- [ ] **H3** CI FOCUS 扩容生效且远端 CI（3.10/3.12）双绿，run ID 记入本账本。
- [ ] **H4** 覆盖率不低于 P4 基线（全库 99.02%；新心脏文件逐个 ≥90%）。

## 3. 阶段验证命令（收口前须在 CI 复现）

```bash
.venv/bin/pytest -q tests/unit/test_policy.py tests/unit/test_approval.py tests/unit/test_worktree.py
.venv/bin/pytest -q tests/unit/test_command_runner.py tests/unit/test_sandbox.py tests/unit/test_mcp_client.py
.venv/bin/pytest -q tests/unit/test_github_integration.py tests/integration/test_mcp_stdio.py
.venv/bin/pytest -q tests/integration/test_guarded_ticket_recovery.py
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
git diff 234f620..HEAD --stat -- stableagent/core/   # 必须 0 行（L1）
```

Docker/真实 GitHub 用例只能在显式 marker 与环境变量存在时运行（L8）。

## 4. 任务级实施计划（占位）

沿用 P4 约定：每个里程碑开工前，把该里程碑的步骤展开为 TDD 粒度的 Task/Step 追加为独立小节
（M1 → 第 5 节，M2 → 第 6 节，M3 → 第 7 节，M4 → 第 8 节）。任务级 Step 不计入 40 个里程碑步骤。

## 5. M1 任务级实施计划（writing-plans 产出，2026-11-03）

> **状态：执行中（Task 1 已收口，2026-11-04）。** 对应第 1 节 M1 的 9 个步骤（1.1~1.9），展开为
> **9 个 Task / 45 个 Step**。
> 执行分支 `feat/p5-m1-control-plane`（从 `main` 切出）。零语义改动约束沿用：M1 期间
> `git diff 234f620..HEAD -- stableagent/core/` 恒为 0 行（L1）；`orchestration` 包只允许
> **兼容性扩展**（加列迁移、Mandate 加默认字段、Store 加方法），Scheduler/Service/Outbox
> 既有方法签名与行为不动。
>
> 每个 Task 的固定节奏（沿用 P4）：Step 1 写失败测试 → Step 2 跑到红并记录命令与预期失败 →
> Step 3 实现 → Step 4 目标文件跑绿 + 全量 `pytest -q` → Step 5 提交。Step 4/5 前必须
> `mypy`、`ruff check stableagent tests examples benchmarks`、
> `ruff format --check stableagent tests examples benchmarks` 全绿。
> 证据写进第 1 节 M1 对应步骤与第 2 节 DoD 条目，不重写历史输出。

### Task ↔ 里程碑步骤与 DoD 对照

| Task | 步骤 | 本 Task 产生的 DoD 证据 |
|---|---|---|
| 1 | 1.1 | L2（前半：control 边界用例落位） |
| 2 | 1.2 | A1（旧库升级）、L3（迁移在事务内） |
| 3 | 1.3 | B1（digest 纯函数前提） |
| 4 | 1.4 | A1（往返）、A2（p5-v1 字段形状） |
| 5 | 1.5 | A5、L6、L4（行为半边） |
| 6 | 1.6 | A3（审计先于接线） |
| 7 | 1.7 | B1~B4 |
| 8 | 1.8 | L2（收口）、A2（扫描半边） |
| 9 | 1.9 | M1 出口 + 账本回填 |

---

### Task 1: `control/` 包骨架、版本常量与稳定错误家族（步骤 1.1）

**Files:**
- Create: `stableagent/control/__init__.py`、`stableagent/control/errors.py`
- Test: `tests/unit/test_control_boundaries.py`（新建，首批 3 用例）

**Interfaces:**
- Produces: `CONTROL_SCHEMA_VERSION = "0.1.0"`（只在本包定义，顶层 `stableagent/__init__.py`
  不重导出——沿用 ADR-05 决策 1）；`ControlError(ValueError)` 基类（`code = "control.error"`，
  构造风格与 `OrchestrationError` 相同：`super().__init__(f"[{code}] {message}")`、`self.detail`），
  8 个子类与稳定码：
  `AdmissionRejectedError(reason)`→`control.admission_rejected`、
  `UnknownMandateError(mandate_id)`→`control.unknown_mandate`、
  `UnknownApprovalError(approval_id)`→`control.unknown_approval`、
  `ApprovalDecidedError(approval_id, status)`→`control.approval_decided`、
  `StaleApprovalError(approval_id)`→`control.stale_approval`、
  `MandateExpiredError(mandate_id, expires_at, now)`→`control.mandate_expired`、
  `NotGrantedError(action, mandate_id)`→`control.not_granted`、
  `RevalidationFailedError(when, mandate_id)`→`control.revalidation_failed`。

- [x] **Step 1: 写失败的测试**——`tests/unit/test_control_boundaries.py`：
      `test_control_version_is_independent_and_not_reexported`（常量等值 + `"ControlError" not in
      dir(stableagent)`）；`test_control_error_code_inventory_is_unique_and_prefixed`（对照 P4 同名
      用例：全部 `.code` 以 `control.` 开头、tail 为小写 snake_case、互不重复、类集合恰好 =
      基类 + 8 个子类）；`test_control_error_bases_valueerror`。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_control_boundaries.py`
      Expected: FAIL — `ModuleNotFoundError: stableagent.control`
      Actual: FAIL — `ImportError: cannot import name 'CONTROL_SCHEMA_VERSION' from
      'stableagent.control'`（占位包 2026-10-30 起已存在，见步骤 1.1 证据）
- [x] **Step 3: 实现**两个文件（docstring 写明"授权事实源在 Store，判定逻辑在本包"）。
- [x] **Step 4: 跑到绿**：同命令 PASS + `.venv/bin/pytest -q` 全量不红。
      Actual：目标文件 `3 passed`；全量 `436 passed, 1 skipped`；`mypy` / `ruff check` /
      `ruff format --check` 全绿。
- [x] **Step 5: 提交**：`git add stableagent/control/ tests/unit/test_control_boundaries.py`
      `&& git commit -m "feat: open the control package with its own version and stable codes"`
      Actual: `76538e7`（3 files, +147 −1）。

### Task 2: Store 迁移 `p5-control-v1`——加列与两新表（步骤 1.2）

**Files:**
- Modify: `stableagent/orchestration/store.py`（`_SCHEMA` 追加两表、`_migrate()` 加列）
- Test: `tests/unit/test_control_migration.py`

**Interfaces:**
- Produces: `mandates` 加 11 列（全部带默认值，可空或 `[]`）：`repository TEXT`、
  `base_ref TEXT`、`actions TEXT NOT NULL DEFAULT '[]'`、`allowed_paths TEXT NOT NULL
  DEFAULT '[]'`、`allowed_commands TEXT NOT NULL DEFAULT '[]'`、`allowed_mcp TEXT NOT NULL
  DEFAULT '[]'`、`network_profile TEXT`、`expires_at INTEGER`、`requires_approval INTEGER
  NOT NULL DEFAULT 0`、`request_origin TEXT`、`request_digest TEXT`。
  新表 `_SCHEMA["approval_requests"]`：`id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL,
  assignment_id TEXT, action TEXT NOT NULL, resource_digest TEXT NOT NULL, reason TEXT NOT
  NULL, requested_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', decided_by
  TEXT, decided_at INTEGER`；`_SCHEMA["authorization_audit"]`：`id TEXT PRIMARY KEY, kind
  TEXT NOT NULL, ticket_id TEXT, assignment_id TEXT, mandate_id TEXT, action TEXT,
  resource_digest TEXT, policy_version TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT,
  created_at INTEGER NOT NULL`。`_migrate()` 追加
  `INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES ('p5-control-v1', ?)`。
  迁移后 `Store.schema_version()` 返回 `"p5-control-v1"`（`ORDER BY applied_at DESC` 语义，
  既有 P4 测试若断言 `"0.1.0"` 需在 Step 2 变红处确认后**改测试期望为版本行存在**而非返回值）。

- [ ] **Step 1: 写失败的测试**——`tests/unit/test_control_migration.py`：
      `test_legacy_p4_database_upgrades_without_touching_rows`：手工按 P4 原始 DDL 建一个临时库、
      插入 1 条 `p4-local-v1` mandate；`Store(db, clock=FixedClock())` 打开后断言：
      新列全在（PRAGMA table_info）、两新表存在、旧行 `policy_version` 仍为 `p4-local-v1` 且
      新字段读为默认值（A1）；升级前后该行的 6 个原字段值逐等。
      `test_migration_marker_is_idempotent`：重复打开同一库，`schema_migrations` 里
      `p5-control-v1` 恰 1 行。
      `test_migration_runs_inside_transaction`：monkeypatch `Store.transaction` 记录进入次数，
      断言 `_migrate` 期间计数 +1（L3 迁移半边）。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_control_migration.py`
      Expected: FAIL — 旧库打开后 PRAGMA 里没有 `repository` 列（AssertionError）
- [ ] **Step 3: 实现**：`_migrate()` 内 `PRAGMA table_info(mandates)` 取现有列名，逐列
      `ALTER TABLE mandates ADD COLUMN ...`（每列 `if name not in columns` 守卫，照抄
      `deadline_at` 先例）；`_SCHEMA` 补两表 DDL（新建库由 `_create_schema` 直接建齐）；
      追加 `schema_migrations` 标记行。
- [ ] **Step 4: 跑到绿**：目标文件 PASS + 全量 `pytest -q`（重点看 P4 的 store 测试无回归）。
- [ ] **Step 5: 提交**：`git commit -am "feat: migrate the store to p5-control-v1 additively"`

### Task 3: `control/model.py`——Action/Resource/Admission/Decision/ApprovalRequest + digest（步骤 1.3）

**Files:**
- Create: `stableagent/control/model.py`
- Test: `tests/unit/test_control_model.py`

**Interfaces:**
- Produces:
  - `Action(str, Enum)`：规格 2.1 的 13 个成员（`prepare_worktree` … `enable_auto_merge`）。
    **注意**：`ENABLE_AUTO_MERGE` 存在于枚举是为了"点名拒绝"可测（规格原文），但 ADR-05
    决策 5 与 L6 约束的是它**不进入任何可授权集合与 EffectKind**（Task 5 落地）。
  - `Resource(BaseModel)`：`kind: str` + 可选 `path/command/server/tool/url: str | None` +
    `extra: dict[str, Any] = {}`。
  - `Admission(BaseModel)`：`admitted: bool`、`reason_code: str`、`requester: str`、
    `repository: str`、`stable_id: str`、`request_digest: str`。
  - `Decision(BaseModel)`：`allowed: bool`、`reason: str`、`policy_version: str`。
  - `ApprovalStatus(str, Enum)`：`PENDING/APPROVED/DENIED/REVOKED`；`ApprovalRequest`
    与表列一一对应（毫秒整数、ID 注入生成，沿用 P4 记录风格）。
  - `resource_digest(action: Action, resource: Resource) -> str`：
    `sha256(json.dumps({"action": action.value, "resource": resource.model_dump()},
    sort_keys=True, ensure_ascii=False).encode())` 的 hexdigest。

- [ ] **Step 1: 写失败的测试**——关键用例：
      `test_resource_digest_is_stable_under_key_order`（extra 键乱序 → digest 相等）；
      `test_resource_digest_changes_on_action_or_resource`（改 action、改 path、改 extra 值
      各得不同 digest）；`test_action_enum_has_exactly_spec_thirteen_members`；
      `test_approval_request_requires_ticket_and_digest`（缺字段 pydantic ValidationError）。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_control_model.py`
      Expected: FAIL — `ModuleNotFoundError`
- [ ] **Step 3: 实现** `control/model.py`（文件头 docstring 写明"纯数据 + 纯函数，不 import
      orchestration 之外的执行/集成层"）。
- [ ] **Step 4: 跑到绿**：目标文件 + 全量。
- [ ] **Step 5: 提交**：`git commit -am "feat: describe actions, admissions and approvals as data"`

### Task 4: Mandate 兼容扩展与 Store 往返（步骤 1.4）

**Files:**
- Modify: `stableagent/orchestration/model.py`（`Mandate` 加带默认值的字段）、
  `stableagent/orchestration/store.py`（`insert_mandate`/`_row_to_mandate` 扩到 17 列）
- Test: `tests/unit/test_mandate_extension.py`

**Interfaces:**
- Produces: `Mandate` 新字段（默认值 = "P4 本地授权无外部面"语义）：`repository/base_ref/
  network_profile/request_origin/request_digest: str | None = None`、
  `actions/allowed_paths/allowed_commands/allowed_mcp: list[str] = Field(default_factory=list)`、
  `expires_at: int | None = None`、`requires_approval: bool = False`。
  `origin_kind` 的 Literal 扩为 `Literal["local", "github"]`（默认仍 `"local"`）。
  Store 写读 JSON 列表列（`sort_keys=True, ensure_ascii=False`，沿用 report details 先例）。

- [ ] **Step 1: 写失败的测试**——
      `test_p5_mandate_roundtrip_keeps_every_field`（含 `policy_version="p5-v1"`、11 个新字段
      的满配构造 → 落库 → 读回逐字段相等）；
      `test_p4_local_mandate_roundtrip_unchanged`（旧 6 字段构造，新字段读为默认——A1 往返半边）；
      `test_mandate_ids_list_is_sorted_on_write`（写 `["b","a"]` 读回 `["a","b"]` 或原样——
      以实现口径为准，钉死其一并断言）。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_mandate_extension.py`
      Expected: FAIL — `TypeError`/`ValidationError`（Mandate 无新字段）
- [ ] **Step 3: 实现**：model 加字段 + store 两个读写函数扩列（INSERT 参数从 6 → 17）。
- [ ] **Step 4: 跑到绿**：目标文件 + `tests/unit/test_orchestration_store.py` 回归。
- [ ] **Step 5: 提交**：`git commit -am "feat: carry p5 authorization fields on mandates"`

### Task 5: `StaticPolicy` 与默认拒绝集（步骤 1.5）

**Files:**
- Create: `stableagent/control/policy.py`
- Test: `tests/unit/test_static_policy.py`

**Interfaces:**
- Produces:
  - `StaticPolicyConfig(BaseModel)`：`repository: str`、`actors: frozenset[str]`、
    `grantable_actions: frozenset[Action]`、`allowed_commands: list[str]`、
    `allowed_mcp: list[str]`、`network_profiles: dict[str, list[str]]`、
    `default_validity_ms: int`。构造校验：`Action.ENABLE_AUTO_MERGE in grantable_actions`
    → `AdmissionRejectedError("grantable_must_exclude_auto_merge")`（结构性拒绝的**构造期**
    一道；运行期还有 Task 5.1.5 断言）。
  - `StaticPolicy(config, *, clock, ids)`：
    `admit_explicit(request: ExplicitRequest) -> Admission`——四道门顺序：
    actor ∈ allowlist → 明确 mention → repository 精确匹配 → 生成 request_digest
    （每道失败返回 `admitted=False` + 稳定 `reason_code`：`actor_not_allowlisted` /
    `mention_required` / `repository_mismatch`）；**重复检测归 M3 Task 3.5**（稳定 ID 语义
    依赖 GitHub 请求模型，本任务不实现，账本已注明）。
    `create_mandate(admission, *, actions, allowed_paths, ...) -> Mandate`——**唯一**
    `p5-v1` Mandate 构造点（A2），`expires_at = clock.now_ms() + default_validity_ms`，
    内部把 `Action.ENABLE_AUTO_MERGE` 从传入 actions 里**剔除**并拒绝（双保险）。
    `authorize(mandate, action, resource) -> Decision`——action ∈ mandate.actions ∧
    resource 命中 allowlist（command 精确匹配 / path 前缀 / mcp server/tool ∉ allowed_mcp 拒）
    ∧ 未过期；拒绝 reason 用稳定码。
    `revalidate(mandate, *, when: str) -> Decision`——过期 / `mandate.policy_version !=
    config 派生版本` → deny（when ∈ {"launch","recover","retry","worktree","external_write",
    "mcp_call"} 全集校验，未知 when → `RevalidationFailedError`）。
- [ ] **Step 1: 写失败的测试**——判定矩阵用例：
      `test_admit_rejects_in_gate_order`（非 allowlist + 无 mention + 仓库不符同时踩 →
      reason 按第一道门）；`test_grantable_excludes_auto_merge_by_construction`（构造期抛）；
      `test_authorize_denies_every_action_not_granted`（含 `ENABLE_AUTO_MERGE` 恒 deny——
      A5、L6 运行期半边）；`test_mandate_commands_allowlist_is_exact_match`（`ls` 过、
      `ls -la /etc` 与 `rm` 拒）；`test_revalidate_flags_expiry_and_drift`；
      `test_p5_mandate_is_only_constructable_via_policy`（`create_mandate` 是唯一返回
      `policy_version=="p5-v1"` 的入口：对 `admit→create` 后直接 `Mandate(policy_version="p5-v1")`
      的裸构造在本包外无引用——AST 断言放 Task 8，此处先行为半边：L4 用例，
      请求文本携带 `"actions": ["enable_auto_merge"]` 字样不改变产出 Mandate 的 actions）。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_static_policy.py`
      Expected: FAIL — `ModuleNotFoundError: stableagent.control.policy`
- [ ] **Step 3: 实现** `policy.py`（不 import `execution/integrations`；`ExplicitRequest`
  放 `control/model.py` 作最小形状：`repository/requester/mentioned/stable_id/text`，
  GitHub 富字段归 M3 Task 3.5 扩展——偏差届时记入账本 F1 条目）。
- [ ] **Step 4: 跑到绿**：目标文件 + 全量。
- [ ] **Step 5: 提交**：`git commit -am "feat: admit explicit requests through a static deny-first policy"`

### Task 6: 审计落库——每次决策一行、含 policy_version（步骤 1.6）

**Files:**
- Modify: `stableagent/orchestration/store.py`（`insert_audit`、`list_audit(*, ticket_id=None, kind=None)`、
  `insert_approval`、`update_approval`、`get_approval`、`list_approvals(ticket_id, *, status=None)`）
- Create: `stableagent/control/audit.py`
- Test: `tests/unit/test_authorization_audit.py`

**Interfaces:**
- Produces: `AuditWriter(policy, store, *, clock, ids)`——方法 `admitted(admission)` /
  `authorized(mandate, action, resource, decision)` / `revalidated(mandate, when, decision)` /
  `decided(approval, decision)`，每个恰好写 1 行 `authorization_audit`，`kind ∈
  {"admit","authorize","revalidate","approve"}`，`decision ∈ {"allow","deny"}`，
  `policy_version` 写**决策当刻**的策略版本快照；`reason` 存稳定码，非自由文本。
- [ ] **Step 1: 写失败的测试**——
      `test_each_decision_kind_writes_exactly_one_row`（四类各一次 → 4 行，字段齐全，A3）；
      `test_audit_snapshots_policy_version`（用两个不同 config 实例先后决策 → 两行版本不同）；
      `test_audit_reason_is_stable_code_not_free_text`（deny 行 reason 匹配
      `^[a-z][a-z0-9_]*$`）。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_authorization_audit.py`
      Expected: FAIL — `AttributeError: 'Store' object has no attribute 'insert_audit'`
- [ ] **Step 3: 实现** store 方法 + `audit.py`。
- [ ] **Step 4: 跑到绿**：目标文件 + 全量。
- [ ] **Step 5: 提交**：`git commit -am "feat: persist every authorization decision with its version"`

### Task 7: ApprovalService、Gate 与守卫式取消（步骤 1.7）

**Files:**
- Create: `stableagent/control/approval.py`
- Test: `tests/unit/test_approval_service.py`、`tests/unit/test_guarded_cancel.py`

**Interfaces:**
- Produces:
  - `ApprovalService(store, *, clock, ids)`：`submit(ticket_id, action, resource_digest,
    reason, assignment_id=None) -> ApprovalRequest`（pending 唯一键 `(ticket_id, action,
    resource_digest)` 已存在 pending 则返回既有行，不双开）；`decide(approval_id, approve:
    bool, by)`——终态后再 decide → `ApprovalDecidedError`；`revoke_pending(ticket_id) -> int`。
  - `ApprovalGate(policy, approvals, audit, store, *, clock)`：
    `check(mandate, action, resource) -> Decision`——先 `policy.authorize`；要求审批的
    （`mandate.requires_approval` 或 action ∈ 高危集）必须存在 `approved` 且
    `(action, resource_digest)` 全等；digest 变了 → deny `approval_stale`（B1）；
    同 action 有 `denied` 记录 → deny `approval_denied`，**不再自动放行**（B2 的 Gate 半边）。
  - `GuardedTicketOps.cancel_ticket(store, service_ids, ticket_id, reason=None) -> Ticket`：
    在**单个** `store.transaction()` 内完成：ticket `CANCEL` 转换（纯函数
    `next_ticket_status`）+ 未终态 Assignment 各入队 `CANCEL_ASSIGNMENT` effect（键
    `cancel:{assignment.id}`，与 P4 同形）+ `revoke_pending(ticket_id)`——真·同事务（B3），
    P4 `OrchestrationService.cancel_ticket` 一字不动（约束 1）。
- [ ] **Step 1: 写失败的测试**——
      `test_approved_binding_breaks_on_resource_change`（approve 后同 action 换 path →
      Gate deny `approval_stale`，B1）；
      `test_denied_never_self_heals`（denied 后重新 submit+check → 仍 deny，B2）；
      `test_decide_twice_is_rejected`；
      `test_approval_survives_reopen`（关店重开 Store 后 Gate 仍判 approved，B4）；
      `test_guarded_cancel_rolls_back_all_three_on_error`（在 revoke 处注入异常 →
      ticket 未取消、effect 未入队、approval 仍 pending，证明原子性，B3 + L3）；
      `test_guarded_cancel_revokes_pending_only`（approved 历史行保留——审计不删史）。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_approval_service.py
      tests/unit/test_guarded_cancel.py` Expected: FAIL — `ModuleNotFoundError`
- [ ] **Step 3: 实现** `approval.py`。
- [ ] **Step 4: 跑到绿**：目标两文件 + 全量。
- [ ] **Step 5: 提交**：`git commit -am "feat: gate high-risk actions on digest-bound approvals"`

### Task 8: 依赖方向矩阵——三新包 × 五旧包（步骤 1.8）

**Files:**
- Modify: `tests/unit/test_control_boundaries.py`
- Test: 同文件（本 Task 无生产代码）

**Interfaces:**
- Produces: AST 矩阵测试 `test_no_legacy_package_imports_p5_layers`——
  扫描 `core/context/providers/tools/orchestration` 五包全部 `.py`，断言不 import
  `stableagent.control/execution/integrations`（后两包尚不存在 → `rglob` 空转但断言
  常量表已含三者，M2/M3 落包后自动生效）；
  `test_control_never_imports_execution_or_integrations`（方向锁：
  `stableagent/control/*.py` 不得 import `stableagent.execution/integrations`）；
  `test_p5_v1_mandate_construction_is_confined_to_control`——全库 AST 找
  `Mandate(...)` 构造调用点，`policy_version="p5-v1"` 字面量只允许出现在
  `stableagent/control/`（A2/L4 扫描半边；`orchestration/model.py` 默认值路径不含该字面量）。
- [ ] **Step 1: 写失败的测试**：上述三个用例（第三个先对 `control/policy.py` 放行、
      人为在 docstring 外构造一个越界样例进红）。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_control_boundaries.py`
      Expected: 前两例 PASS（现状即满足），第三例红——若字面量断言写法未收口，先记红再收。
- [ ] **Step 3: 实现**：把 `policy.py` 的 `p5-v1` 收敛为单一模块常量
      `POLICY_VERSION = "p5-v1"`，扫描按"该常量的 import 方只允许 control 包"断言。
- [ ] **Step 4: 跑到绿**：目标文件 + `test_orchestration_boundaries.py` 同跑（新旧矩阵并绿）。
- [ ] **Step 5: 提交**：`git commit -am "test: lock the p5 dependency direction across packages"`

### Task 9: M1 出口——门禁、账本回填与归属修正（步骤 1.9）

**Files:**
- Modify: `docs/acceptance/p5-guarded-execution.md`（1.1~1.9 勾选 + A1~A3、A5、B1~B4、L3、L4、
  L5（授权侧）证据回填）

- [ ] **Step 1: 跑出口命令并收集输出**：

```bash
.venv/bin/pytest -q
.venv/bin/mypy
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
git diff 234f620..HEAD --stat -- stableagent/core/        # 必须 0 行（L1）
.venv/bin/pytest --cov=stableagent --cov-report=term-missing  # control 各文件 ≥90%
```

- [ ] **Step 2: 复核心脏边界**：L2/L3/L4/L5 授权侧用例名与输出记进第 0 节对应条目。
- [ ] **Step 3: 回填账本**：第 1 节 M1 九步全勾 + 实测；第 2 节 A1~A3、A5、B1~B4 勾选共
      **8 项**（A4 保持未勾——接线在 M2/M3，归属修正备注已有），计数改为 **DoD 8/46、
      里程碑 9/40**。口径（沿用 P4）：L1~L8 只有在其事实被**全部里程碑**完整证明后才勾，
      M1 只把边界用例名与输出写进对应条目，不提前勾选。
- [ ] **Step 4: 提交**：`git add -A && git commit -m "docs: close p5 m1 with measured evidence"`
- [ ] **Step 5: 汇报**：向维护者给出 M2 开工分支与是否继续的确认点（M2 属新里程碑，
      按 P4 惯例出口后另行确认开工）。

