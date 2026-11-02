# P3 详细实施规格：Worker Context Runtime

> **阶段定位**：完成单个 StableAgent Worker 的上下文治理闭环。P3 不创建 Ticket、Router、Source、GitHub 集成、MCP Server、Sandbox 或长期 Memory。它只决定 Worker 在一次 `AgentState` run 中向 Provider 发送哪些消息和工具、如何在预算内运行、如何保存可复现证据。
>
> **开始条件**：P2 的 checkpoint / recovery 已通过其既有质量门禁。
>
> **完成条件**：本规格所有工作包和验收项完成，`docs/acceptance/p3-context-engineering.md`、README、CHANGELOG、ADR 与实际源码一致，才可启动 P4。

## 1. 目标与可交付结果

P3 必须使 Worker 具备以下可观察能力：

1. 以可注入、离线确定性的方式估算本次 Provider 请求的 token 成本。
2. 在不破坏工具调用配对的前提下压缩历史消息。
3. 在不丢失本 run 已使用工具的前提下缩小工具视图。
4. 在明确的 token 上限下执行 `compress`、`degrade` 或 `reject`。
5. 将上下文决策、估算值、实际 Provider usage 和质量保留率记录为可验证证据。
6. 在压缩、暂停、重试和 checkpoint 恢复后保持确定的状态与事件语义。

P3 的对外交付物：

- `stableagent/context/tokens.py`、`compression.py`、`capability.py`、`budget.py`、`engine.py`。
- 与现有 `Agent`、`ContextAssembler`、`ToolSelector` 的无侵入接线。
- `tools/evidence.py` 的位置决策和相应兼容实现，或 ADR 中明确的“不迁移”结论。
- `benchmarks/context_compression_benchmark.py` 和工具爆炸 benchmark 场景。
- ADR-03、更新后的 P3 DoD、README、CHANGELOG、博客初稿和 CI context 覆盖率门禁。

## 2. 范围、非目标与硬边界

### 范围

- 消息历史组装、截断、摘要与混合压缩。
- ToolRegistry specs 的动态筛选与 token 计量。
- `max_context_tokens` 的 Worker 请求级预算治理。
- 上下文报告、usage 校准、质量保留 benchmark。
- Evidence 追溯模型的最小兼容扩展。

### 明确非目标

- 不实现跨 Ticket 的记忆、全局知识检索或长期 Memory。
- 不实现任务分解、DAG、并发调度或 SubAgent 编排。
- 不实现 GitHub/飞书 Source、Router、Policy、Mandate、Outbox 或 Web 面板。
- 不实现 Docker sandbox、权限审批或 MCP Server。
- 不依赖 `tiktoken`、在线摘要模型、真实网络或任意 secrets 作为测试前提。

### 不可破坏的契约

| 契约 | 实施要求 |
|---|---|
| `core/loop.py` | P3 不修改此文件。只能使用 `ContextAssembler.assemble()` 与 `ToolSelector` 接缝；预算拒绝由 `Agent` 外层处理。 |
| `Event` | 每次 run 仍恰有一个 `done` 或 `error`，且必须位于事件流末尾。默认不新增 context event。 |
| `AgentState` | 仍是单 Worker run 的唯一快照。不得放入 ContextEngine 的私有可变状态、跨 run 缓存或全局预算。 |
| tool pair | 任何 Provider 输入中，含 `tool_calls` 的 assistant 消息必须保留全部对应 `tool` 消息。 |
| 默认配置 | `Config()` 不能创建 ContextEngine，Provider payload、工具顺序、事件序列和 P2 行为逐条一致。 |
| 恢复 | 压缩产物必须可从 checkpoint 重建或直接持久化，不能在恢复时额外调用摘要器。 |

### 当前实现事实与文档纠正

当前 P3 实现以 `Message(role="system", name="context_summary")` 作为摘要持久化载体，并由 `ContextEngine.fold()` 写入 `AgentState.messages`。这避免 `AgentState.context_summary` 与消息中的摘要发生双写漂移。

实施 Agent 必须先统一事实源：**采用“摘要标记消息是唯一快照事实源”的方案**。因此：

- 不新增第二份 `AgentState.context_summary` 字段，除非 ADR-03 明确提出迁移、同步规则和兼容测试。
- 修正 `docs/acceptance/p3-context-engineering.md`、CHANGELOG 等仍宣称该字段存在的表述。
- 所有测试以实际 snapshot payload 含 `context_summary` 标记消息为验收事实。

## 3. 目标架构

```text
Agent._pipeline（外层）
  ├─ ContextEngine.fold(state)                # 将确定性压缩结果写入快照
  ├─ ContextEngine.preflight(state, specs)    # 在调用 loop 前判定预算
  ├─ AgentLoop.drive(state)                   # 不修改
  │    ├─ EngineAssembler.assemble(messages)
  │    └─ ContextEngine.select(specs, state)
  └─ ContextEngine.observe(after_state)       # 记录 Provider 实际 usage 增量
```

