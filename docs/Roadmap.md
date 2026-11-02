# StableAgent 路线图：从可靠 Worker 到受控 Super-Agent

> 目标：构建一个**面向生产环境的轻量级 Agent 运行时框架**。其核心价值是：让复杂任务可靠完成，并将上下文效率、执行权限和人工注意力都做成可度量、可治理的系统能力。
>
> 本路线图采用两层架构、逐级放权的策略：
>
> - **StableAgent Core**：通用、可嵌入的可靠 Worker Runtime。它负责单个任务的推理循环、工具执行、上下文治理、状态快照与恢复。
> - **Super-Agent Reference Deployment**：基于 Core 构建的单仓库长期运行控制平面。它负责事件接入、授权、工单、并发 Worker、外部副作用与人工监管。
>
> 核心原则：**先让一个任务可靠完成，再让系统可靠管理多个任务，最后才允许它在明确边界内主动工作。**

---

## 架构边界与演进原则

### 两层架构

```text
Super-Agent Reference Deployment（P4 以后逐步建设）
  Source / Trigger / Policy / Ticket / Assignment / Outbox / Router / Panel
                                  │
                                  ▼
StableAgent Core（P0–P3）
  Agent / AgentLoop / AgentState / ContextEngine / Tools / Checkpoint
```

`stableagent.core` 是单个 Worker 的执行内核，必须保持通用且可嵌入：

- `AgentLoop` 只负责一次 Worker 运行中的模型调用、工具循环与事件流。
- `AgentState` 只描述一次 Worker run 的可序列化状态。
- `checkpoint` 只负责 Worker run 的断点恢复。
- `context` 只负责该 Worker 的消息、工具与 token 治理。

长期任务、外部协作和控制平面属于上层：

- `Ticket` 是可跨多次执行、多个 Worker 和多个外部事件存在的授权工作单元。
- `Assignment` 是 Ticket 与一次 Worker 执行之间的绑定。
- `Source` 产生外部事件；`Channel` 负责将结果写回外部系统。
- `Policy` 和 `Mandate` 决定谁可以发起工作、工作允许到什么范围。
- `Outbox` 负责持久化外部副作用，避免重启后重复或丢失投递。

**红线：**不得将 Router、Ticket、GitHub/IM Source、长生命周期策略或面板逻辑耦合进 `core/loop.py`；上层必须通过明确的适配器启动、恢复和监管 Core Worker。

### 渐进式自治

自动化只能逐步放权：

```text
显式人工任务 → observe（仅记录会做什么） → auto（有限规则下自动执行）
```

- 初始部署只响应已授权人员的显式请求。
- 推断型或主动工作先以 `observe` 模式记录决定，不执行副作用。
- 仅在规则误报率、恢复语义、权限和审计均经过验证后，才对低风险规则启用 `auto`。
- 外部输入、工具输出、Issue/PR 内容及提示词都只能作为决策输入，不能自行扩大授权范围。

---

## 质量基线与跨阶段约束

质量门槛只升不降。阶段未达退出条件，不进入下一阶段。

| 维度 | 当前基线 | 终点要求 |
|---|---:|---:|
| 测试覆盖 | 全库 ≥70%；核心运行时 ≥90% | 全库 ≥80%，包含 e2e 与故障恢复场景 |
| 类型检查 | `mypy` 通过 | `mypy` strict 通过 |
| Lint / 格式 | ruff 0 error、format 通过 | ruff 0 warning、format 通过 |
| CI | Python 3.10 / 3.12：lint、类型、测试、覆盖率 | 增加 benchmark 回归、核心模块覆盖率及关键 e2e 门禁 |
| 文档 | 模块级 docstring、ADR、示例 | Architecture、API Reference、运维与安全说明完整 |
| 契约 | P2 起仅允许向后兼容扩展 | 语义化版本、CHANGELOG、迁移策略、弃用策略完整 |
| 自治安全 | 默认仅显式任务 | 授权、审批、审计和副作用幂等均有可验证实现 |

