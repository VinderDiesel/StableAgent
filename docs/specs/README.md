# StableAgent 阶段实施规格

本目录提供 P3–P7 的可执行实施规格。每份规格均采用相同结构：目标、范围、非目标、架构与契约、工作包、测试与验收、文档与发布、阶段交接。

| 阶段 | 规格 | 主题 | 开始条件 |
|---|---|---|---|
| P3 | [Worker Context Runtime](p3-worker-context-runtime.md) | 上下文、工具视图、预算、证据与 benchmark | P2 已完成 |
| P4 | [Ticket 编排内核](p4-ticket-orchestration-kernel.md) | Ticket、Assignment、Scheduler、DAG、Outbox | P3 退出条件满足 |
| P5 | [受控执行与仓库集成](p5-guarded-execution-repository-integration.md) | worktree、授权、sandbox、MCP、GitHub 显式任务 | P4 退出条件满足 |
| P6 | [单仓库 Super-Agent Beta](p6-single-repository-super-agent-beta.md) | Router、Source、Subscription、observe 模式、监管面 | P5 退出条件满足 |
| P7 | [生产化与生态扩展](p7-production-hardening-ecosystem.md) | 多来源、多模型、Memory、控制面、可观测与部署 | P6 退出条件满足 |

## 使用规则

1. 任一 Agent 实施前必须完整阅读目标阶段规格及所有前置阶段的“阶段交接”章节。
2. 每个工作包先写/更新测试，再实现，再运行该工作包规定的验证命令。
3. 不得以修改既有测试断言来掩盖回归；公共契约仅允许带默认值的兼容扩展。
4. 仅当阶段全部退出条件具备可复现证据时，才能启动下一阶段。
5. 规格和实际代码冲突时，以当前稳定契约、测试和 ADR 为准；先更新规格或 ADR 形成明确决策，再修改实现。
