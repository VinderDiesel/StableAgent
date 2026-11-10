# P7 生产化与生态扩展 验收账本与实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏 Core / 编排语义的前提下，交付可观测、多 Provider、多 Source、Memory、
可选 API 与部署能力；工程收口只认代码完成与离线可靠性
（[Roadmap 工程收口口径](../Roadmap.md)）。

**Architecture:** 新增 `stableagent/observability/`（M1）；后续按 Spec 子版本扩展
ProviderRouter、Source/MCP、Memory、FastAPI/tenancy。`ObservabilityStore(Store)` 共享
SQLite；`SupervisorStore` 继承之。`observability` 不得 import `supervisor`。

**Tech Stack:** Python 3.10+、Pydantic v2、SQLite、pytest / mypy / ruff；M1 无新运行时依赖。

**Spec:** [P7 实施规格](../specs/p7-production-hardening-ecosystem.md)
**ADR:** [ADR-07](../design-decisions/07-production-hardening.md)

## Global Constraints

- Python `>=3.10`；行宽 100；`ruff check` / `ruff format --check` / `mypy` 全绿。
- M1：`stableagent/core/` 与 `orchestration/service.py` 零 diff。
- 方向锁：`observability` ← supervisor/orchestration/control/integrations；observability
  不得 import supervisor。
- 采集旁路：emit 失败不挡业务事务。
- 工程收口不作真实线上数据要求。
- 全量测试与 CI 默认无网络 / 真实 GitHub / Docker。
- 门禁命令统一带 `env -u NO_PROXY -u no_proxy`；读数落 `/tmp/p7-*.txt` 再读回。

---

## 0. P7 出口不变量（工程；违反即不收口）

| 编号 | 不变量 |
|---|---|
| I1 | DomainEvent 强制 `tenant_id`；默认 `"default"` |
| I2 | 入库 attrs 经脱敏；secret/token 类键不得明文 |
| I3 | `emit_safe` 失败只计指标，不抛穿、不回滚已提交业务 |
| I4 | `domain_events` append-only；与 `authorization_audit` 并存 |
| I5 | observability 包不 import supervisor |
| I6 | 默认 CI 离线可绿 |

## 1. 里程碑

### M1：可观测性与审计基线（规格 WP-7.1；ADR-07）

> **状态：M1 收口（2026-11-10）。** 包 `stableagent/observability/`；Router /
> EffectDispatcher 事务外 `emit_safe`；FOCUS 35→40。

- [x] **M1-1** ADR-07 与本账本落盘；状态面指向 P7 Active。
- [x] **M1-2** `DomainEvent` 模型与 `redaction`；脱敏单测。
- [x] **M1-3** `ObservabilityStore`：`domain_events` + `p7-observability-v1`；P6 库无损；
      `SupervisorStore` 继承之。
- [x] **M1-4** `DomainEventEmitter` + Metrics + Trace；`emit` / `emit_safe`。
- [x] **M1-5** 接线：Router receive/reevaluate、EffectDispatcher 路径；
      `build_runtime` 默认注入 emitter。
- [x] **M1-6** CI FOCUS 扩容至 40；`docs/guides/observability.md`。
- [x] **M1-7** M1 出口：见下方出口读数。

### M2：多 Provider 与成本路由（规格 WP-7.2；ADR-07 决策 7–10）

> **状态：M2 收口（2026-11-10）。** Anthropic/Ollama + Router + cost + 内层硬超时。

- [x] **M2-1** ADR-07 追加决策 7–10；本账本 M2 TDD；状态面。
- [x] **M2-2** Anthropic / Ollama Provider + fake transport 协议测。
- [x] **M2-3** `control/cost.py`：价格表、软/硬预算、ledger；硬超支零调用。
- [x] **M2-4** `ProviderRouter` + `merge_provider_selection` 写入 `worker_config`。
- [x] **M2-5** `call_hard_timeout_seconds` + `AgentLoop` `asyncio.wait_for`。
- [x] **M2-6** 最小接线、指南、FOCUS、出口门禁。

### M3：多 Source / Channel 与 MCP 治理（规格 WP-7.3；ADR-07 决策 11–14）

> **状态：M3 收口（2026-11-10）。** Slack fake Source + McpRegistry + IdentityLink。

- [x] **M3-1** ADR-07 决策 11–14；本账本 M3 TDD；状态面。
- [x] **M3-2** 模型扩展 + GitHub Source 拆分 + SlackFakeSource。
- [x] **M3-3** Slack Channel/Effect + McpRegistry + IdentityLink。
- [x] **M3-4** 指南、FOCUS、出口门禁。

### M4：Memory 与数据生命周期（规格 WP-7.4；ADR-07 决策 15–18）

> **状态：实施中。** 独立 `MemoryStore` + Policy + 词法检索 + erase/降级。

- [x] **M4-1** ADR-07 决策 15–18；本账本 M4 TDD；状态面。
- [x] **M4-2** model + `MemoryStore`（`p7-memory-v1`）+ `MemoryPolicy` + short/long facade。
- [x] **M4-3** `LexicalRetriever` + `MemoryService`（降级 / erase / inject）+ 领域事件。
- [x] **M4-4** 离线评测、指南、FOCUS、出口门禁。

### M5：FastAPI、Transport、监管 UI 与多租户（规格 WP-7.5）

- [ ] optional `server` extra、SSE、RBAC、真实租户隔离。

### M6：部署、auto 与终点收口（规格 WP-7.6）

- [ ] runbook、auto 开关离线证明、终点门禁与文档。

## 2. P7 退出清单（规格 §5；工程收口）

