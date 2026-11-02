# P7 详细实施规格：生产化与生态扩展

> **阶段定位**：将已在单仓库、observe-first 条件下验证的 Super-Agent 扩展为可部署、可观测、可治理的生产系统，同时保持 StableAgent Core 可嵌入、控制平面可替换、授权与外部副作用可审计。
>
> **开始条件**：P6 所有退出条件满足，并已获得单仓库 Beta 的 observe 数据、故障演练记录和明确的未解决风险清单。
>
> **完成条件**：多 Provider、多 Source、MCP 治理、Memory、实时 transport、监管面、多租户与运维能力在同一安全/审计模型中工作；全库质量门禁达到路线图终点，生产部署示例可复现。

## 1. 目标、范围与发布策略

### 目标

1. 扩展 Provider、Source、Channel 和 MCP 管理，但始终通过 P3/P6 已有边界接入。
2. 将成本、延迟、成功率、上下文、effect、授权和人工注意力纳入统一可观测模型。
3. 提供短期与长期 Memory，具备隔离、生命周期、删除、评估和失败降级能力。
4. 提供 FastAPI + SSE / WebSocket 的管理与实时事件接口，以及最小可用监管 UI/API。
5. 支持多租户、部署隔离、版本化配置、备份、恢复和升级迁移。
6. 在满足证据门槛的情况下，从 `observe` 有选择地提升低风险规则为 `auto`，并能立即回滚。

### 非目标

- 不承诺无人工审核的通用自治编码。
- 不默认跨租户共享 Memory、MCP server、凭据、worktree、Source cursor、Ticket 或审计记录。
- 不以提升自动化率为唯一指标；误报、授权拒绝、通知噪音、恢复正确性和人工负担同等重要。
- 不因新增平台功能改变 Core `AgentLoop` 的单 Worker 边界，或改写 P4/P6 的领域状态和 outbox 语义。

### 推荐的子版本顺序

P7 规模较大，必须按下列子版本交付。任一子版本未完成，不并行启动后续生产开放：

```text
P7.1 可观测性与审计基线
→ P7.2 多 Provider 与成本路由
→ P7.3 多 Source / Channel 与 MCP 治理
→ P7.4 Memory 与数据生命周期
→ P7.5 FastAPI、SSE/WebSocket、监管 UI 与多租户
→ P7.6 部署、演练、auto 规则分级发布与终点收口
```

## 2. 总体架构与公共边界

```text
Clients / UI / CLI
  │ FastAPI + SSE/WebSocket + RBAC
  ▼
Tenant-scoped Supervisor Runtime
  Source → Trigger → Router/Policy → Ticket → Assignment → Worker
  Channel ← Outbox ← Report/Result
  │              │                    │
  ├─ Audit/Trace/Metrics               ├─ ProviderRouter
  ├─ MemoryService                     ├─ MCPRegistry
  └─ Config/Secrets/Deployment         └─ Sandbox/Worktree
```

### 不变边界

- Provider 仍实现 `complete()`、`acomplete()`、`stream()`；不得为新模型创建第四种 Worker 调用形态。
- Source 仍输出 Trigger，Channel 仍通过 outbox 写入；新集成不能直接写 Ticket 或网络副作用。
- Memory 是 Worker/Router 可检索的受控信息服务，不是 `AgentState` 的无限字段。
- Tenant ID 必须贯穿 Store、Source cursor、Ticket、Assignment、Mandate、Effect、Audit、Memory、MCP 和执行环境。
- observability 是旁路或提交后记录；采集失败不能阻止授权判定、Ticket 状态变更或 effect 重试。
- `auto` 只能由 Policy 规则开启，不能由模型、Memory、MCP tool 或 Source message 动态启用。

## 3. 实施工作包

### WP-7.1：可观测性、审计与 SLO 基线

**目录**：`stableagent/observability/`，包含 `events.py`、`audit.py`、`metrics.py`、`tracing.py`、`redaction.py`。