### 跨阶段不变式

- `Event` 是 Worker 唯一对外事件单元；每次 Worker run 恰有一个终止事件，且终止事件必须居尾。
- `AgentState` 是单次 Worker run 的唯一持久化快照来源。
- Worker 恢复不得重放已经确认完成的步骤或外部工具动作。
- 默认 `Config()` 必须保持既有行为，不因可选能力引入性能或事件流回归。
- 上层领域状态与外部副作用必须分离：先在事务中记录状态和待执行 effect，提交后才执行 I/O。
- 工具上下文与外部事件可以提供信息，但只有 Policy / Mandate 才能授予行动权限。
- 默认安静：系统仅在有结果、需要人做决策、需要审批或无法自动恢复时占用人的注意力。

---

## 实施与验收资料

- [文档中心](README.md) 是全部设计、规格、验收与历史资料的导航入口。
- P3 的实现合同是 [Worker Context Runtime Spec](specs/p3-worker-context-runtime.md)，完成状态和验证证据以 [P3 Context Engineering Acceptance](acceptance/p3-context-engineering.md) 为准。
- P4–P7 的未来实施合同由 [阶段规格索引](specs/README.md) 统一维护。
- P1/P2 的完成证据在 [Acceptance](acceptance/README.md)；公共契约与已接受设计见 [Design Decisions](design-decisions/)。

---

## 已完成：P0–P2，可靠 Worker 的基础

### P0：项目骨架与契约（Foundation）— 已完成

**目标**：确定后续阶段依赖的公共契约与工程质量基线。

**已交付**

- Python 3.10+ 包骨架、Hatch 打包与 Apache-2.0 许可。
- Pydantic v2 的 `Event`、`Message`、`AgentState`、`Config` 契约。
- `Provider` 的同步、异步、流式三种调用形态。
- `Tool` / `ToolRegistry` / `Evidence` 的工具调用与异常归一化。
- GitHub Actions：Python 3.10 / 3.12 下的 ruff、mypy、pytest 与覆盖率。
- `docs/design-decisions/00-contracts.md` 的契约冻结规则。

### P1：最小可运行内核（Worker Runtime）— 已完成

**目标**：提供可运行的单 Worker Agent，完成多步推理、工具调用和事件流。

**已交付**

- `core/loop.py`：可重入的异步 Agent Loop；状态通过 `AgentState` 外置。
- `Agent.run()` / `arun()` / `stream()` 三种调用形态。
- OpenAI 兼容 Provider 与流式 function calling 重组。
- 工具循环：模型 → tool call → tool result → 后续模型回合。
- `max_steps`、步边界 `pause()` / `resume()`、确定性终止事件。
- P3 所需的 `ContextAssembler` 和 `tool_selector` 两个接缝。

**保留约束**：P2、P3 和未来上层功能不得要求重写 `core/loop.py`。

### P2：执行可靠性（Checkpoint & Recovery）— 已完成

**目标**：让单个 Worker run 在崩溃、短暂故障和需要人工处理时保持可恢复。

**已交付**

- Memory、SQLite、Redis 三种 checkpoint 后端。
- `(run_id, step)` 幂等快照、历史裁剪与版本迁移。
- `recover()` / `arecover()` / `stream_recover()`。
- 失败分类、指数退避、fallback Provider 与需要人工处理时的暂停。
- 子进程 `SIGKILL` + SQLite 集成测试，验证恢复不会重复已完成步骤。
- `docs/design-decisions/02-checkpoint.md`：一致性、并发、迁移、重试边界。

---

## P3：Worker Context Runtime — 已完成（52/52）

**目标**：让单个 Worker 在长任务中以可测量、可恢复、可治理的方式使用历史消息和工具能力。

P3 已正式收口：变更提交并推送 `main` 后，GitHub Actions run 41384598912 在 Python 3.10 / 3.12 两个 job 全绿（`283 passed, 1 skipped`、总覆盖率 98.99%、12 个 FOCUS 心脏文件均 ≥90%）。逐条证据与命令记在 [P3 验收账本](acceptance/p3-context-engineering.md) 的“远端 CI 收口记录”。

