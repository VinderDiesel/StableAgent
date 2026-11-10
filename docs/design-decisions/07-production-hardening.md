# ADR-07：P7 生产化基线（可观测性优先）

- 状态：Accepted（2026-11-10 经维护者确认，随 P7 M1 开工）
- 日期：2026-11-10
- 关联：[公共契约](00-contracts.md)、[ADR-06 Super-Agent Router](06-super-agent-router.md)、
  [P7 实施规格](../specs/p7-production-hardening-ecosystem.md)、
  [P7 验收账本](../acceptance/p7-production-hardening.md)、
  [Roadmap 工程收口口径](../Roadmap.md)
- 代码入口：`stableagent/observability/`（本 ADR 批准的新包；后续 M2–M6 追加 Provider/
  Memory/API 等决策时以勘误或后续决策节追加，不静默改写本节）

## 背景

P6 工程收口后，控制平面已具备 Router、Runtime、监管 CLI 与离线 Beta。P7 要在不破坏
Core / 编排语义的前提下补齐生产能力。规格推荐顺序以 **WP-7.1 可观测性** 为第一刀：
没有稳定事件、脱敏与旁路采集语义，后续成本路由、多 Source 与 API 无法统一审计。

本 ADR 钉死 M1 的实现口径；M2+ 的 Provider/Memory/租户细节另开决策节。

## 约束

1. **工程收口口径**：只看代码完成与离线门禁；真实线上数据不作 DoD。
2. **Core / 编排稳定**：M1 对 `stableagent/core/` 与 `orchestration/service.py` 零 diff。
3. **方向锁**：`observability` 可被 `supervisor` / `orchestration` / `control` /
   `integrations` 调用；**不得** import `stableagent.supervisor`。旧包不得反向依赖。
4. **旁路**：采集失败不得阻止授权、Ticket 状态变更或 effect 重试。
5. **离线门禁**：默认 CI 无网络 / 真实 GitHub / Docker。
6. **依赖**：M1 不新增运行时依赖；不引入 OpenTelemetry SDK。

## 决策

### 1. 新包 `observability/`，与 `authorization_audit` 并存

- `control.AuditWriter` + `authorization_audit` 表继续作为**授权判定**的事实源，不迁移、
  不改列。
- `observability` 提供更广的领域事件（Trigger / Decision / Effect 等）、脱敏、进程内
  指标与 ticket 根 trace 关联。
- 可选地把授权结果镜像为 DomainEvent，不阻塞 M1；授权真相仍以 `authorization_audit` 为准。

### 2. 默认 tenant，完整隔离留 M5

- 每条 DomainEvent **强制** `tenant_id`；常量默认 `"default"`（`DEFAULT_TENANT`）。
- M1 不做多库拆分或多租户 RBAC；字段先占位，避免 M5 再改事件契约。

### 3. 存储：共享 SQLite、append-only `domain_events`

- `ObservabilityStore(Store)` 在同一连接上建 `domain_events` 与 marker
  `p7-observability-v1`；`SupervisorStore` 继承 `ObservabilityStore`，P6 库打开无损。
- 表 append-only：无 UPDATE/DELETE API；查询只读。
- 业务事务提交**之后**再 `emit_safe` 写事件；sink 异常吞掉并计入 `emit_failures`，
  绝不回滚已提交业务。

### 4. 脱敏红线先于入库

- 入库 attrs 必须经 `redaction.redact_mapping`：匹配 token/secret/password/authorization/
  api_key 等键名替换为 `[REDACTED]`；过长字符串截断；疑似本地绝对路径脱敏。
- 完整 prompt、工具原始输出、私有代码默认不得入库；只存 `payload_digest` 与小字段。

### 5. 不引入 OpenTelemetry

- 自研 `DomainEvent` + `EventSink`/`ObservabilityStore` + 进程内 `Metrics` +
  ticket 根 `TraceContext`。
- 未来若需 OTLP 导出，以可选适配器实现 `EventSink`，不得迫使 Core 依赖 OTel。

### 6. M1 最小接线

- Router `receive` / `reevaluate`：事务外发射 `trigger.received`（及有决策时
  `decision.recorded`）。
- `EffectDispatcher`：事务外发射 `effect.attempted` / `effect.succeeded` /
  `effect.failed`。
- 不接满规格全部事件种类；其余在后续里程碑按需追加。

### 7. 多 Provider 统一契约（M2 / WP-7.2）

- OpenAI、Anthropic、Ollama 均只实现 `complete` / `acomplete` / `stream` 与
  `ToolCall`/`usage` 归一化；不得新增第四种 Worker 调用形态。
- Anthropic 走 Messages API；Ollama 钉死 `/api/chat`。离线以 httpx MockTransport
  验收；live smoke 仅 `@pytest.mark.live`。

### 8. ProviderRouter 与选择持久化

- `ProviderRouter.select` 仅在 Assignment **启动前**运行；输出不可变
  `ProviderSelection`（候选、理由、model、price_table_version、fallback 链）。
- 选择经 `merge_provider_selection` 写入 `worker_config["provider_selection"]`；
  禁止在已有 Worker step 内静默换模型。`providers/` 不 import `orchestration`。

### 9. 成本与预算在 Core 外强制

- `control/cost.py`：`PricingTable` + `BudgetPolicy` + `CostLedger`。
- 实际 Provider usage 优先；缺失时用估算并标 `estimated=true`。
- 硬超支不得调用 Provider；软超支返回可观测信号，外层可转审批/暂停（M2 不深改
  ApprovalService）。租户成本规则不得塞进 `AgentLoop` 内部策略。

### 10. Provider 单次调用内层硬超时（批准碰 Core）

