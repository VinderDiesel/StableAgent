# 可观测性指南（P7 M1）

## 定位

`stableagent.observability` 提供旁路领域事件、脱敏入库、进程内指标与以 Ticket 为根的
轻量 trace 关联。授权判定真相仍在 `control.AuditWriter` / `authorization_audit`
（[ADR-07](../design-decisions/07-production-hardening.md)）。

## 事件种类（M1）

| kind | 何时 |
|---|---|
| `trigger.received` | Router `receive` / `reevaluate` 事务提交后 |
| `decision.recorded` | 同上，每个 decision_id 一条 |
| `effect.attempted` | EffectDispatcher claim 后 |
| `effect.succeeded` | handler 成功 settle 后 |
| `effect.failed` | 可延期 / 永久 / 可重试失败后 |

每条事件强制 `tenant_id`（默认 `"default"`），attrs 经脱敏后入库，并带
`payload_digest`。

## 脱敏红线

- 键名匹配 token / secret / password / authorization / api_key → `[REDACTED]`
- 过长字符串截断（默认 200）
- 疑似 `/Users`、`/home`、`/root` 绝对路径 → `[REDACTED_PATH]`

## 旁路语义

业务事务**提交之后**调用 `DomainEventEmitter.emit_safe`。sink 异常只增加
`emit_failures` 计数，不抛穿、不回滚已提交业务。

## 查询

```python
from stableagent.observability.audit import list_ticket_events
from stableagent.observability.store import ObservabilityStore

events = store.list_domain_events(ticket_id="tk-…", limit=100)
# 或
events = list_ticket_events(store, "tk-…")
```

`domain_events` 为 append-only；marker `p7-observability-v1`。P6 库打开后无损加表。

## 运行时注入

`build_runtime(...)` 默认构造 `DomainEventEmitter(store, …)` 并注入 Router 与
EffectDispatcher。测试可传入自定义 emitter 或依赖 `emit_safe` 旁路行为。