- [x] ProviderRouter 多 Provider，成本/预算/选择/fallback 可追溯且不污染 Core（M2）。
- [x] 新 Source/Channel/MCP 经 Trigger/Outbox/Policy，tenant 隔离与幂等（M3：Slack fake +
      McpRegistry + IdentityLink；飞书/Jira 同构留后续）。
- [x] Memory 具备 scope、授权、生命周期、删除、降级与离线质量评估（M4）。
- [x] 审计、指标、trace、SLO 与脱敏覆盖 Worker→effect（M1：Trigger/Decision/Effect
      旁路事件 + 脱敏 + 指标 + ticket trace；SLO 配置化留后续）。
- [ ] FastAPI、SSE、RBAC 与最小监管 UI 可部署且不绕过服务边界。
- [ ] 多租户资源彼此隔离。
- [ ] 部署/备份/恢复/升级/灾难与 auto 回滚：runbook + 离线证明。
- [ ] auto 规则：范围、阈值、审计、回滚代码与离线测试（默认关闭）。
- [ ] 全库覆盖 ≥80%，mypy strict，ruff 0 warning，CI benchmark/安全门禁。
- [ ] 文档、API Reference、示例与 CHANGELOG 完整。

## 3. 阶段验证命令

```bash
env -u NO_PROXY -u no_proxy .venv/bin/ruff check stableagent tests examples benchmarks
env -u NO_PROXY -u no_proxy .venv/bin/ruff format --check stableagent tests examples benchmarks
env -u NO_PROXY -u no_proxy .venv/bin/mypy
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q
```

---

## 4. M1 任务级计划（WP-7.1）

### Task 1–7：已合并落地（2026-11-10）

实现与文档同一次工程收口提交；逐 Task 勾选见 §1 M1。

### M1 出口读数（2026-11-10，落盘 `/tmp/p7-m1-*.txt`）

- 本地（Cursor sandbox 无法 `git init` hooks，故忽略 worktree/github 示例相关用例）：
  `845 passed, 6 skipped`；全库覆盖 **97.35%**。
- 被忽略仅因沙箱：`test_worktree.py`、`test_worktree_concurrency.py`、
  `test_guarded_ticket_recovery.py`、`test_github_example.py`（`.git/hooks` Operation
  not permitted）。正式门禁以远端 CI 为准。
- `ruff check`：All checks passed；`mypy`：Success: no issues found in 80 source files。
- FOCUS 新文件：`events/redaction/emitter/store/metrics` 均 ≥95%（实测 100/100/100/100/95.8）。
- `git diff 234f620 -- stableagent/core/` 与 `orchestration/service.py` 工作区相对 HEAD：
  零 diff（M1 未改 service.py；outbox 仅增可选 emitter 参数）。

---

## 5. M2 任务级计划（WP-7.2）

### Task 1–6：已合并落地（2026-11-10）

见 §1 M2 勾选。

### M2 出口读数（2026-11-10，`/tmp/p7-m2-*.txt`）

- 本地（沙箱忽略 git worktree 相关用例）：`869 passed, 8 skipped`；覆盖 **97.10%**。
- `ruff check` 目标文件通过；`mypy`：Success: no issues found in 84 source files。
- FOCUS 42 文件；`control/cost.py` 93.9%、`providers/routing.py` 98.4%、`core/loop.py` 97.1%。
- `orchestration/service.py` 未改签名；loop 哈希基线更新为 P7 M2（ADR-07 决策 10）。

---

## 6. M3 任务级计划（WP-7.3）

### Task 1–4：已合并落地（2026-11-10）

见 §1 M3 勾选。

### M3 出口读数（2026-11-10，`/tmp/p7-m3-*.txt`）

- 本地（沙箱忽略 git worktree 相关用例）：`878 passed, 8 skipped`；覆盖 **96.83%**。
- `ruff check` 通过；`mypy`：Success: no issues found in 90 source files。
- FOCUS 45 文件；`mcp_registry` 94.5%、`sources/slack` 96.5%、`channels/slack` 91.4%。
- `SUPERVISOR_SCHEMA_VERSION = p7-supervisor-v1`（identity_links 表）。

---

## 7. M4 任务级计划（WP-7.4）

### Task 1：ADR / 账本 / 状态面（M4-1）

- ADR-07 追加决策 15–18；状态面指向 M4；本账本展开勾选。

### Task 2：Store / Policy / facade（M4-2）

- `MemoryRecord` / scope / classification；`MemoryStore` DDL + 跨 tenant 拒绝。
- `MemoryPolicy`（origin、分类、去重、TTL、容量）；short/long 薄 facade。

### Task 3：检索 / Service / 事件（M4-3）

- `LexicalRetriever` + 授权过滤 + token 裁剪；`MemoryService` 降级与 erase receipt；
  `DomainEventKind` 追加 memory.*；`inject_into_messages`。

### Task 4：评测 / FOCUS / 出口（M4-4）

- 离线评测集；`docs/guides/memory.md`；FOCUS 45→48；全量离线门禁。

### M4 出口读数（2026-11-10，`/tmp/p7-m4-*.txt`）

- 本地（沙箱忽略 git worktree / github example 相关用例）：`903 passed, 8 skipped`；覆盖 **96.80%**。
- `ruff check`：All checks passed；`mypy`：Success: no issues found in 99 source files。
- FOCUS 48 文件；`memory/store` 100%、`memory/policy` 95.9%、`memory/retrieval` 90.7%。
- marker `p7-memory-v1`；与 checkpoint `MemoryCheckpointStore` 隔离。
