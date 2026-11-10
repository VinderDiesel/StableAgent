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

## 被否决方案

- **用 OpenTelemetry 替换自研事件**：增加运行时依赖与学习成本，违背 Core 轻量。
- **把 DomainEvent 写入改到业务同一事务且失败即回滚**：违反旁路约束。
- **扩展 `authorization_audit` 承载全部领域事件**：破坏授权表语义，难以后续裁剪。
- **M1 引入完整多租户**：范围蔓延；字段默认值足够向前兼容。

## 后果与代价

- `SupervisorStore` 继承链多一层；打开旧库会多一张表与一个 marker。
- 事件种类初期不全，仪表盘需容忍缺失 kind。
- 进程内 Metrics 不跨进程聚合；多实例需外置 sink（P7 后续）。

## 测试与证据

M1 实测门禁、脱敏与旁路失败证据回填在
[P7 验收账本](../acceptance/p7-production-hardening.md)；本 ADR 不复制会腐坏的读数。

## 关联文档

- [P7 Spec WP-7.1](../specs/p7-production-hardening-ecosystem.md)
- [可观测性指南](../guides/observability.md)（M1 交付）
- [工程收口口径](../Roadmap.md)
