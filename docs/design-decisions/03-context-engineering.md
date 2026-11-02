# ADR-03：Worker Context Engineering 与预算治理（P3）

- 状态：Accepted
- 日期：2026-11-02
- 关联：[公共契约](00-contracts.md)、[ADR-01 Loop](01-loop-runtime.md)、[ADR-02 Checkpoint](02-checkpoint.md)、[P3 验收账本](../acceptance/p3-context-engineering.md)、[P3 基准记录](../evidence/p3-context-benchmark-2026-11-02.md)
- 代码入口：`stableagent/context/{tokens,compression,capability,budget,engine}.py`、`stableagent/core/agent.py`

## 背景

Worker 的上下文成本由对话历史与工具 schema 共同构成。历史无限增长会增加成本并降低模型注意力；将所有工具都注入模型也会在工具多时淹没有效能力。直接在 `AgentLoop` 中插入压缩、权限或预算异常会破坏 P1/P2 的可重入 loop、终止事件与 checkpoint 恢复契约。

P3 需要在不改 `core/loop.py` 的前提下，提供确定性的 token 估算、历史压缩、动态能力视图、预算治理、可观测报告和崩溃恢复等价性。

## 约束

1. `AgentLoop` 只认识 `ContextAssembler` 与 `ToolSelector` 两个接缝；它不认识 checkpoint、预算、Ticket 或 Router。
2. `Event` 仍只有既有类型；每次 run 仍恰有一个 `done` 或 `error` 并位于流末尾。
3. `AgentState` 是 Worker run 的唯一持久化快照。P2 起公共类型只能兼容扩展。
4. P3 测试、benchmark 和默认路径不得依赖网络、真实模型、墙钟或额外运行时依赖。
5. 压缩不得拆开 assistant tool call 与对应 tool result；丢失已使用工具的后续可用性同样不可接受。

## 决策

### 统一 ContextEngine，但不修改 loop

`ContextEngine` 是唯一的 P3 编排对象。`EngineAssembler` 把 `assemble()` 适配给 loop，`ContextEngine.select()` 作为 tool selector；`Agent._pipeline()` 在段入口调用 `fold()` 和 `preflight()`，并在每步后调用 `observe()`。

同一引擎实例同时决定外层预算和 Provider 实际 payload，避免“外层判定一套、loop 发送另一套”的裂缝。默认 `Config()` 不创建 ContextEngine，保持 P2 的消息、工具顺序、事件和性能路径。

### token 采用可注入估算，Provider usage 只做事后校准

`TokenCounter` 计算实际发送的 messages 与 tool specs；默认实现对 CJK/全角和非 CJK 分域估算，并对 schema 使用稳定 JSON 序列化。Provider 的 `usage.prompt_tokens` 以每步增量写入 `ContextReport.observed_prompt_tokens`，但不反馈到后续决策。

估算值写入 `ContextReport.estimated_prompt_tokens`，工具裁剪值写入 `tools_dropped_tokens`。不向 `AgentState.usage` 复制相同的上下文计量，避免和 Provider 累计 usage、checkpoint 或恢复段产生双重事实源。

### 压缩结果以摘要标记消息持久化

`ContextEngine.fold()` 在新 run、恢复、重试等段入口把压缩结果写回 `AgentState.messages`。摘要的唯一载体是 `Message(role="system", name="context_summary")`，而不是 `AgentState.context_summary` 字段。

这使 snapshot 自带恢复所需的摘要文本。恢复不会再次调用可能非确定性的摘要器，因此恢复后 Provider 输入可与不中断运行保持一致。对已经折叠的 messages 再次 fold 必须幂等。

### 压缩与能力视图的安全策略

- `truncation` 固定保留 system、首个 user 任务和最近窗口；切点向前回退以保全 tool call/result 配对。
- `summary` 用默认离线抽取式摘要器，也接受注入式摘要器。
- `hybrid` 固定顺序为旧工具结果摘录 → 摘要 → 截断。
- `static` 工具视图保留 registry 原序，确保默认行为不变。
- `relevance` 以确定性词面规则筛选，英文忽略短词/停用词，CJK 单字可匹配；动态结果按名称排序。
- 已在本 run 使用的工具构成 resident set，始终保留，即使超过 `max_visible_tools`；无命中时回退全量工具。