`ContextEngine` 是唯一的 P3 编排对象，同一实例同时提供外层 preflight 和 loop 适配器。禁止外层与 loop 分别创建计数器、压缩器或能力筛选器，否则“判定量”和“实际发送量”会漂移。

### 预算口径

```text
estimated_context_tokens = count_messages(actual_provider_messages)
                         + count_tools(actual_provider_tool_specs)
```

- `before`：未压缩、未裁工具时的消息和全部 specs。
- `after`：当前计划下实际将发给 Provider 的消息和 specs。
- `prompt_tokens`：Provider response usage 的真实值，按步增量记录，仅用于事后比较，不反馈进决策。
- 所有估算必须是确定性的；工具 schema 序列化必须定序。

### 策略语义

| 策略 | 入场（新 run、resume、recover、retry） | 运行中发现超限 |
|---|---|---|
| `compress` | 先收缩历史；可达标则以新计划启动 | 当前步完整执行，下一个步边界使用新计划 |
| `degrade` | 先收紧工具视图，再收缩历史 | 当前步完整执行，下一个步边界使用新计划 |
| `reject` | 外层产生唯一 `error`，不进入 `AgentLoop.drive()`、不调用 Provider、不触发 retry | 不可中断已经发生副作用的 step；在下一个步边界暂停并持久化，恢复时仍无法装入则外层产生 `error` |

计划搜索必须有下限并有限终止。历史保留下限和工具可见下限必须是命名常量，不可依赖隐含空列表行为。

## 4. 实施工作包

按顺序实施。每个工作包先新增或更新测试，再修改实现；完成本包的验证命令后才进入下一包。

### WP-3.1：冻结事实、修正 P3 DoD 与默认回归

**文件**：`docs/acceptance/p3-context-engineering.md`、`tests/unit/test_context_engine.py`、必要时 `tests/unit/test_three_shapes.py`。

1. 逐项核对 P3 DoD 与实际代码，不允许用历史通过次数代替当前证据。
2. 将摘要事实源修正为 `AgentState.messages` 的 `context_summary` 标记消息。
3. 新增 `test_p3_default_config_stream_identical_to_p2`：使用同一个脚本 Provider、Registry 和任务，对比 P3 默认 Agent 与无 P3 接线基准的 Provider messages、tools、事件类型、step、data 与最终 state。
4. 确认默认 `Config()` 不创建 ContextEngine，且 `context_reports == []`、不发额外事件。
5. 为 ContextEngine、budget、capability 的每个公开数据模型写明默认路径语义。

**验收**：默认配置满足逐条事件与 payload 等价；DoD 中的“已完成”仅指存在当前可执行证据的事项。

### WP-3.2：Token 计量与校准报告

**文件**：`stableagent/context/tokens.py`、`stableagent/context/budget.py`、`stableagent/context/engine.py`、`stableagent/core/agent.py`、相关单测。

1. 保持 `TokenCounter` 三方法协议和运行时早失败校验。
2. 保持 CJK/全角与非 CJK 的分域估算，工具 schema 使用稳定 JSON 序列化。
3. 每一次 Provider 请求在 `ContextReport` 中记录实际 payload 的 `estimated_prompt_tokens`，并在能力视图开启时记录 `tools_dropped_tokens`。
4. Provider 回报 `prompt_tokens` 时，按 step 增量记录到 `ContextReport.observed_prompt_tokens`；该值只用于事后校准，不能反向影响预算决策。
5. `AgentState.usage` 继续保存 Provider 原始累计 usage；不得复制 `context_tokens_est` 或 `tools_tokens_dropped` 形成第二份可漂移快照事实。
6. checkpoint → recover 后，fold 与预算决策不得因为上一段的 observed usage 重复计数或改变。

**验收**：注入 FlatCounter 可改变预算结果；每步估算、裁掉 token 与真实 usage 可由 ContextReport 核对；Provider 谎报 usage 也不改变决策序列。

### WP-3.3：压缩与快照恢复

**文件**：`stableagent/context/compression.py`、`assembly.py`、`engine.py`、`tests/unit/test_compression.py`、`tests/integration/test_crash_recovery.py` 或新的参数化集成测试。

1. `truncation` 保留所有 system 消息、首个 user 任务和最近窗口，并在边界向前回退避免孤儿 tool result。
2. `summary` 使用默认离线抽取式摘要器；注入摘要器可替换，但其输出必须在 snapshot 中持久化。
3. `hybrid` 固定执行旧工具结果摘录 → 摘要 → 截断；用测试锁住顺序与最近消息原文不变。
4. `ContextEngine.fold()` 只在段入口调用，并对已经含摘要标记消息的 state 幂等。
5. 增加实际 SQLite 崩溃恢复用例：在摘要已产生后杀死进程，恢复后的事件、Provider 输入和最终 state 必须与不中断执行的尾段一致。
6. 为每次组装记录 `orphans == 0`；发现非零时测试失败并在报告中保留事实。