- 承接 ADR-02 后果 4 / ADR-04 处置：`ProviderConfig.call_hard_timeout_seconds`
  （`None` = 不包裹，向后兼容）与 `request_timeout_seconds`（httpx）分离。
- `AgentLoop.advance` 用 `asyncio.timeout` 包裹整次 `acomplete`；超时走既有
  `provider_error` → FAILED。外层 Assignment `deadline_at` 语义不变，两层分别可配、
  分别可观测。

### 11. 首个非 GitHub Source = Slack（M3 / WP-7.3）

- `supervisor/sources/`：`github.py`（从 `source.py` 迁出实现）、`slack.py`（fake）。
- `Trigger.source` / `Thread.source` ∈ `{github, slack}`；新增 `TriggerKind.MESSAGE`、
  `ChannelKind.SLACK_MESSAGE`、`ThreadKind.CHANNEL`。
- Slack 仅 fake fixture；无真实 HTTP。飞书/Jira 同构扩展留后续。

### 12. 禁止隐式跨 Source 身份映射

- 引入 `IdentityLink`（显式、可撤销）落 Store；无自动映射路径。
- M3 不做管理 UI，只提供 CRUD + 审计钩子。

### 13. Slack Channel 经 Outbox

- 新增 `EffectKind.SLACK_MESSAGE`；`channels/slack.py` fake writer。
- 保序/幂等沿用 outbox；外部失败可重试并留 receipt。

### 14. McpRegistry 治理

- `integrations/mcp_registry.py`：descriptor（version、transport、tenant allowlist、
  schema digest、health、credential_ref、audit_level、enabled、远程安全字段）。
- 注册/启用/禁用经 operator 回调；discovery ≠ invoke；禁用阻断 pending 调用并审计。

### 15. Memory 两层同表，独立 Store（M4 / WP-7.4）

- `stableagent/memory/`：`MemoryStore(ObservabilityStore)` + marker `p7-memory-v1`；
  **不**加深 `SupervisorStore` 继承链。
- `memories` 行以 `layer ∈ {short, long}` 区分：short = Ticket/Assignment scope；
  long = tenant（可选 repository）scope。只存 policy 批准后的摘要/事实 + digest。
- 与 checkpoint 的 `MemoryCheckpointStore`（进程内 run 快照）命名隔离，勿混用。

### 16. 写入必经 MemoryPolicy；检索 = 授权 + 词法 baseline

- `MemoryPolicy`：允许 origin、分类上限、同 scope digest 去重、TTL、容量、敏感拒绝/
  脱敏。模型/MCP 无直写旁路。
- `LexicalRetriever`：确定性 token overlap；无向量库、无在线 embedding。跨
  tenant/scope 拒绝；过期与已 erase 不可见；结果带 provenance 与 token 预算裁剪。

### 17. 删除可验证；失败降级不阻断 Ticket

- `erase` 软删 + `EraseReceipt`；索引同步剔除；发射 `memory.erased`。
- `MemoryService.retrieve/write` 吞异常 → 空结果 / 跳过写入并记 `memory.degraded`；
  绝不阻断 Ticket。

### 18. Worker 注入不改 Core / 编排签名

- `inject_into_messages` 供 Context 侧可选注入；`core/loop.py` 与
  `orchestration/service.py` 零签名改动。`MemoryConfig` 仅兼容扩展字段。

## 被否决方案

- **用 OpenTelemetry 替换自研事件**：增加运行时依赖与学习成本，违背 Core 轻量。
- **把 DomainEvent 写入改到业务同一事务且失败即回滚**：违反旁路约束。
- **扩展 `authorization_audit` 承载全部领域事件**：破坏授权表语义，难以后续裁剪。
- **M1 引入完整多租户**：范围蔓延；字段默认值足够向前兼容。
- **在 Worker step 中途无记录换模型**：破坏可重放与审计。
- **把预算判定塞进 AgentLoop 内部**：污染 Core；预算属外层/Assignment policy。
- **跨 Source 隐式 actor 映射**：身份混淆与越权风险。
- **把知识 Memory 塞进 AgentState 或改 AgentLoop**：污染 Core；Memory 是受控服务。
- **M4 引入在线 embedding / 向量库**：破坏离线门禁与确定性评测。
- **隐式 short→long 提升**：绕过 Policy 与审批边界。

## 后果与代价

- `SupervisorStore` 继承链多一层；打开旧库会多一张表与一个 marker。
- 事件种类初期不全，仪表盘需容忍缺失 kind。
- 进程内 Metrics 不跨进程聚合；多实例需外置 sink（P7 后续）。
- Core 增加可选硬超时字段；默认 `None` 保持旧行为。
- 价格表版本需运维维护；估算误差必须显式标记。
- Source/Channel kind 扩展后旧客户端需容忍新枚举值。
- Memory 独立 Store 与 Supervisor 同库需各自打开或共享路径；调用方注入 `MemoryService`。
- 词法检索召回弱于向量；M4 以离线评测与权限正确性优先。

## 测试与证据

M1–M4 实测门禁证据回填在
[P7 验收账本](../acceptance/p7-production-hardening.md)；本 ADR 不复制会腐坏的读数。

## 关联文档

- [P7 Spec WP-7.1–7.4](../specs/p7-production-hardening-ecosystem.md)
- [可观测性指南](../guides/observability.md)（M1）
- [Provider 路由指南](../guides/provider-routing.md)（M2）
- [Sources / MCP 指南](../guides/sources-mcp.md)（M3）
- [Memory 指南](../guides/memory.md)（M4）
- [工程收口口径](../Roadmap.md)