### 预算动作与不对称终止语义

预算对象是**实际将发送给 Provider 的组装后消息加实际注入的工具 specs**，边界为 `after <= max_context_tokens`。

- `compress`：先收缩历史，再收缩工具视图。
- `degrade`：先收缩工具视图，再收缩历史。
- `reject`：段入口判定超限时，`Agent._pipeline()` 产生唯一 `budget_exceeded` error，不调用 Provider、不重试、不静默降级。

运行中发现超限时，loop 已经完成当前 step 的工具副作用但尚未交还控制权。P3 不尝试中途终止该 step；对于无解的 reject，在安全步边界进入 `PAUSED` 并持久化，恢复时若入口仍超限则确定性失败，避免 pause/resume 空转。

### Evidence 的兼容迁移

`Evidence` 移至 `stableagent.tools.evidence`，`stableagent.tools.registry` 保留同一类对象的重导出，顶层包重导出 canonical 类型。P3 **不**新增 `step`、`origin` 或 `parent_call_id`：这些字段即使默认 `None` 也会改变默认 `tool_result` 事件的序列化载荷，违反 P2 金标准。它们延后到 P4 的 Ticket/Assignment 领域记录中实现。模型强制 `ok == (error is None)`，失败而未给出错误文本时填入确定性说明，避免 tool message 变为 `"None"`。

## 被否决方案

| 方案 | 拒绝原因 |
|---|---|
| 在 `core/loop.py` 内联压缩、筛选或预算 | 违反 P1 留出的接缝与 P2 “可靠性在 Agent 外层”的边界，增大后续 Router/Ticket 耦合。 |
| 让 assembler/selector 抛 `BudgetExceeded` | 该调用在 Provider try/except 外，会绕过规范 `error` 事件和终止事件居尾契约。 |
| 引入 `tiktoken` 作为必选依赖 | 模型/编码器专属、增加运行时依赖并削弱离线可移植性；真实 usage 已可事后校准。 |
| 仅在进程内缓存摘要 | 崩溃恢复需要重算，注入式摘要器会使恢复 Provider 输入不等价。 |
| 摘要同时写 `AgentState.context_summary` 与 messages | 两份可变事实会漂移，还需额外迁移；标记消息已能被 snapshot 无损保存。 |
| 使用 LLM 作为唯一质量评审 | 质量门禁将依赖网络、模型漂移和非确定性；P3 使用固定事实保留与最终答案断言。 |
| 为每步新增 context Event | 会扩张公共事件契约并增加流噪音；`agent.context_reports` 的只读副本足以提供观测。 |

## 后果与代价

- P3 保持 Core 的轻量，但 token 数是估算值，真实模型差异需要部署后显式采集。
- 动态工具视图优先 resident 正确性，可能超过可见工具上限。
- `ContextReport` 属于 Agent 进程内观测，不是第二套 checkpoint state；P4 如需长期报告应映射为 Ticket/Assignment Report。
- 压缩在段入口持久化且运行中计划下一步生效，不能对已经执行的工具副作用实施“紧急预算刹车”。
- Evidence 追溯字段为 P4 Ticket/Assignment 调用链预留，不应提前承担跨 Ticket 生命周期。

## 证据与后续

- 默认路径与 P2 收口金标准逐字节等价：`tests/unit/test_l4_default_equivalence.py`。
- 压缩、能力视图、预算与恢复等价：`tests/unit/test_compression.py`、`test_capability.py`、`test_context_engine.py`。
- Evidence 兼容与不变式：`tests/unit/test_evidence.py`。
- 固定离线基准和 checkpoint 影响：[P3 基准记录](../evidence/p3-context-benchmark-2026-11-02.md)。
- P4 必须将 Worker `Event` / `ContextReport` 映射为长期 Ticket/Assignment Report，但不得把 Ticket、Scheduler、Outbox 或权限逻辑加入 ContextEngine。