**验收**：所有策略对 tool call/result 配对安全；压缩恢复不多调摘要器；分段 fold 与一次性 fold 的结果满足 ADR-03 规定的合流性。

### WP-3.4：动态能力视图

**文件**：`stableagent/context/capability.py`、`core/agent.py`、`tests/unit/test_capability.py`。

1. `static` 返回 registry 原顺序的完整列表，不排序、不计“节省”。
2. `relevance` 使用确定性词面策略：英文有效词长度至少 3，排除停用词；CJK 单字可参与匹配；匹配 name 与 description。
3. 动态筛选结果按工具名字典序排序；不得依赖 dict 或 set 插入顺序。
4. resident set 从 assistant `tool_calls` 和 `role="tool".name` 共同提取。resident 永远保留，即使超过 `max_visible_tools`。
5. 无命中时回退完整列表，并令 `CapabilityReport.fallback=True`。
6. 调用方显式 `tool_selector` 优先；此时不生成误导性的 capability report。
7. 将实际被删 specs 的 token 计数写入 `ContextReport.tools_dropped_tokens`，保持 loop 快照不受上下文观测污染。

**验收**：至少覆盖英文、CJK、停用词退化、空 registry、反向注册顺序、resident 超上限、显式 selector 优先、恢复后 resident 延续和 200 工具压力样本。

### WP-3.5：预算治理与终止语义

**文件**：`stableagent/context/budget.py`、`engine.py`、`core/agent.py`、`tests/unit/test_context_engine.py`。

1. `ContextEngine.preflight()` 必须使用该 plan 实际 assemble 与 select 后的 payload 计数。
2. 边界为 `after <= max_context_tokens`，恰等于上限不得采取动作。
3. `compress` 阶梯先减 `keep_recent_messages`，到下限后才收窄工具。
4. `degrade` 阶梯先收窄工具，之后才减少历史；两种路径要有独立、可读的 `levels_tried`。
5. 任何阶梯都必须有固定下限、有限步骤和 exhausted 结果。
6. 入场 `reject` 必须：仅输出 `error`、状态 FAILED、保存终态 checkpoint、Provider 调用次数为 0、不会进入 P2 retry。
7. 运行中 reject 耗尽必须在安全步边界转为带 `budget_exceeded` 原因的 PAUSED 状态；下一次恢复如果仍无法满足入口预算，应输出标准 `budget_exceeded` error，不能形成暂停循环。
8. `max_steps` 与 context budget 是相互独立的终止原因，各有测试。
9. `context_reports` 是只读复制；默认不扩展 `EventType`。

**验收**：每种策略至少有“可达标”和“耗尽”两类离线用例；reject 用例证明无 Provider 调用；所有 ContextReport 可解释 before、after、action、levels、工具数、orphans、estimated 和 observed 值。

### WP-3.6：Evidence 最小演进

**决策点**：在开始修改前写 ADR-03 的 E1 决定，并选定下列唯一方案。

- **推荐方案 A**：新建 `stableagent/tools/evidence.py`，移动 `Evidence` 与相关纯数据类型，`registry.py` 保留重导出，旧导入路径保持可用。
- **方案 B**：不迁移文件，但 ADR-03 说明 Evidence 已是 ToolRegistry 的稳定边界，Roadmap 与 DoD 删除错误的文件交付要求。

如采用方案 A：

1. 保持 `Evidence` 的既有序列化字段；P3 不新增 `step` / `origin` / `parent_call_id`，因为默认 `None` 也会改变 P2 金标准中的 `tool_result` payload。
2. 将 P4 所需的调用链追溯字段放入 Ticket / Assignment 领域记录，而不是提前塞入单 Worker Evidence。
3. 保持现有成功/失败归一化和序列化语义。
4. 工具结果压缩优先处理 Evidence 文本，但不得改写原始 user / assistant 消息。
5. 旧的 `stableagent.tools.registry.Evidence` 导入必须继续可用。

**验收**：所有旧工具测试不改断言；默认 `Evidence.model_dump()` 与 P2 `tool_result` 载荷字段一致；压缩测试断言 Evidence 相关文本可折叠但对话配对不被破坏。

### WP-3.7：Benchmark、CI、文档与发布证据

**文件**：`benchmarks/context_compression_benchmark.py`、`benchmarks/recovery_time_benchmark.py`、`.github/workflows/ci.yml`、`docs/design-decisions/03-context-engineering.md`、`docs/blog/02-context-engineering.md`、`README.md`、`CHANGELOG.md`、`docs/acceptance/p3-context-engineering.md`、`docs/design-decisions/00-contracts.md`。

