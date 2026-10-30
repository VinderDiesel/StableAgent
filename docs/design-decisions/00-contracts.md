# ADR-00：公共契约基线（Contract v1.0.0）

- 状态：Accepted（P0 基线）
- 日期：2026-10-30
- 代码入口：`stableagent/core/state.py`（`CONTRACT_VERSION = "1.0.0"`）

## 版本化与冻结规则（全局）

1. 五大契约统一以 `CONTRACT_VERSION` 标注当前基线版本 **v1.0.0**。
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

代码入口：`stableagent/tools/registry.py`

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
