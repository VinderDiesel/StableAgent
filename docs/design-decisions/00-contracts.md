# ADR-00：公共契约基线（当前 Contract v1.1.0）

- 状态：Accepted（P0 初始基线 v1.0.0；P1 修订及 P2/P3 兼容扩展见文末记录，当前 `CONTRACT_VERSION = 1.1.0`）
- 日期：2026-10-30
- 代码入口：`stableagent/core/state.py`（`CONTRACT_VERSION = "1.1.0"`）

## 版本化与冻结规则（全局）

1. 六项公共契约以 `CONTRACT_VERSION` 标注当前版本 **v1.1.0**；v1.0.0 是 P0 初始基线。
2. **P0~P1：允许修订**。任何字段/语义变更必须：
   - 写入 `CHANGELOG.md`，标注**破坏性 / 非破坏性**；
   - 同步更新本文档对应小节。
3. **P2 起：冻结**。只允许向后兼容的扩展：
   - 新增**带默认值的可选字段**；
   - 新增**枚举成员**（消费方必须容忍未知成员以外的新值——按字符串解析并降级处理）;
   - 新增 `Config` 配置节（默认工厂构造）。
   推翻既有字段语义、删除字段、改变不变式 = 破坏性变更，P2 起禁止。
4. 兼容性判据：旧快照 / 旧调用方代码在新版本上行为不变（`AgentState.contract_version` 供迁移器识别）。

## 契约一：Event —— 统一事件流（v1.0.0）

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `EventType` | `thinking / tool_call / tool_result / checkpoint / error / done` |
| `data` | `dict[str, Any]` | 事件载荷，按 type 解释 |
| `step` | `int` | 产生该事件时的 loop 步号 |

不变式：Event 一经发布**不可变**（frozen model）；`done` 或 `error` 是流的终止事件。
扩展点：新增 EventType 成员；`data` 内新增键（消费方按键容忍缺失）。

## 契约二：Message —— 对话消息（v1.0.0）

| 字段 | 类型 | 说明 |
|------|------|------|
| `role` | `system/user/assistant/tool` | 工具结果固定 `tool` |
| `content` | `str` | 文本内容 |
| `name` / `tool_call_id` | `str \| None` | 工具消息回填 ToolCall.id 以建立追溯 |

不变式：一条工具结果的 `tool_call_id` 必须能追溯到先前 `role=assistant` 消息产生的 `ToolCall.id`。

## 契约三：AgentState —— 可序列化快照（v1.0.0）

| 字段 | 类型 | 说明 |
|------|------|------|
| `contract_version` | `str` | 快照产生时的契约版本，供 P2 迁移器识别 |
| `run_id` / `agent_name` | `str` | 运行与 Agent 标识 |
| `status` | `AgentStatus` | `pending → running → (paused ⇄ running) → succeeded \| failed` |
| `step` | `int` | 当前步号 |
| `messages` | `list[Message]` | 完整对话历史 |
| `error` | `str \| None` | 失败归一信息 |

不变式（P1 loop 的实现前提，P2 消费）：
1. `AgentState` 是一次运行的**唯一事实源**；loop 不得携带状态外信息。
2. 序列化/反序列化无损：`AgentState.loads(s.snapshot()) == s`。
3. `PAUSED` 状态可由快照完整重建——这是 P2 checkpoint 的地基。

## 契约四：Provider —— 三种调用形态（v1.0.0）

代码入口：`stableagent/providers/base.py`

| 形态 | 签名 | 返回 |
|------|------|------|
| sync | `complete(messages, tools=None, **options)` | `ProviderResponse` |
| async | `acomplete(messages, tools=None, **options)` | `ProviderResponse`（await） |
| stream | `stream(messages, tools=None, **options)` | `AsyncIterator[ProviderChunk]` |

- `ProviderResponse`：`content / tool_calls / usage`（`usage` 为 token 计量预留，P3 预算治理消费）。
- `ProviderChunk`：`text / tool_call / done`；`done=True` 终止流。

不变式：loop 只依赖这三种形态；新 Provider（Anthropic/Ollama，P6）不引入第四种公开调用面。

## 契约五：Tool / Evidence —— 工具抽象与失败归一化（v1.0.0）

代码入口：`stableagent/tools/evidence.py`（canonical `Evidence`）与 `stableagent/tools/registry.py`（`Tool` / `ToolRegistry` / 兼容重导出）

- `Tool.execute(arguments) -> ToolResult`（async）；工具实现**不吞异常**。
- `ToolRegistry.call(tool_call) -> Evidence`：唯一归一化点——注册缺失、执行异常、
  `ok=False` 返回值一律收敛为 `Evidence`。
