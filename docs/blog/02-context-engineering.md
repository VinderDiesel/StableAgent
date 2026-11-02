# Context Engineering：把 Agent 的 token 效率做成可测量能力

> **状态：Draft。** 本文基于 P3 的离线固定语料基准与验收测试，不对真实模型 token、任务成功率或生产成本作泛化承诺。

长任务 Agent 的上下文不是单一字符串。每一轮请求至少包含历史消息、工具 schema、工具结果和系统约束。历史不断增长时，简单地“截断更多内容”确实能减少 token，但也可能删掉决定答案的证据，或把 tool call 和 tool result 切开，直接导致 Provider 拒绝请求。

StableAgent P3 将 Context Engineering 定义为一个可验证的 Worker Runtime 能力：在不修改 AgentLoop 的前提下，控制真实发给 Provider 的消息与工具，并把节省、质量和恢复语义放进同一套验收标准。

## 要测量实际 payload，而不是历史长度

预算对象是：

```text
组装后的 messages + 实际注入的 tool specs
```

这避免了两个常见错误。第一，只计历史文本，忽略几十或几百个工具 schema 的成本。第二，在外层按一套消息估算，而 loop 实际发送另一套消息。P3 使用同一个 `ContextEngine` 同时服务 Agent 外层 preflight 和 loop 的 assembler/tool selector 接缝，因此预算判定与 Provider payload 使用同一份计划。

默认 `Config()` 不创建 ContextEngine，维持 P2 的工具顺序、事件流和性能路径。预算、压缩或能力视图只在显式配置时启用。

## 三种压缩策略，不能只比较节省率

P3 提供：

- `truncation`：保留 system、首个用户任务和最近窗口，丢弃最早历史。
- `summary`：把旧历史折叠为确定性摘要。
- `hybrid`：先摘录旧工具结果，再摘要，最后截断。

所有策略都在切分边界向前回退，确保每个 assistant tool call 都保留对应的 tool result。摘要保存在 `messages` 中的 `context_summary` 标记消息，随 `AgentState` checkpoint 持久化；恢复时不必重算摘要。

固定语料的离线结果见 [P3 基准记录](../evidence/p3-context-benchmark-2026-11-02.md)：

| 策略 | 估算 token 节省率 | 事实保留率 |
|---|---:|---:|
| truncation | 64.29% | 25% |
| summary | 30.20% | 100% |
| hybrid | 51.16% | 100% |

`truncation` 的结果说明“最省”不等于“最好”。在这组语料中，`hybrid` 比 `summary` 多节省 270 个估算 token，同时保留全部 8 条必需事实。这个结论只适用于记录的固定语料和参数；P3 因此要求对外报告始终成对给出节省率与质量保留率。

## 动态工具视图的约束：先保证召回

工具数量增加时，工具 schema 本身会占据大部分请求。P3 的 `relevance` 视图基于确定性词面规则筛选工具；但已经在本次运行调用过的工具进入 resident set，永远不能被筛掉。筛选无结果时退回全量工具。

在固定的 200 工具场景中，动态视图保留 3 个工具，减少 8,570 个估算 token，节省率为 98.59%。其中一个与当前任务无关但已调用的 resident 工具仍被保留。这是一个刻意的取舍：工具上限服务成本，resident set 服务正确性。[基准记录](../evidence/p3-context-benchmark-2026-11-02.md) 记录了完整参数与结果。

## 预算拒绝必须是正常的运行时结果

当实际 payload 超过 `max_context_tokens` 时，P3 支持：

- `compress`：优先压缩历史。
- `degrade`：优先裁减工具，再压缩历史。
- `reject`：在运行段入口拒绝，不调用模型，也不触发重试。

`reject` 不通过 assembler 抛异常实现。组装器异常会绕过 Agent 的规范 error 事件和 checkpoint 语义。运行中如果当前 step 已经执行工具，P3 只会在安全步边界暂停；恢复后仍无法满足预算时才确定性失败。设计理由见 [ADR-03](../design-decisions/03-context-engineering.md)。

## 估算、真实 usage 与恢复成本

P3 的默认计数器是可注入的启发式实现，刻意不引入模型特定 tokenizer。Provider 返回的 `prompt_tokens` 仅用于事后校准，不影响后续决策，因此同一 snapshot 的恢复行为不会因为不同 Provider usage 而变化。

离线 benchmark 的 reference Provider 口径与启发式估算不同，用来检验校准链路而非模仿在线模型。真实 Provider 的误差需要通过显式 opt-in、脱敏的集成测量获得。

同一份基准还从第 3 步 SQLite checkpoint 恢复：摘要使最新 snapshot 从 4,056 字节降至 2,594 字节。单次读取 + 迁移时间相差 0.0352 ms，处于本机 SQLite 噪声量级，不能据此宣称稳定的延迟改进。

## 下一步

P3 完成后，StableAgent 可以将一个 Worker 的上下文、工具与预算治理为可恢复、可测量的运行时能力。P4 不会把这些规则塞进 AgentState，而会构建 Ticket、Assignment、Scheduler 与 durable outbox，让多个 Worker 在长期任务模型中协作。阶段边界见 [Roadmap](../Roadmap.md) 和 [P3 实施规格](../specs/p3-worker-context-runtime.md)。