### 已完成的基础

- `TokenCounter` 协议与默认启发式计数，支持 CJK 与工具 JSON Schema 成本估算。
- `truncation`、`summary`、`hybrid` 三档历史压缩。
- 不拆散 assistant tool call / tool result 配对的截断规则。
- `ContextAssembler` 注入点与默认路径短路。
- P3 DoD：`docs/acceptance/p3-context-engineering.md`。

### 交付内容

1. **动态能力视图与预算治理**
   - `static` 保持 P2 工具顺序与事件流；`relevance` 使用确定性规则、resident 护栏、空集 fallback 和稳定排序。
   - `ContextEngine` 统一压缩、能力视图和实际 Provider payload 的预算 preflight。
   - `compress`、`degrade`、`reject` 的入场与运行中语义均有离线测试；入口 reject 不调用 Provider 或触发 retry。
   - `ContextReport` 提供 token 估算、Provider usage 校准、工具裁剪、策略层级和孤儿配对自检，默认不增加事件噪音。

2. **Evidence、恢复和量化证据**
   - `Evidence` 已迁至 `tools/evidence.py`，旧导入路径保持兼容，并强制失败记录包含可读错误。
   - 压缩摘要以 `context_summary` 标记消息进入 checkpoint，恢复不重算摘要。
   - [P3 基准记录](evidence/p3-context-benchmark-2026-11-02.md) 报告压缩质量/节省、200 工具 resident 护栏和 checkpoint 影响。

3. **发布与工程收口**
   - [ADR-03](design-decisions/03-context-engineering.md)、[P3 验收账本](acceptance/p3-context-engineering.md)、[P3 实施规格](specs/p3-worker-context-runtime.md) 和 [对外文章](blog/02-context-engineering.md) 已同步。
   - 本地与远端质量门禁通过：ruff、format、mypy、全量 pytest、覆盖率，以及扩展后的 12 文件核心覆盖率闸门。

### P3 正式退出条件（已满足）

- [x] 当前分支经维护者确认后提交并推送（`origin/main` @ `707dff2`，共 10 个提交）。
- [x] GitHub Actions 在 Python 3.10 / 3.12 均通过 ruff、format、mypy、pytest、全库覆盖率与扩展后的核心覆盖率闸门（run 41384598912，两 job success）。
- [x] 将 workflow run URL/ID 记录到 [P3 验收账本](acceptance/p3-context-engineering.md)，G6 已勾选为 52/52。


---

## P4：Ticket 编排内核（Ticket Orchestration Kernel）

**目标**：将“任务”建模为可持久化、可授权、可恢复、可调度的领域对象；在不接入外部消息系统的前提下，可靠管理多个 Core Worker。

### 领域模型

```text
Ticket
  ├─ origin：任务来源
  ├─ mandate：初始授权与范围
  ├─ inputs：后续输入
  ├─ assignments：Worker 执行历史
  ├─ reports：结构化进度与结果
  └─ subscriptions：后续关联事件（模型预留）

Assignment
  ├─ ticket_id
  ├─ worker_run_id
  ├─ checkpoint_ref
  ├─ worker_config
  └─ status / result / failure
```

`Ticket` 是长期工作单位；`Assignment` 才绑定一次 StableAgent Worker run。不得将 Ticket 字段混入 `AgentState`。

### 交付物

- `Ticket`、`Assignment`、`Input`、`Report` 的不可变记录与状态机。
- SQLite 持久化仓储和迁移策略。
- `Ticket → Assignment → StableAgent Agent` 的适配器。
- Scheduler：并发上限、排队、取消、重试、恢复与父子任务关系。
- 基础 DAG：依赖满足、失败传播与可配置并发。
- 结构化 Worker Report：`progress`、`waiting_for_human`、`ready`、`blocked`、`failed`。
- Durable Outbox / Effects：事务中持久化领域状态与待执行 side effect，提交后执行 I/O。

