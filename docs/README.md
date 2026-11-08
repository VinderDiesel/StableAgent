# StableAgent 文档中心

本目录是 StableAgent 的项目知识入口。文档按职责划分，而不是按创建日期堆放：路线图说明**要做什么和先后顺序**，实施规格说明**怎样完成一个阶段**，验收账本保存**实际完成证据**，ADR 记录**为何做出关键选择**。

## 当前状态

当前主线是 **P5：受控执行与仓库集成**，状态 Active——2026-11-03 实施计划经维护者确认（ADR-05 Accepted），M1/M3/M4 已完成、M2 为 11/12（2.6 未勾）；2026-11-08 推送 `main` 后远端 CI run `42427823663` 在 Python 3.10 / 3.12 双绿（DoD 46/46、里程碑 39/40），2.6 的"命令结果写入 Assignment Report"处理完才收口。执行顺序与逐项验收写在 [P5 验收账本](acceptance/p5-guarded-execution.md)。**P4：Ticket 编排内核已 60/60 正式收口**（编排内核、离线示例、基准与 18 文件覆盖率闸门落地，远端 CI run 41596839282 在 Python 3.10 / 3.12 双绿，账本见 [P4 验收账本](acceptance/p4-ticket-orchestration.md)）。**P3：Worker Context Runtime 已 52/52 正式收口**（实现、文档、benchmark、ADR-03 与远端 CI 证据齐备，run 41384598912 在 3.10 / 3.12 全绿）。

- 现行范围和阶段顺序：[Roadmap](Roadmap.md)
- 当前阶段 P5：[实施规格](specs/p5-guarded-execution-repository-integration.md) → [验收账本与实施计划](acceptance/p5-guarded-execution.md) → [ADR-05](design-decisions/05-guarded-execution.md)
- 已完成阶段 P4：[实施规格](specs/p4-ticket-orchestration-kernel.md) → [验收账本（已完成证据）](acceptance/p4-ticket-orchestration.md) → [ADR-04](design-decisions/04-ticket-orchestration.md)
- 已完成阶段 P3：[实施规格](specs/p3-worker-context-runtime.md) 与 [验收账本（已完成证据）](acceptance/p3-context-engineering.md)
- 公共契约：[ADR-00 Contracts](design-decisions/00-contracts.md)

> 状态的单一来源是当前阶段的验收账本与可复现验证输出。路线图只给摘要；README、博客和 CHANGELOG 不得自行宣布阶段完成。

## 按读者选择阅读路径

| 读者 | 建议顺序 |
|---|---|
| 使用者 | 根目录 [README](../README.md) → 示例 → API/配置说明 |
| 当前阶段实施者 | Roadmap → Contracts → 本阶段 Spec → 本阶段 Acceptance → 相关 ADR |
| 架构审阅者 | Roadmap → Contracts → ADR-01/02 → 当前阶段 ADR → Spec |
| 可靠性审阅者 | P2 Acceptance → ADR-02 → recovery benchmark → 相关集成测试 |
| 未来 Super-Agent 实施者 | Roadmap → P3 交接 → [阶段规格索引](specs/README.md) → 前置阶段交接 |
| 外部读者 | 根 README → [Articles](blog/) → Roadmap |

## 目录与职责

| 位置 | 状态 | 职责 |
|---|---|---|
| [Roadmap.md](Roadmap.md) | Current | P0–P7 的唯一阶段范围、顺序、边界和退出条件摘要 |
| [documentation-standards.md](documentation-standards.md) | Current | 文档分类、命名、状态标签、证据和更新规范 |
| [acceptance/](acceptance/) | Evidence | 每个阶段的 DoD、验收方法、实测输出和未关闭事项 |
| [specs/](specs/) | Current / Future | P3–P7 的可执行实施合同、工作包、测试和阶段交接 |
| [design-decisions/](design-decisions/) | Accepted | 公共契约和已接受的架构决策；未来 ADR 按阶段追加 |
| [blog/](blog/) | Draft / Published | 对外技术文章；只引用已记录的 benchmark 和验收证据 |
| [archive/](archive/) | Archived | 原始构想和被取代的历史材料，不作为现行实现规范 |

## 阶段索引

| 阶段 | 状态 | 实施规格 | 验收账本 | 关键决策 |
|---|---|---|---|---|
| P0 | 完成 | — | — | [Contracts](design-decisions/00-contracts.md) |
| P1 | 完成 | — | [P1 Core Runtime](acceptance/p1-core-runtime.md) | [ADR-01](design-decisions/01-loop-runtime.md) |
| P2 | 完成 | — | [P2 Checkpoint & Recovery](acceptance/p2-checkpoint-recovery.md) | [ADR-02](design-decisions/02-checkpoint.md) |
| P3 | 完成 | [P3 Spec](specs/p3-worker-context-runtime.md) | [P3 Acceptance](acceptance/p3-context-engineering.md) | [ADR-03](design-decisions/03-context-engineering.md) |
| P4 | 完成（DoD 60/60，里程碑 30/30） | [P4 Spec](specs/p4-ticket-orchestration-kernel.md) | [P4 Acceptance](acceptance/p4-ticket-orchestration.md) | [ADR-04](design-decisions/04-ticket-orchestration.md) |
| P5 | Active（M1/M3/M4 完成，M2 11/12；远端 CI 3.10/3.12 双绿，run 42427823663；DoD 46/46、里程碑 39/40；2.6 未勾） | [P5 Spec](specs/p5-guarded-execution-repository-integration.md) | [P5 Acceptance](acceptance/p5-guarded-execution.md) | [ADR-05](design-decisions/05-guarded-execution.md) |
| P6 | 未来 | [P6 Spec](specs/p6-single-repository-super-agent-beta.md) | 待阶段启动时创建 | ADR-06，待创建 |
| P7 | 未来 | [P7 Spec](specs/p7-production-hardening-ecosystem.md) | 待阶段启动时创建 | ADR-07，待创建 |

## 文档维护

- 新增或更新文档前，先阅读 [文档维护规范](documentation-standards.md)。
- 变更公共契约、执行语义或阶段边界时，必须在同一次改动中更新 ADR、Acceptance、CHANGELOG 与相关 Spec。
- 不删除完成阶段的验收原始证据；通过勘误、补充说明或新的 ADR 修正其解释。
- 内部链接使用相对路径；移动文档后必须检查仓库内 Markdown、代码 docstring、README 和 CHANGELOG 的引用。
