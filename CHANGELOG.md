# CHANGELOG

本文件遵循语义化版本；公共契约的版本与冻结规则见 `docs/design-decisions/00-contracts.md`。

## [Unreleased]

### 契约修订 v1.0.0 -> v1.1.0（P1，全部非破坏性）
- `EventType` 新增成员 `paused`（兼容性扩展，消费方按字符串解析）
- `Message` 新增字段 `tool_calls: list[ToolCall] = []`
- `AgentState` 新增字段 `usage: dict[str, int] = {}`
- `ControlConfig` 新增字段 `max_steps: int = 10`
- `ToolCall` 定义移入 `core/state.py`（解除 Message 循环引用），
  `providers/base.py` 保留重导出，**旧导入路径不变**
- P1 语义决策 F1~F5（详见 `docs/p1-dod-checklist.md` 第 0 节）：
  advance 纯转移接口 / max_steps→error+FAILED / pause 步边界生效、非法态 no-op /
  终止事件恰一且居尾 / 暂停表达=新枚举成员 PAUSED

### Added（P1）
- `core/loop.py`：AgentLoop——`advance(state) -> (state, events)` 纯转移 +
  `drive()` 事件引擎（可快照、可重入，P2/P3 接入点已按 DoD 预留）
- `context/assembly.py`：上下文组装位点（默认 passthrough，注册表式扩展）
- `providers/openai.py`：sync / async / SSE 三形态 + `ToolCallAssembler`
  流式 function calling 重组
- `Agent.run/arun/stream` + `pause()/resume()` 最小实现（内存态，P2 换持久化）
- `examples/01_basic_research_agent.py`（真实 OpenAI，需 OPENAI_API_KEY）、
  `examples/02_loop_offline_demo.py`（零依赖离线演示）
- 测试 49 个（总覆盖率 99%，`core/loop.py` 100%），含 C2 断点重放等价参数化测试

### Added
- **契约基线 v1.0.0（非破坏性：首次定义，无历史版本）**
  - `Event` / `Message` / `AgentState` / `Config`（`stableagent/core/state.py`）
  - `Provider` 三调用形态抽象（`stableagent/providers/base.py`）
  - `Tool` / `ToolRegistry` / `Evidence` 失败归一化（`stableagent/tools/registry.py`）
- 包骨架：core / context / planning / tools / sandbox / memory / control / providers / transport
  （空模块标注各自交付阶段）
- `docs/design-decisions/00-contracts.md`：契约规范、版本化规则、P2 起冻结范围
- CI（GitHub Actions）：ruff + mypy + pytest + coverage
- 示例 `examples/00_agent_skeleton.py`：可导入、可构造、不执行

### 修订窗口约定
- P0~P1：契约允许修订，必须在本文件记录并标注破坏性/非破坏性。
- P2 起：契约冻结，只允许向后兼容扩展（新增带默认值的可选字段 / 新增枚举成员 / 新增配置节）。