### 退出条件

- 一个父 Ticket 可以派生多个子 Ticket，且依赖与并发上限正确生效。
- 崩溃后 Ticket、Assignment 和 pending effect 可恢复。
- 失败的 Worker 不影响不相关 Ticket。
- 同一 effect 重放不产生重复启动、重复通知或重复状态写入。
- 测试完全离线，无 GitHub、飞书或真实模型依赖。

---

## P5：受控执行与仓库集成（Guarded Execution & Repository Integration）

**目标**：让 Worker 在明确授权和隔离边界内操作真实代码仓库，为长期运行的协作 Agent 建立安全基础。

### 交付物

1. **隔离执行环境**
   - 每 Ticket 使用独立目录或 Git worktree，防止并发任务互相污染。
   - 将目录隔离、工具权限与安全沙箱明确分层：三者不互相替代。
   - 先提供本地受限模式，再增加 Docker sandbox、资源限制、网络策略与密钥隔离。

2. **结构化授权**
   - 将 `permission_hook` / `require_approval_tools` 演进为可持久化、可审计的 Policy / Mandate。
   - 区分准入授权、任务范围授权、动作审批、恢复重验证和交付授权。
   - 默认禁止未经授权的高风险动作与自动合并；外部文本不能扩大授权。

3. **GitHub 显式任务闭环**
   - 首个 Source 只支持单仓库 GitHub。
   - 只接受 allowlist 人员的明确 `@agent` 请求。
   - 创建 Ticket、独立 worktree、Worker 执行、创建 PR 或回写评论。
   - 暂不允许任意评论触发任务，也不启用主动规则或自动 merge。

4. **MCP 的正确位置**
   - MCP Client 作为 Worker 的工具适配层进入本阶段。
   - `Tool` 是 Worker 主动调用的能力；`Source` 是系统被动接收的事件流；二者不得混为同一抽象。

### 退出条件

- 多个 Ticket 在隔离 worktree 中运行，互不污染。
- 高风险工具和外部副作用由结构化授权控制，而非仅靠 prompt。
- GitHub 显式任务可完整走通 Ticket、Worker、PR/评论回写。
- 恢复过程不会重复创建 PR、重复提交或重复发送评论。
- Docker 可用时能验证资源与网络边界；不可用时本地受限模式仍可测试。

---

## P6：单仓库 Super-Agent Beta

**目标**：建立一个常驻 Router，在一个仓库内管理多个隔离 Worker，并以最少的人类注意力交付可审查结果。

### 架构职责

```text
Source → Trigger → Router / Policy → Ticket → Assignment → Core Worker
  ↑                                                        ↓
Channel ← Durable Outbox ← Report / Result ←───────────────┘
```

- Router 负责接收 Trigger、匹配或创建 Ticket、校验 Mandate、路由后续输入、调度 Worker 与汇总报告。
- Worker 只执行具体任务。每个 Assignment 继续复用 StableAgent 的 `AgentState` 和 checkpoint。
- 后续 Issue/PR/CI 信息通过 Subscription 提供上下文，但不自动扩大 Ticket 的授权范围。
- 先使用 `observe`，记录“会处理什么”；只有经过评估的低风险规则才逐步进入 `auto`。

### 交付物

- 常驻 GitHub Source：Issue、PR、评论、CI 状态等 Trigger 的幂等接入。
- 单仓库 Router：Trigger → Ticket 的路由、后续消息归属与恢复语义。
- Ticket Subscription：关联 Thread 的后续消息能够进入正确 Ticket。
- Policy 版本化、Mandate 记录与恢复前重新校验。
- Router / Worker 并发治理、队列和过载行为。
- 最小控制面：CLI 或只读 Web 页面，优先展示待人工处理事项、失败、审批和队列。
- 真实仓库的端到端试运行与可审计记录。

### 退出条件