1. 定义稳定的结构化领域事件：Trigger 接收、Decision、Mandate、Ticket/Assignment 状态、Worker step 摘要、ContextReport、Effect 尝试、Approval、Source health、Memory operation、MCP call。
2. 每条记录包含 `timestamp`、tenant、repository/source、ticket/assignment/run/effect 关联 ID、outcome、reason code；不得默认记录完整 prompt、secret、工具原始输出或私有代码。
3. 审计日志 append-only；记录“谁/哪条 Policy/哪个批准”授权了每个高风险 Action，以及对应 external effect receipt。
4. 指标最少包括：Worker 成功率、P50/P95 队列等待和完成时延、恢复率、Provider 错误、token 估算误差、成本、工具裁剪、effect 重试、Source lag、通知数/每 Ticket、审批等待时长、auto 规则误报率。
5. trace 以 Ticket 为根，关联 Trigger、Decision、Assignment、Worker run、context report、effect 和 Channel receipt；采样和保留期可配置。
6. 建立 SLO：例如 effect 投递成功率、重复外部副作用为 0、恢复正确性、Source lag、人工 attention SLA。阈值须通过配置和报警而非硬编码。

**验收**：脱敏测试覆盖 secret/PII/长文本；审计链能从 PR/comment 追到 Mandate 与 Approval；采集故障不影响核心事务。

### WP-7.2：多 Provider 与成本路由

**目录**：`stableagent/providers/anthropic.py`、`ollama.py`、`routing.py`、`control/cost.py`。

1. Anthropic、Ollama 等 Provider 严格实现既有三调用形态与 ToolCall/usage 契约；每个 provider 有 fake transport 单测和可选 live smoke marker。
2. `ProviderRouter` 在 Assignment 启动前基于 tenant policy、任务类别、模型能力、预算、健康和 fallback 选择 Provider；不能在一个已有 Worker step 中无记录地换模型。
3. 每次选择持久化到 Assignment/Audit：候选、选择理由、模型版本、价格表版本、fallback 链。
4. Token/cost 以 Provider 实际 usage 优先，P3 估算为预估与缺失 usage 的降级；货币、时区、计费版本和估算误差必须明确。
5. `max_total_tokens` 和成本上限在 Core 外层/Assignment policy 强制。达到软阈值可请求审批，达到硬阈值必须暂停或失败，不能静默超支。
6. Provider 故障转移遵循 P2 retry 语义，但 Router/Assignment 记录选择，不把租户成本规则塞进 `AgentLoop`。

**验收**：相同 Assignment 的模型选择可重放解释；超预算不调用 Provider；fallback 不丢失 run/Ticket 关联；每 Provider 的工具/流式协议兼容测试完整。

### WP-7.3：多 Source、Channel 与 MCP 治理

**目录**：`stableagent/supervisor/sources/`、`channels/`、`integrations/mcp_registry.py`。

1. 新 Source（飞书、Slack、Jira）各自实现 P6 Source/Trigger/Thread/Channel 协议，拥有独立 cursor、bot identity、actor namespace、健康和去重规则。
2. 禁止跨 Source 隐式身份映射。若需要映射，必须经 tenant 管理员维护的显式 IdentityLink、审计和撤销机制。
3. Channel 写入仍走 Outbox，并按 Ticket + destination 保序、幂等、可重试；外部删除/权限丢失必须形成 receipt 和可行动问题。
4. `McpRegistry` 管理 server descriptor、版本、transport、tenant allowlist、工具 schema、健康、凭据引用和审计级别。
5. MCP server 注册、发现、启用、禁用和版本更新必须有管理员授权；Worker discovery 不等于可调用权限。
6. 远程 MCP transport 必须设置 TLS、超时、请求大小、域名 allowlist 和认证 secret 引用；stdio server 保持 P5 的最小环境与子进程限制。

**验收**：每个 Source 有 fake fixture、重启/重放/去重/自身 bot 过滤测试；Tenant A 不能使用 Tenant B 的 Source/MCP/Channel；禁用 server 后 pending 调用被安全阻止并审计。

### WP-7.4：Memory 与数据生命周期

**目录**：`stableagent/memory/store.py`、`short_term.py`、`long_term.py`、`retrieval.py`、`policy.py`。