1. Benchmark 使用固定离线语料和脚本 Provider。输出每种压缩策略的：估算 token、节省率、事实保留率、最终回答断言、Provider usage 误差（MAE 与偏差方向）。
2. 工具爆炸 benchmark 固定消息语料、只改变工具数或筛选策略；至少 200 tools；同时输出裁掉 token 和 resident 护栏是否满足。
3. 重跑 P2 recovery benchmark，记录压缩后 snapshot payload 字节数和恢复耗时变化。任何新增开销必须有解释与配置项。
4. CI 的 FOCUS 增加 `context/tokens.py`、`assembly.py`、`compression.py`、`capability.py`、`budget.py`、`engine.py`，每个文件 ≥90% 覆盖；保留已有 core 门禁。
5. ADR-03 必须解释并否决：在 loop 内联上下文逻辑、组装器抛预算异常、依赖 tiktoken、摘要仅存内存、使用 LLM 充当唯一质量评审、将摘要双写到 state 字段与 message。
6. README 只陈述已完成能力和实际 benchmark 数据；更新开发命令与测试数量时必须以本次运行输出为准。
7. CHANGELOG 逐项登记兼容扩展和摘要事实源决策。

## 5. 测试矩阵与验证命令

每个工作包完成后运行最小相关测试；阶段收口运行全量门禁。

```bash
.venv/bin/pytest -q tests/unit/test_token_counter.py tests/unit/test_compression.py
.venv/bin/pytest -q tests/unit/test_capability.py tests/unit/test_context_engine.py
.venv/bin/pytest -q tests/unit/test_agent_recovery.py tests/integration/test_crash_recovery.py
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
```

收口前还必须：

```bash
.venv/bin/pytest --cov=stableagent --cov-report=xml --cov-report=term-missing
python benchmarks/context_compression_benchmark.py
python benchmarks/recovery_time_benchmark.py
```

执行 Python 3.10 与 3.12 验证时，使用隔离环境，记录命令、Python 版本、日期和输出摘要。真实 Provider 的测试只能作为补充，不能替代离线 CI 测试。

## 6. P3 阶段验收清单（2026-11-02）

- [x] `core/loop.py` 相对 P2 收口基线 `46441f0` 为零修改。
- [x] 默认 `Config()` 的 Provider payload、事件流和最终 state 与 P2 金标准逐字节一致。
- [x] token 估算、工具裁剪、压缩和预算策略均可注入、离线且确定性。
- [x] ContextReport 中的估算 token、工具裁剪 token 与 Provider `prompt_tokens` 口径清晰；恢复不会改变预算决策。
- [x] 三档压缩均无 orphan tool call/result；summary/hybrid 的产物进入 checkpoint payload。
- [x] static/relevance、resident 护栏、fallback、稳定排序和显式 selector 优先均有测试。
- [x] compress/degrade/reject 在入场和运行中语义明确，终止事件契约未被破坏。
- [x] Evidence 的文件位置与兼容策略已由 ADR-03 记录并测试；P3 不增加会改变默认 event payload 的 trace 字段。
- [x] context benchmark、200 工具爆炸 benchmark、summary checkpoint 影响和 P2 recovery 基线均可复现并有日期明确的对外证据。
- [x] 本地与远端 CI 验证在 Python 3.10 / 3.12 通过（run 41384598912，两 job success）；3.11 本地同规格通过；全库覆盖率 98.99%，Core 与 context FOCUS 每文件 ≥90%。
- [x] ADR-03、P3 DoD、契约文档、CHANGELOG、README、博客与源码一致。
- [x] 当前分支已提交并推送（`origin/main` @ `707dff2`），GitHub Actions 3.10/3.12 workflow 全绿；run URL 与逐 job 数值记在 [P3 Acceptance](../acceptance/p3-context-engineering.md) 的“远端 CI 收口记录”，G6 已标记 52/52 正式收口。

## 7. 阶段交接：P4 的输入

P3 向 P4 交付的稳定能力是**可恢复的单 Worker Runtime**：

- `Agent` 可作为 Assignment 的执行器；`run_id` 是单次 Assignment worker run 的标识，不是 Ticket 标识。
- `AgentState` 可被 checkpoint/recover，但不得扩展为多任务状态数据库。
- `Event` 和 `ContextReport` 可被 P4 观察、映射为 Worker Report，但 P4 不得修改其终止语义。
- ContextEngine 管理单 Worker 的 token 和工具视图；P4 Scheduler 的并发、队列和长生命周期预算应建立独立模型。

P4 启动前，实施者必须在该阶段最终提交中记录：P3 版本、全量验证输出、benchmark 基线位置和已知估算误差范围。
