# CHANGELOG

本文件遵循语义化版本；公共契约的版本与冻结规则见 `docs/design-decisions/00-contracts.md`。

## [Unreleased]

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