1. 明确两层：短期 Memory 是受 Ticket/Assignment scope 限制的摘要、偏好和已验证事实；长期 Memory 是 tenant/repository-scoped 文档或事实记录。
2. 每条 Memory 有 `id`、tenant、scope、origin、content/digest、embedding/reference、created/updated/expires、confidence、classification、delete status、source provenance。
3. 写入必须经 MemoryPolicy：允许来源、敏感数据分类、去重、审批、TTL、最大容量和删除策略。模型不能自行把任意对话永久写入。
4. 检索必须经 Authorization：只返回当前 Ticket/Mandate 有权读取的 scope；结果应带 provenance 和 token budget，注入 P3 ContextEngine 前可裁剪。
5. 提供 retriever 的确定性 fake/lexical baseline；向量数据库为可选适配器，不得使核心测试依赖外部服务。
6. 支持 erase、过期、重嵌入、导出与审计；删除必须传播到索引/缓存，并提供可验证 receipt。
7. 对 Memory 质量建立离线评估集：召回、精度、陈旧事实、权限泄漏、答案质量和 token 成本。

**验收**：跨 tenant/scope 读取拒绝；过期和删除后无法检索；Memory failure 降级为无 Memory 不阻断 Ticket；评测不依赖在线 embedding 服务。

### WP-7.5：FastAPI、实时 Transport、监管 UI 与多租户

**目录**：`stableagent/transport/`、`stableagent/api/`、`stableagent/control/tenancy.py`。

1. 引入 FastAPI 作为可选部署 extra，Core install 不能因此增加 Web 运行时依赖。定义 `server` extra 与版本边界。
2. API 仅调用 Router/Orchestration service；不得直接修改 SQLite 表或绕过 Policy/Outbox。
3. 鉴权采用明确的 tenant-scoped principal/RBAC：至少 operator、reviewer、viewer、admin。敏感动作（Policy、MCP、approval、stop、retry、auto 开关）必须审计。
4. SSE 提供 Ticket 时间线、Report、状态变化；WebSocket 仅在确有双向需求时支持。两者需 cursor、reconnect、backpressure、授权重检和事件脱敏。
5. UI 按 P6 注意力顺序：attention/approval、失败、ready review、健康、历史。健康状态不抢占首屏。
6. 租户隔离采取 tenant key + 数据库行过滤或独立数据库之一，并明确备份、迁移和执行资源隔离。所有 Repository/Source/MCP/Memory/worktree 路径包含 tenant 边界。

**验收**：API/RBAC 负面测试、SSE 重连和权限变化测试、跨租户数据/事件/路径隔离测试、写操作审计测试；UI 端到端测试优先覆盖行动而非像素。

### WP-7.6：部署、运维、分级 auto 与终点收口

**目录**：`docs/guides/`、`docs/runbooks/`、`docs/design-decisions/07-production-hardening.md`、`examples/`、CI/CD 配置。

1. 交付容器化部署、配置 schema、secret reference、数据库备份/恢复、滚动升级、schema migration、Source reconnect、worker drain、事故回滚 runbook。
2. 每个 tenant/runtime 使用独立 state directory/lock、最小凭据、资源配额和日志保留策略。
3. 增加 chaos/failure 演练：Provider/Source/MCP/Channel 断连，SQLite 暂不可用，worker crash，Docker timeout，API restart，重复 webhook，审批过期，Memory index 故障。
4. `auto` 开启流程必须是一个可审计变更：选择单条 Policy rule、限定 tenant/repository、指定 Action 上限、根据 P6 observe 数据设置目标/错误阈值、先 canary、持续监控、自动/手动回滚条件。
5. auto 规则的硬门槛：重复副作用 0、授权越权 0、可解释 Decision 100%、通知预算达标、误报率/人工拒绝率低于 policy 阈值、回滚演练通过。
6. 产出完整 API Reference、运维手册、安全模型、ADR、版本迁移指南、benchmark 报告和端到端生产姿态示例。

## 4. 测试策略与质量门禁

### 分层测试