- `Evidence` 不变式：`ok=False` **当且仅当** `error` 非空；`tool_call` 原样携带以便追溯。

扩展点（P3）：Evidence 增强（来源链、父子调用）以新增可选字段方式扩展。

## 契约六：Config —— 组合式配置（v1.0.0）

`Config = ProviderConfig + ContextConfig + CheckpointConfig + SandboxConfig + MemoryConfig + ControlConfig`，
全部子节带默认值，Agent 缺省构造即可运行——扩展 = 新增配置节（default_factory），对既有调用零侵入。

## 与后续阶段的接入声明（P2 起冻结范围的依据）

- P1 `core/loop.py` 必须实现"状态外置 + 纯函数转移 + `pause()` 接入点"，否则违反契约三 → P2 无法只做持久化层。
- P3 上下文组装经 loop 预留位点（默认 pass-through，`ContextConfig.assembly="passthrough"`）注入，不得修改 `core/loop.py`。
- 二者均为 Phase 1 DoD 的硬性验收条目。

## P1 修订记录（v1.0.0 -> v1.1.0，全部非破坏性）

| 变更 | 类型 | 说明 |
|------|------|------|
| `EventType.PAUSED` 新成员 | 兼容扩展 | 暂停事件表达（决策 F5） |
| `Message.tool_calls` | 新增字段（默认 `[]`） | assistant 消息携带工具调用，重放/恢复的前提 |
| `AgentState.usage` | 新增字段（默认 `{}`） | 跨步 token 累计（P3 预算数据源，验收 D4） |
| `ControlConfig.max_steps` | 新增字段（默认 10） | 超限 -> `error` + FAILED（决策 F2） |
| `ToolCall` 移至 `core/state.py` | 位置调整 | `providers/base.py` 重导出，导入路径不变 |

### P1 锁定的 loop 契约（P2 起冻结范围的组成部分）

决策正文（理由、备选、后果）见 **ADR-01 `01-loop-runtime.md`**，此处仅存摘要索引：

- F1：步转移接口 `AgentLoop.advance(state) -> (AgentState, list[Event])`；
  事件引擎 `drive(state) -> AsyncIterator[(Event, AgentState)]`。
- F3：`pause()` 步边界生效；非法态返回 False（no-op）；`resume()/aresume()` 无
  PAUSED 态时抛 ValueError；暂停期间拒绝新任务（RuntimeError）。
- F4：`done | error` 每次运行恰好一个且为流末尾（不变式）。
- 组装位点：`ContextAssembler`（`context/assembly.py`，ASSEMBLERS 注册表）与
  `ToolSelector`（loop 注入参数）——P3 经此接入，零修改 `core/loop.py`。

## P2 扩展记录（v1.1.0 之上的兼容扩展，冻结规则首次生效）

冻结规则（第 3 条）自 P2 起适用：**只允许**新增带默认值的可选字段 / 新增枚举成员 /
新增配置节。P2 交付内容逐条对照如下，**全部为非破坏性**，故 `CONTRACT_VERSION` 保持
`1.1.0`（扩展未改变任何既有字段语义与不变式，不抬版本；抬版本的判据是语义变化，
不是新增面积）。

| 变更 | 类型 | 冻结规则条目 | 说明 |
|------|------|--------------|------|
| `EventType.RETRY` | 新枚举成员 | 枚举成员 | 重试通知；旧消费方按字符串解析，忽略未知成员即安全 |
| `CheckpointConfig.sqlite_path` | 新可选字段（默认 `None`） | 带默认值字段 | 仅 `backend="sqlite"` 时必需 |
| `CheckpointConfig.redis_key_prefix` | 新可选字段（默认 `"stableagent:ckpt"`） | 带默认值字段 | 经 `redis_client` 注入时生效 |
| `CheckpointConfig.history_limit` | 新可选字段（默认 20） | 带默认值字段 | 三后端语义一致的历史裁剪 |
| `CheckpointConfig.retry_*`（4 个） | 新可选字段 | 带默认值字段 | 指数退避预算；`recovery_strategy="none"` 时不参与决策 |
| `Agent(..., checkpoint_store=/redis_client=/fallback=/sleep=)` | 新关键字参数 | —— （非契约面） | 缺省值保持 P1 行为：不配 checkpoint 即零 I/O |
| `OpenAIProvider.from_config` | 新构造入口 | —— （非契约面） | 契约四的三调用形态未增加第四种公开调用面 |

### 契约不变式在 P2 之后的成立情况

- 契约一（Event）：新增成员后终止语义不变——注入 `checkpoint`/`retry` 之后
  `done|error` 依然恰好一个且位于流末尾（DoD L4，测试
  `test_checkpoint_events_land_at_step_boundaries_and_never_after_done`）。
