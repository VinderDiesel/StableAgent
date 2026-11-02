# P5 详细实施规格：受控执行与仓库集成

> **阶段定位**：在 P4 Ticket 编排内核之上，让 Worker 能在明确授权、隔离工作区和可审计副作用的条件下处理一个 GitHub 仓库中的**显式人工任务**。P5 不是常驻 Super-Agent：不轮询所有事件、不自动接受推断任务、不自动 merge。
>
> **开始条件**：P4 的 Ticket / Assignment / Outbox / Scheduler / 离线 crash recovery 已完成并形成稳定接口。
>
> **完成条件**：allowlist 人员发起的显式请求可以在隔离 worktree 内执行，创建或更新 PR/评论的行为经结构化授权、outbox 和幂等测试验证；任何真实 GitHub 或 Docker 测试均为可选补充。

## 1. 目标、范围与安全定义

### 目标

1. 每个需要修改仓库的 Ticket 获得独立的 Git worktree、分支和可追溯执行环境。
2. `Policy` / `Mandate` 成为持久化、版本化的授权事实源，替换 P4 的最小本地 Mandate。
3. 每个 Worker 工具、命令、网络访问、GitHub 写入和交付动作在执行前经过结构化判定。
4. 支持 MCP Client 作为 Worker Tool 适配层，不把 MCP Server 或被动事件接入混入本阶段。
5. 支持一次显式 GitHub 请求的人工触发闭环：请求 → Ticket → worktree Worker → PR 或评论 → 回写结果。
6. 通过 outbox 保证 GitHub 写入和本地准备动作在重试/恢复时不会重复产生语义副作用。

### 非目标

- 不监听 webhook、Issue/PR 全量流或 CI；P6 才引入长期 Source/Trigger/Router。
- 不接受未明确 `@agent` 的请求，不实现 `observe` 或 `auto` 主动规则。
- 不实现自动 merge、自动 rebase、跨仓库执行或多租户。
- 不将 worktree 当作安全沙箱；不宣称本地受限模式能隔离恶意代码。
- 不实现长期 Memory、管理 UI、SSE/WebSocket 或 Provider 成本路由。

### 安全层次

| 层次 | 解决的问题 | P5 要求 |
|---|---|---|
| 工作目录隔离 | 并行 Ticket 修改互相污染 | 必须：独立 worktree、分支、目录生命周期 |
| 授权与审批 | Agent 是否有资格做某动作 | 必须：Policy/Mandate/Approval 结构化判定 |
| 命令与工具约束 | 误调用高风险工具或命令 | 必须：allowlist、参数校验、拒绝记录 |
| Sandbox | 不可信代码影响宿主机、网络、资源 | 必须提供可选 Docker 实现；本地模式显式标为非安全边界 |
| 凭据隔离 | 密钥泄漏到 prompt、日志、子进程 | 必须：最小环境注入、脱敏日志、禁止写入 Ticket/AgentState |

## 2. 模块与稳定接口

```text
stableagent/
  orchestration/            # P4，继续作为 Ticket / Outbox 所有者
  execution/
    worktree.py             # GitWorktreeManager
    command.py              # CommandRunner / policy wrapper
    sandbox.py              # SandboxRunner + LocalRestrictedRunner + DockerRunner
    environment.py          # 仅暴露安全的执行变量
  control/
    policy.py               # Policy、Mandate、Action、Admission
    approval.py             # ApprovalRequest、ApprovalStore、Gate
  integrations/
    github.py               # 显式请求适配器、GitHubClient、PR/comment effect handlers
    mcp.py                  # MCP transport/session/tool adapter
```

`orchestration.WorkerExecutor` 是 P5 的执行入口。P5 通过一个 `RepositoryWorkerExecutor` 实现替换 P4 的本地 `AgentExecutor`，不得改变 Scheduler、Store 或 Effect 协议。

### 2.1 授权模型

#### Action

所有可见副作用必须归类为 Action，至少包括：

```text
prepare_worktree
read_repository
write_repository
run_command
call_mcp_tool
create_branch
commit_changes
push_branch
create_pull_request
comment_issue
comment_pull_request
request_review
enable_auto_merge
```

`enable_auto_merge` 在 P5 必须永久拒绝；存在该 Action 只是为了让 Policy 明确表达默认拒绝，而不是未来绕过。

#### Policy 与 Mandate