| 层级 | 内容 |
|---|---|
| 单元测试 | policy、cost、provider parser、Source mapper、MCP protocol、Memory policy、redaction、RBAC、状态机 |
| 组件测试 | SQLite/Outbox、Provider fake transport、MCP stdio fake、Docker fixture、SSE/WebSocket reconnect |
| 集成测试 | 多 Tenant Router、跨 Source/Channel、Memory retrieval、Provider fallback、崩溃恢复、迁移 |
| e2e | FastAPI + GitHub fake + Worker + PR/comment effect + approval + observe/auto canary |
| 性能/benchmark | context、工具爆炸、恢复、queue latency、Source lag、Memory retrieval、effect throughput、成本误差 |
| 安全测试 | 越权、租户隔离、secret redaction、prompt injection、MCP allowlist、路径逃逸、网络限制、重复 webhook |
| 演练 | backup restore、upgrade/downgrade、worker drain、灾难恢复和 auto rollback |

### 终点门禁

```bash
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy --strict
.venv/bin/pytest -q
.venv/bin/pytest --cov=stableagent --cov-report=xml --cov-report=term-missing
```

CI 还必须：

- Python 3.10/3.12 完整矩阵；可选支持版本另行明确。
- 全库覆盖率 ≥80%，核心模块覆盖率阈值不低于 90%。
- benchmark 采用固定参数并检查相对回归阈值；阈值变化需 ADR/基线更新说明。
- 网络、Docker、真实 Provider/Source 用例按 marker 隔离，默认 CI 仍可无 secrets 运行。
- 依赖漏洞、许可证、镜像 digest 和配置 schema 检查纳入发布流水线。

## 5. P7 退出清单

- [ ] ProviderRouter 支持多个 Provider，成本/预算/选择/fallback 可追溯且不污染 Core。
- [ ] 新 Source/Channel/MCP server 通过 Trigger/Outbox/Policy 接入，并实现 tenant 隔离与幂等。
- [ ] Memory 具备 scope、授权、生命周期、删除、降级与离线质量评估。
- [ ] 审计、指标、trace、SLO 与脱敏链路覆盖 Worker 到外部 effect 的完整路径。
- [ ] FastAPI、SSE/WebSocket、RBAC 与最小监管 UI 在不绕过服务边界的前提下可部署。
- [ ] 多租户的 Store、Source、MCP、Memory、worktree、凭据、事件流和备份彼此隔离。
- [ ] 部署、备份、恢复、升级、灾难演练和 auto 回滚 runbook 可复现。
- [ ] 每条 auto rule 有 observe 数据、canary 范围、硬阈值、审计和回滚证据。
- [ ] 全库覆盖率 ≥80%，mypy strict、ruff 0 warning、CI benchmark 和安全门禁通过。
- [ ] 文档、API Reference、示例和 CHANGELOG 完整，且对自动化能力、安全边界和已知限制做出准确表述。

## 6. 最终系统验收

在一个隔离的测试组织中完成以下演练，作为 P7 的最终端到端证据：

1. 管理员创建 tenant、GitHub Source、MCP allowlist、预算和 observe Policy。
2. allowlist 开发者通过明确请求创建 Ticket，系统以隔离 Worker 修改 worktree 并创建 PR。
3. CI、PR review 与后续评论回到正确 Ticket；scope 外请求被 handback 并重新授权。
4. 在 Worker、Router、Outbox 和 API 各自崩溃后恢复，验证没有重复 PR/comment/commit，也不丢失待处理输入。
5. 在批准后切换一条受限 canary rule 到 auto，注入规则不匹配、权限拒绝、MCP 失败、Source 重复和预算耗尽事件，确认不会越权或制造通知风暴。
6. 导出审计链，验证每个外部副作用都能追溯到 tenant、Trigger、Decision、Mandate、Approval、Ticket、Assignment、Provider/Tool 和 effect receipt。
7. 删除一条 Memory、撤销一项 MCP 授权、停用一个 Source，确认后续检索/调用/投递均被拒绝且旧数据按生命周期处理。

通过该演练后，StableAgent 才可声明具备“面向生产的受控 Super-Agent 参考实现”；任何未覆盖的 Source、Provider、自动规则或隔离场景必须继续标注为实验性。
