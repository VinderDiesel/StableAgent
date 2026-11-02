# StableAgent 文档中心

本目录是 StableAgent 的项目知识入口。文档按职责划分，而不是按创建日期堆放：路线图说明**要做什么和先后顺序**，实施规格说明**怎样完成一个阶段**，验收账本保存**实际完成证据**，ADR 记录**为何做出关键选择**。

## 当前状态

当前主线是 **P3：Worker Context Runtime**。P3 的实现、文档、benchmark 与本地 3.10/3.11/3.12 验证已经完成；仅远端 GitHub Actions 需在维护者确认、提交并推送当前变更后记录 workflow 证据。

- 现行范围和阶段顺序：[Roadmap](Roadmap.md)
- P3 实施规格：[P3 Worker Context Runtime](specs/p3-worker-context-runtime.md)
- P3 活动验收账本：[P3 Context Engineering](acceptance/p3-context-engineering.md)
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
| P3 | 本地收口，待远端 CI | [P3 Spec](specs/p3-worker-context-runtime.md) | [P3 Acceptance](acceptance/p3-context-engineering.md) | [ADR-03](design-decisions/03-context-engineering.md) |
| P4 | 未来 | [P4 Spec](specs/p4-ticket-orchestration-kernel.md) | 待阶段启动时创建 | ADR-04，待创建 |
| P5 | 未来 | [P5 Spec](specs/p5-guarded-execution-repository-integration.md) | 待阶段启动时创建 | ADR-05，待创建 |
| P6 | 未来 | [P6 Spec](specs/p6-single-repository-super-agent-beta.md) | 待阶段启动时创建 | ADR-06，待创建 |
| P7 | 未来 | [P7 Spec](specs/p7-production-hardening-ecosystem.md) | 待阶段启动时创建 | ADR-07，待创建 |

## 文档维护

- 新增或更新文档前，先阅读 [文档维护规范](documentation-standards.md)。
- 变更公共契约、执行语义或阶段边界时，必须在同一次改动中更新 ADR、Acceptance、CHANGELOG 与相关 Spec。
- 不删除完成阶段的验收原始证据；通过勘误、补充说明或新的 ADR 修正其解释。
- 内部链接使用相对路径；移动文档后必须检查仓库内 Markdown、代码 docstring、README 和 CHANGELOG 的引用。