```python
class AuthorizationPolicy(Protocol):
    def admit_explicit(self, request: ExplicitGitHubRequest) -> Admission: ...
    def authorize(self, mandate: Mandate, action: Action, resource: Resource) -> Decision: ...
    def revalidate(self, mandate: Mandate, now: datetime) -> Decision: ...
```

`Mandate` 至少记录：

- `policy_version`、`origin`（GitHub issue / PR / comment URL 与 source ID）、`requester`；
- repository、base ref、允许的 paths / commands / MCP servers / network profile；
- 授权的 action 集合、到期时间、是否需要审批；
- 请求文本摘要与不可变 request digest。

规则：

- 只有 Policy 可创建 Mandate；Worker、prompt、Issue 文本、工具输出都不能扩展 Mandate。
- Policy 变更产生新版本。已运行 Assignment 在恢复、重试、push、PR/comment 写入前必须 revalidate。
- 拒绝、审批、过期和 revalidation 失败必须形成结构化 Report / Audit record，不得仅以自然语言输出。
- P5 默认 Policy：allowlist + 显式 `@agent` + 单仓库 + 创建分支/PR 可允许；自动 merge、任意 shell、全网访问、未列入 MCP server 的工具均拒绝。

### 2.2 审批模型

`ApprovalRequest` 必须持久化在 P4 Store 扩展表中，字段包括 `id`、`ticket_id`、`action`、`resource_digest`、`reason`、`requested_at`、`status`、`decided_by?`、`decided_at?`。

- 需要审批的动作不得先执行后补记录。
- `approved` 必须绑定同一 Action 和资源摘要；参数或资源改变后旧批准失效。
- `denied` 使当前 Assignment 报 `blocked` 或 `failed`，不得自动改用其他动作绕过。
- 取消 Ticket 时撤销 pending approval。
- P5 的审批接口可为 API/CLI；面板属于 P6。

### 2.3 执行与 worktree

`GitWorktreeManager` 只使用参数数组调用 `git`，不允许 shell 拼接；所有路径经 `Path.resolve()` 验证在配置的 root 内。

- `prepare(ticket, assignment)` 创建命名稳定、可复用的 worktree 和受控分支；创建动作由 outbox effect 驱动。
- branch 名使用受限字符、Ticket 短 ID 和稳定 slug；不得接受 request 文本直接作为 branch/path/command。
- 若已有 worktree/branch，验证其仓库、Ticket metadata、base ref 与 ownership，一致才复用，不一致则失败并保留诊断。
- Ticket 结束后默认保留 worktree 供审查；清理由显式、可确认的后续命令完成，不在 P5 自动删除。
- Agent 仅获取 worktree 路径和非敏感环境变量，不能获取 P4 SQLite 连接或其他 Ticket 的目录。

### 2.4 命令与 Sandbox

定义：

```python
class SandboxRunner(Protocol):
    async def run(self, request: CommandRequest) -> CommandResult: ...
```

- `LocalRestrictedRunner`：仅在明确允许的命令名称、参数模式、cwd 与超时范围内通过 `subprocess` 执行；其文档必须声明它不是安全沙箱。
- `DockerRunner`：镜像、只读源挂载、写入 worktree 挂载、CPU/内存/pid/超时限制、默认网络 `none`；allowlist 网络只能通过显式 Policy profile 启用。
- 不接受 `shell=True`、命令字符串解析、宿主网络默认开放、Docker socket 挂载、特权容器或将宿主密钥挂载进容器。
- CommandResult 需包含 exit code、截断 stdout/stderr、开始/结束时间、被拒绝原因；日志必须脱敏。

### 2.5 MCP Client

第一版实现 stdio JSON-RPC MCP Client；远程 HTTP/SSE transport 可留给 P7，但接口不能阻塞后续实现。

1. `McpTransport` 管理进程、请求 ID、超时和关闭；用参数数组启动，环境最小化。
2. `McpSession` 执行 `initialize`、`tools/list`、`tools/call`，校验 JSON-RPC id 和错误。
3. `McpToolAdapter` 将 allowlist 中的 MCP 工具映射为现有 `Tool`，保留 server/tool 名称、schema、超时和资源限制。
4. 工具发现不等于授权：发现到的 server/tool 默认不可被 Worker 调用，需在 Mandate 的 allowed servers/tools 中明确列出。
5. MCP tool output 按现有 `ToolResult` / Evidence 流程归一化，超长结果进入 P3 context compression，不得绕过 token budget。

