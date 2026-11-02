# 阶段验收账本

本目录保存各阶段的 DoD、验收方法、实际验证输出、风险和收口记录。它们是历史证据，不是重复的路线图或实施规格。

| 阶段 | 文档 | 状态 | 使用方式 |
|---|---|---|---|
| P1 | [Core Runtime](p1-core-runtime.md) | Completed evidence | 追溯 AgentLoop、事件、暂停和 P3 接缝契约 |
| P2 | [Checkpoint & Recovery](p2-checkpoint-recovery.md) | Completed evidence | 追溯 checkpoint、恢复、重试、CI 和恢复性能基线 |
| P3 | [Context Engineering](p3-context-engineering.md) | Local close / CI pending | 实现、benchmark 与本地质量证据已齐；远端 CI 通过后正式收口 |

使用规则：

- 不删除或重写已完成阶段的原始验证记录。
- 新阶段启动时，从其 Spec 创建新的验收账本；每项验收应可映射到测试、命令或可审查的证据。
- 对历史记录的解释修正以日期明确的勘误或新的 ADR 追加，保留原始结果。
- 现行阶段范围请看 [Roadmap](../Roadmap.md)，实施步骤请看 [Specs](../specs/README.md)。