- GitHub 后续评论、PR 状态和 CI 结果能关联至正确 Ticket。
- 人的后续评论可恢复既有 Ticket，不重复创建相同任务。
- 外部事件只提供上下文；授权范围只能由 Policy / Mandate 扩大。
- Worker 崩溃后恢复 Assignment，不重复完成的副作用。
- 仅在需要决策、审批、失败或结果可用时通知人。
- 在 `observe` 模式下完成足够的真实数据评估，再决定哪些规则可切换 `auto`。

---

## P7：生产化与生态扩展（Production Hardening & Ecosystem）

**目标**：将经过单仓库验证的能力扩展为可部署、可观测、可治理的生产系统，同时保持 Core 的轻量与可嵌入性。

### 交付物

- 多 Provider 与成本路由：Anthropic、Ollama、模型 fallback、成本和延迟策略。
- 多 Source：飞书、Slack、Jira 等；每个 Source 仍通过统一 Trigger / Channel 边界接入。
- 长期 Memory：写入策略、检索、衰减、隔离、删除与质量评估。
- MCP Server 注册、发现、治理和审计。
- SSE、WebSocket、FastAPI 部署示例。
- 结构化日志、指标、trace、审计导出与 benchmark 回归。
- 多租户与部署隔离。
- 完整 API Reference、运维手册、安全模型和升级迁移文档。

### 退出条件

- 全库覆盖率 ≥80%，包括 e2e、故障恢复、授权、幂等副作用和隔离测试。
- mypy strict、ruff 0 warning、CI benchmark 回归均通过。
- 提供可部署的单仓库 Super-Agent 示例与明确的安全运行指南。
- 所有已声明的主动能力均有 observe→auto 的评估记录、回滚方式和审计证据。

---

## 配套文档与对外证据

文档和 benchmark 从 P2 开始伴随主线，而不是收尾时补写。

| 阶段 | 对外证据 |
|---|---|
| P2 | Checkpoint / Recovery 深度设计与崩溃恢复演示 |
| P3 | Context Engineering：token 估算误差、压缩质量、工具爆炸 benchmark |
| P4 | Ticket / Assignment / Durable Outbox：多任务可靠性的领域建模 |
| P5 | 受控执行与仓库 Agent：授权、worktree、sandbox 的边界 |
| P6 | 单仓库 Super-Agent：Router / Worker、observe 模式与人工注意力治理 |
| P7 | 从可靠 Worker 到受控自治系统：完整架构与生产化 benchmark |

对外数字必须满足：命令、参数、运行环境、日期、波动范围可复现；token 节省必须与质量保留、召回或成功率一起报告，禁止只展示单一效率指标。

---

## 总体顺序与反范围蔓延护栏

```text
P3 Worker Context Runtime
  → P4 Ticket Orchestration Kernel
  → P5 Guarded Execution & Repository Integration
  → P6 Single-Repository Super-Agent Beta
  → P7 Production Hardening & Ecosystem
```

| 风险 | 护栏 |
|---|---|
| P3 未收口便进入多 Agent / 外部集成 | P3 退出条件未满足，不启动 P4 |
| 将 Ticket、Router、Source 塞进 AgentLoop | Core 与控制平面分层；上层只通过适配器使用 Core |
| 先做“聪明规划”，后补任务状态 | P4 先落 Ticket / Assignment / Outbox，DAG 与 Planner 建在其上 |
| 外部文本诱导 Agent 越权 | 外部输入不授予权限；Policy / Mandate 是唯一授权源 |
| worktree 被误当成安全沙箱 | 目录隔离、工具授权、Docker 沙箱分别测试和说明 |
| 自动化制造通知噪音 | 默认静默、observe 先行、控制面只展示可行动事项 |
| checkpoint 无法解决重复外部副作用 | P4 引入 durable outbox、effect 幂等键和提交后执行 |
| 平台 UI 反噬核心研发 | P6 先做最小监管界面；企业平台只在 P7 及核心稳定后扩展 |

> 终点不是“拥有更多 Agent”，而是拥有一个能在明确授权下长期运行、可靠恢复、对人保持克制、并能交付可审查结果的 Agent 系统。