### 2.6 GitHub 显式请求闭环

P5 仅支持由 API/CLI 显式交给系统的 `ExplicitGitHubRequest`。该请求包含 repository、Issue/PR/comment URL、actor、文本、是否直接 @agent、接收时间和原始稳定 ID。

流程：

```text
ExplicitGitHubRequest
 → AuthorizationPolicy.admit_explicit
 → Mandate + Ticket + launch effect（同一事务）
 → worktree prepare / RepositoryWorkerExecutor
 → structured Report
 → approved GitHub comment / PR effect
```

- GitHub API 实现使用注入式 `GitHubClient` 协议；真实实现可基于 `httpx`，所有单测用 fake client。
- 写评论、创建 PR、push 前重新检查 Mandate 和 Approval。
- 每个 GitHub 写 effect 使用外部稳定 id 与 Action/resource digest 构造 idempotency key；重试不得创建重复 PR 或重复评论。
- 默认仅允许创建 PR，不允许 merge 或 enable auto-merge。
- 失败时保留 Ticket、worktree、Report、effect 尝试记录和对外可读错误；不吞掉 GitHub 状态码。

## 3. 实施工作包

### WP-5.1：扩展 P4 模型与结构化授权

**文件**：`control/policy.py`、`control/approval.py`、`orchestration/model.py`、`orchestration/store.py`、测试。

1. 为 P4 Mandate 新增默认字段并迁移旧 `p4-local-v1` records。
2. 实现 Action、Resource、Admission、AuthorizationDecision、PolicyVersion、ApprovalRequest 模型。
3. 先实现 `StaticPolicy`：单 repository、allowlist、明确 mention、预定义 actions 和 command/MCP allowlist。
4. 建立审计表：admission、authorize、revalidate、approval 的决定均持久化，并记录决策使用的 policy version。
5. 定义 revalidation 时机：Assignment launch、recover、retry、prepare worktree、push、create PR/comment、MCP tool call 前。

**验收**：权限不足、过期、Policy 变化、资源摘要改变、审批拒绝均不能执行 action；旧 P4 record 能迁移。

### WP-5.2：worktree 与本地受限执行

**文件**：`execution/worktree.py`、`command.py`、`environment.py`、`sandbox.py`、相关测试和示例。

1. 用临时本地 Git 仓库测试 worktree 创建、复用、冲突、base ref、分支命名、隔离和保留。
2. 为 CommandRunner 加 command allowlist、cwd root、timeout、输出上限和脱敏。
3. 所有 prepare/command 均经 P4 effect 执行，并将结果写入 Assignment Report。
4. 实现 LocalRestrictedRunner；明确拒绝未授权二进制、cwd 越界、环境中秘密变量和 shell 语法。
5. 执行环境只传 `STABLEAGENT_TICKET_ID`、`ASSIGNMENT_ID`、`WORKTREE`、仓库信息等安全变量。

### WP-5.3：Docker sandbox

**文件**：`execution/sandbox.py`、Docker 测试标记、文档。

1. Docker 不可用时测试跳过并说明；核心单测不得依赖 Docker。
2. 默认 network none、非特权、无 Docker socket、资源上限、只挂载该 worktree、无 secrets。
3. 验证超时、退出码、内存/CPU/pid 限制配置传递、网络默认关闭和拒绝危险挂载。
4. Docker image 必须可配置并带 digest/pin 指南；不得静默使用浮动 `latest` 作为生产默认。

### WP-5.4：MCP Client 与 Tool 适配

**文件**：`integrations/mcp.py`、`tests/unit/test_mcp_client.py`、`tests/integration/test_mcp_stdio.py`。

1. 用 fake transport 覆盖 initialize、list、call、JSON-RPC error、超时、进程退出和 schema 错误。
2. 用小型本地 stdio fixture 覆盖实际 framing；不得连接公网。
3. 验证未在 Mandate allowlist 内的 server/tool 被拒绝，且不会启动调用。
4. 验证 MCP output 进入 ToolResult/Evidence/ContextEngine 正常链路。

### WP-5.5：GitHub 显式请求与交付 effect

**文件**：`integrations/github.py`、`orchestration/service.py`、`outbox.py`、fake GitHub 测试。