- 契约二/三（Message / AgentState）：`AgentState.loads(s.snapshot()) == s` 往返无损
  仍成立；旧快照（v1.0.0 fixture）经 checkpoint **读取侧** `migrate_state` 升级，
  `AgentState.loads` 语义保持严格（DoD E3/E4）。
- 契约四（Provider）：三形态未变；超时经构造入口进入 httpx，不是新的调用面。
- 契约五（Tool / Evidence）：未变。工具幂等性是恢复正确性的**使用前提**，框架只能约定
  不能强制——见 ADR-02 后果节第 1 条。
- 契约六（Config）：新增字段全部落在 `CheckpointConfig` 子节内，`Config()` 缺省构造
  即可运行（P1 的示例与测试未改动即通过）。

### P2 锁定的恢复契约（后续阶段的冻结范围组成部分）

决策正文（理由、备选、后果）见 **ADR-02 `02-checkpoint.md`**，此处仅存摘要索引：

- 保存点 = 步边界，与 F3 的暂停点同构；崩溃窗口上界为一步（工具须可重入）。
- 回看式落盘：第 k 步的快照在第 k+1 步第一个事件到达时写入；终止态快照**静默落盘**
  （不发事件）以保 F4。
- 存储协议面固定为 `save/load/records/delete` 四方法；Redis 客户端为注入项。
- 版本迁移只在读取侧；高于当前版本 / 未知版本 → `SnapshotVersionError`（不静默猜测）。
- 失败自愈三分类与重试边界（`max_attempts` 含首次调用）；需人工停在 `PAUSED`，
  不新增状态机成员。

## P3 扩展记录（v1.1.0 之上的兼容扩展，版本不变）

P3 没有改变既有 Event、Message、AgentState、Provider、Tool、Evidence 或 Config 的字段语义与状态机。所有新增字段带默认值，默认 `Config()` 仍使用 P2 的 pass-through 消息、全量原序工具和零 ContextEngine 路径，因此 `CONTRACT_VERSION` 保持 `1.1.0`。

| 变更 | 类型 | 说明 |
|---|---|---|
| `ContextConfig.keep_recent_messages` / `max_summary_chars` / `tool_result_excerpt_chars` / `max_visible_tools` | 新带默认值字段 | 历史压缩和能力视图参数；默认值不启用压缩/动态筛选。 |
| `ContextConfig.compression` / `capability_filter` / `max_context_tokens` / `on_budget_exceeded` | 既有配置的首次执行语义 | `compression="none"`、`capability_filter="static"`、无 budget 时保持 P2 路径；预算动作见 ADR-03。 |
| `Agent(..., token_counter=/summariser=)` | 新关键字注入项 | 可替换 token 估算和摘要器；位置参数调用形状不变。 |
| `Agent.context_reports` | 新只读属性 | 返回 ContextReport 副本；不新增 EventType，避免事件流噪音和终止语义变化。 |
| `ContextReport` / `BudgetDecision` | 新观测数据模型 | 进程内上下文观测与入口判定，不是 AgentState 的第二份快照。 |
| Evidence 位置与失败不变式 | 兼容实现调整 | canonical 类型移至 `tools/evidence.py`，registry 和顶层导入保持兼容；P3 不新增 trace 字段，避免默认 event payload 变化，Ticket/Assignment trace 延后至 P4。 |

### P3 锁定的上下文契约

- 上下文逻辑只经 `ContextAssembler`、`ToolSelector` 和 Agent 外层接入，`core/loop.py` 不认识 compression、budget 或 capability。
- 摘要唯一持久化载体是 `Message(role="system", name="context_summary")`，由 `ContextEngine.fold()` 写入 `AgentState.messages`；不新增会漂移的 `AgentState.context_summary` 字段。
- 工具裁剪永不删除本 run 已使用的 resident 工具；筛选为空时退回全量工具。
- `max_context_tokens` 比较实际发送的 messages + specs；入口 reject 产生标准 error 且不调用 Provider，运行中无法安全中止的超限在步边界暂停。
- Provider usage 仅用于 ContextReport 事后校准，不反馈到决策，因此 checkpoint/recover 保持确定性。

决策理由、替代方案、失败语义与 benchmark 见 **ADR-03 `03-context-engineering.md`**。

## 关联 ADR

- ADR-01：Loop 执行模型（P1）——`01-loop-runtime.md`
- ADR-02：Checkpoint 与失败自愈（P2）——`02-checkpoint.md`
- ADR-03：Worker Context Engineering 与预算治理（P3）——`03-context-engineering.md`

