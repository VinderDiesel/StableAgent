# P5 验收账本：受控执行与仓库集成

> **状态：Active（2026-11-03 计划经维护者确认，ADR-05 Accepted）。** 本文件把
> [P5 实施规格](../specs/p5-guarded-execution-repository-integration.md) 的 6 个工作包与
> [ADR-05](../design-decisions/05-guarded-execution.md) 的 10 项决策展开为可勾选的验收账本。
> 执行从 M1 任务级计划（第 5 节，开工前追加）开始；当前 **DoD 0/46、里程碑步骤 0/40**。
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

- [ ] **1.1** `control/` 包骨架：`CONTROL_SCHEMA_VERSION = "0.1.0"` 不进顶层重导出；
      `errors.py` 稳定 `.code` 错误家族。验证：包导入测试 + 顶层 `__init__` 无新增导出。
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
| M1 | L3、L4、L5（授权侧） | A1~A5、B1~B4 |
| M2 | L7、L8（部分） | C1~C8、D1~D3 |
| M3 | L5、L6、L7（凭据侧） | E1~E4、F1~F6 |
| M4 | L1、L2、L8（收口） | G1~G4、H1~H4 |

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