1. 导入显式请求时校验稳定 event ID、repository 和 mention；重复请求返回同一 Ticket 或明确 duplicate 结果。
2. 在同一 transaction 创建 Mandate、Ticket、初始 Input、Assignment 和 launch effect。
3. 实现 comment、create PR、request review 的 effect handler；每项都验证授权和 idempotency key。
4. GitHub API 错误按可重试/不可重试/需人工分类，写 Report 和 effect 状态；不要复用 P2 Provider 错误分类而不做适配。
5. 利用 fake client 验证重启、超时、重复 effect 与 PR/comment 幂等；可选真实仓库 smoke test 必须显式 opt-in。

### WP-5.6：端到端演示、ADR 与发布证据

**文件**：`examples/05_guarded_github_task.py`、`docs/design-decisions/05-guarded-execution.md`、README、CHANGELOG。

- 示例用本地 Git fixture 和 fake GitHub client 走通：明确请求 → 授权 → worktree → 修改 → PR effect。
- 记录拒绝示例：非 allowlist、无 mention、越权命令、无审批 PR 写入、自动 merge。
- ADR-05 解释 Source 与 Tool 的边界、worktree 与 sandbox 的差别、Mandate/revalidation、approval digest、MCP allowlist、GitHub effect 幂等。

## 4. 测试与退出条件

### 必测场景

| 范畴 | 验收事实 |
|---|---|
| 授权 | 未授权输入、过期 Mandate、Policy 变更、审批拒绝均不产生外部 action |
| worktree | Ticket 目录隔离、重复 prepare 幂等、路径逃逸拒绝、结束后保留 |
| 命令/sandbox | shell 注入拒绝、allowlist、生效超时、默认无网络、无 secret 泄漏 |
| MCP | discovery 不授权、allowlist、生效 schema、错误归一化、离线 stdio |
| GitHub | 明确请求去重、评论/PR 幂等、恢复不重复写入、禁止 auto merge |
| 恢复 | P4 Store/outbox + P2 checkpoint 共同恢复，未确认 effect 可重试，已确认 effect 不重复 |
| 回归 | P0-P4 全量测试通过；Core loop 无 P5 控制面逻辑 |

### 阶段验证命令

```bash
.venv/bin/pytest -q tests/unit/test_policy.py tests/unit/test_approval.py tests/unit/test_worktree.py
.venv/bin/pytest -q tests/unit/test_command_runner.py tests/unit/test_sandbox.py tests/unit/test_mcp_client.py
.venv/bin/pytest -q tests/unit/test_github_integration.py tests/integration/test_mcp_stdio.py
.venv/bin/pytest -q tests/integration/test_guarded_ticket_recovery.py
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
```

Docker/真实 GitHub 用例只能在显式 marker 与环境变量存在时运行。

### P5 退出清单

- [ ] P4 的 Ticket / Assignment / Outbox 语义和恢复测试无回归。
- [ ] Policy、Mandate、Approval 和 Audit 为持久化、版本化、结构化事实源。
- [ ] 每个高风险 Action 在执行前可判定、可审计、可重新验证；默认拒绝 auto merge。
- [ ] worktree 隔离、路径校验和受限 command runner 有离线测试。
- [ ] Docker runner 默认无网络、最小挂载和资源限制；未配置 Docker 时不影响核心门禁。
- [ ] MCP Client 只允许 Mandate 指定的 server/tool，并沿用 Tool/Evidence/ContextEngine 链路。
- [ ] allowlist + 明确 GitHub 请求可以创建 Ticket、执行 Worker 并幂等地写 PR/评论。
- [ ] 所有恢复路径不重复 PR、评论、push 或其他已确认副作用。
- [ ] ADR-05、安全说明、示例、CHANGELOG、README 完整且不夸大本地限制模式的安全性。

## 5. 阶段交接：P6 的输入

P6 建立长期 Router/Source 时复用 P5 的授权、GitHubClient、Channel effect 和 RepositoryWorkerExecutor：

- P6 的 GitHub Source 只负责将事件标准化为 Trigger；它不能直接创建 worktree、调用 Worker 或绕过 Policy。
- P6 Router 只能经 P5 `AuthorizationPolicy` 创建/恢复 Mandate，再调用 P4 `OrchestrationService`。
- P6 后续 Issue/PR/CI 信息作为 TicketInput/Subscription context，不因此扩大 Mandate。
- P5 的“仅显式请求、无自动 merge”安全默认值必须保留；P6 `observe` 也不执行副作用。
