# StableAgent 路线图：从内核到生产级 Agent 运行时

> 目标：成为**面向生产环境的轻量级 Agent 运行时框架**（不是 demo，不是玩具）。
> 核心卖点：**让复杂任务稳定跑完（可靠性）+ 让上下文更高效（Context Engineering）**。
> 总原则：每个阶段都是**可运行、可测试、可对外演示**的增量；质量门禁（测试/类型/可观测性）逐阶段抬高。

---

## 质量基线与跨阶段约束（贯穿全程）

所有阶段共同遵守，且门槛只升不降：

| 维度 | 基线 | 终点要求 |
|------|------|----------|
| 测试覆盖 | Phase 1 ≥ 70% | Phase 6 ≥ 80%（含 e2e） |
| 类型检查 | mypy 通过 | mypy strict 通过 |
| Lint | ruff 0 error | ruff 0 warning |
| CI | 提交即跑测试+类型 | + 覆盖率门禁 + Benchmark 回归 |
| 文档 | 模块级 docstring | Architecture / Design Decisions / API Reference 完整 |
| 对外契约 | 阶段内稳定 | 语义化版本 + CHANGELOG |

**核心设计契约（Phase 0 定义并版本化；P0~P1 允许修订、逐条标注，P2 起冻结——只扩展不推翻）：**
- `Event` 统一事件流（thinking / tool_call / tool_result / checkpoint / error / done）
- `AgentState` 可序列化快照（Pydantic v2）
- `Provider` 抽象（统一 sync / stream / async 三种调用形态）
- `Tool` 抽象（统一入参/出参 + 失败归一化 → `Evidence`）
- `Config` 组合式（Agent = Provider + ContextEngine + Checkpoint + Sandbox + Memory + Control）

---

## Phase 0：项目骨架与契约（Foundation）— 1~2 周

**目标**：把"地基和接口契约"钉死，后续所有阶段都在这套契约上实现，避免返工。

**交付物**
- `pyproject.toml`（Python 3.10+，依赖：pydantic v2、anyio、httpx、ruff、mypy、pytest、pytest-asyncio、pytest-cov）
- 包骨架（按 Plans.md 目录，先落地空模块 + `__init__`）
- 核心类型与契约：`core/state.py`（Event / Message / AgentState / Config）、`providers/base.py`、`tools/registry.py` 抽象
- CI 流水线与质量门禁（GitHub Actions：test + mypy + ruff + coverage）
- 一个"空转"示例：能 `from stableagent import Agent` 并构造对象（暂不执行）

**完成定义（DoD）**
- 包可安装、可导入；CI 全绿；类型检查通过；覆盖率工具接入。
- 产出 `docs/design-decisions/00-contracts.md`（把所有公共契约与不变式写清楚，这是技术深度的第一块招牌）。
- 契约逐条标注版本号，并声明"P2 起冻结"的范围；P0~P1 期间的任何修订必须同步 CHANGELOG，标明破坏性/非破坏性。

---

## Phase 1：最小可运行内核（MVP Core Runtime）— 3~4 周

**目标**：端到端跑通"多步推理 + 工具调用 + 事件流"，这是框架的心脏。

**交付物**
- `core/loop.py`：异步事件驱动的 Agent Loop（支持 `run` 同步 / `stream` 流式 / `arun` 异步）
- `providers/openai.py`：首个 Provider，支持流式 function calling
- 基础工具循环：LLM → tool_call → 执行 → tool_result → 继续，直至 `done`
- `core/state.py`：内存态状态管理（暂不做持久化）
- `examples/01_basic_research_agent.py`：真实可跑的多步研究任务

**完成定义（DoD）**
- 一个 3+ 步的真实任务能端到端跑完，事件流完整可打印。
- 单元测试覆盖 loop 的各分支（无工具 / 多轮工具 / 提前终止）；覆盖率 ≥ 70%。
- **为 P2 预留**：loop 按"可快照、可重入"结构实现——状态外置（`AgentState` 为唯一事实源，loop 内不携带隐式内存态）、每步转移是纯函数、保留 `pause()` 接入点；P2 的 checkpoint/恢复**不得要求重写 `core/loop.py`**。
- **为 P3 预留**：loop 中留有可插拔的上下文组装位点（默认 pass-through 实现）；P3 的压缩 / 动态能力视图 / 预算治理**不得要求修改 `core/loop.py`**。
- 此阶段产物已可作为"框架能跑"的最早对外演示。

---

## Phase 2：执行可靠性（Checkpoint & Recovery）— 3~4 周

**目标**：让任务"断点可恢复、失败可自愈"——这是区别于玩具的核心。

**交付物**
- `core/checkpoint/`：`manager.py`（快照触发策略 `auto_save_every`）、`storage.py`（Memory / SQLite / Redis 三种后端）、`recovery.py`（resume / retry_with_fallback）
- `core/state.py` 升级：状态序列化与版本化（兼容旧快照）
- 失败自愈：超时控制、指数退避重试、错误分类（可重试 / 不可重试 / 需人工）
- 优雅中断：`agent.pause()` / `agent.resume()` 语义

**完成定义（DoD）**
- 演示：运行中 kill 进程 → 重启 → 从最近 checkpoint 恢复并跑完。
- 三种存储后端各有集成测试；恢复策略各有单测。
- 产出 `docs/design-decisions/01-checkpoint.md`（一致性、并发、版本迁移策略）——这是"生产级"的硬证据。

---

## Phase 3：推理调度 / Context Engineering — 4~5 周

**目标**：把"上下文效率"做成可度量、可治理的能力（独家卖点主场）。

**交付物**
- `context/engine.py`：Context Engine 抽象（可插拔）
- `context/assembly.py`：上下文组装策略（system / history / tools / evidence 编排）
- `context/compression.py`：历史压缩（summary / truncation / hybrid）
- `context/capability.py`：动态能力视图（每轮按上下文裁剪可用工具，而非全量注入）
- `context/budget.py`：Token 预算治理（超限策略：压缩 / 降级 / 拒绝）
- `tools/evidence.py`：工具结果归一化与追溯（结构化 `Evidence`）
- `benchmarks/context_compression_benchmark.py`：压缩策略的 token 节省率 / 质量保留率

**完成定义（DoD）**
- 在固定长任务上，**可量化**展示 token 节省率（如 hybrid 比截断省 X% 且质量不降）。
- 预算超限时行为确定（单测覆盖 compress / degrade / reject）。
- 动态能力视图在"工具爆炸"场景下显著降低注入 token（有 benchmark 数据支撑）。

---

## Phase 4：任务规划与编排（Planning & SubAgent）— 3~4 周

**目标**：支持复杂任务的自动拆解与并发执行。

**交付物**
- `planning/planner.py`：基于 LLM 的任务分解（含分解质量校验 / 回退到单步）
- `planning/dag.py`：依赖图建模与拓扑排序
- `planning/scheduler.py`：并发调度（max_concurrency、依赖阻塞、失败传播）
- SubAgent 派生：子任务交由子 Agent 执行并回收结果

**完成定义（DoD）**
- 一个含依赖关系的复杂任务被拆解并按 DAG 顺序 / 并发正确执行。
- 调度器单测：依赖正确性、并发上限、失败隔离。
- 与 Phase 2 的 Checkpoint 协同：规划中途失败可从断点续跑。

---

## Phase 5：能力底座（MCP / Sandbox / Memory）— 4~5 周

**目标**：让框架能安全接入外部工具、执行不可信代码、具备长期记忆。

**交付物**
- `tools/mcp_client.py`：MCP 协议客户端（注册 / 发现 / 调用外部工具）
- `sandbox/executor.py` + `docker_runner.py` + `policy.py`：Docker 沙箱执行（隔离级别、资源/网络限制、超时）
- `memory/store.py` + `short_term.py` + `long_term.py`：短期记忆 + 长期记忆（向量检索 + 衰减机制）

**完成定义（DoD）**
- 接入一个真实 MCP Server 的工具并成功调用。
- 在 Docker 沙箱中执行不可信代码，宿主机资源不受影响（有安全测试）。
- 长期记忆的写入 / 检索 / 衰减行为有单测覆盖。

---

## Phase 6：执行控制与生产化（Control & Hardening）— 3~4 周

**目标**：补齐"生产环境"最后一块拼图——控制、多模型、通信、可观测、文档。

**交付物**
- `control/`：成本/Token 控制（`cost.py`）、权限校验钩子（`permission.py`）、Human-in-the-loop 澄清（`clarification.py`）
- `providers/` 扩展：Anthropic、Ollama + Provider 故障转移（fallback）
- `transport/`：SSE 流式输出、WebSocket 双向通信
- 可观测性：结构化日志、指标（token/耗时/成功率）、trace 串联
- 安全自检、配置管理、完整文档（Architecture / Design Decisions / API Reference / Quickstart）
- e2e 测试 + Benchmark 回归基线（相对 Phase 3 自家指标，不强行对标成熟框架）

**完成定义（DoD）**
- 测试覆盖率 ≥ 80%（含 e2e）；mypy strict 通过；ruff 0 warning。
- 一个"生产姿态"的部署示例（如 FastAPI 包装 + SSE 推事件）。
- 文档齐备，新人可按 Quickstart 在 10 分钟内跑通第一个 Agent。
- 至此：**StableAgent 达到"面向生产环境的轻量级 Agent 运行时框架"终点定义。**

---

## 总体节奏：主线 + 双配套并行

主框架（P0~P6）是**主线**，两个配套项目不是"做完主线之后的事"，而是**嵌入节奏、各自有启动时机与里程碑**的并行轨道：

- **配套二（文档/博客）**：低成本高回报，**从 P2 起即并行启动**，随主线能力成熟持续产出，是建立技术影响力的主力。
- **配套一（企业级平台）**：依赖主线能力，**从 P4 起启动**，复用主框架作为运行时内核，避免重复造轮子。

---

## 配套项目二：Agent Context Engineering 深度实践（文档 / 博客 / Benchmark）— 从 P2 起并行

**定位**：用技术文章 + Benchmark 证明"推理调度"方向的深度思考，是简历/开源影响力的低成本高回报动作。

**与主线的协同节奏**

| 阶段 | 产出内容 | 对应主线能力 |
|------|----------|--------------|
| P2 期间 | 《Agent Checkpoint 机制的深度设计与实现》 | Checkpoint / Recovery |
| P3 期间 | 《Context Engineering：从 Token 浪费到注意力组织》+ 压缩 Benchmark 报告 | Context Engine / 压缩 / 预算 |
| P4 期间 | 《长链路任务的预算治理策略对比》 | DAG / 并发 / 成本 |
| P5 期间 | 《MCP 协议实战：如何构建企业级工具网关》 | MCP / Sandbox |
| P6 期间 | 《从 0 到生产级 Agent 框架》总览文 + 公开 Benchmark 基线 | 全框架收口 |

**交付物**
- `docs/blog/` 下 5+ 篇深度文（含架构图、代码片段、数据图表）
- `benchmarks/` 下可复现的 Benchmark 脚本与结果报告（token 节省率、恢复时间、压缩质量保留率）
- 每篇配 Demo 仓库链接，形成"文档即产品"的飞轮

**完成定义**：主框架收口时，已有一组可对外传播、数据扎实的技术内容，能直接支撑开源代表作定位。

---

## 配套项目一：企业级 Agent 运行时平台（Web UI + 运维/可观测）— 从 P4 起并行

**定位**：类似 Dify 的轻量级替代品，但**专注 Agent 运维与可观测性**，运行内核直接复用主框架（不是另写一套）。

**分阶段（复用主线能力，不重复造轮）**

| 阶段 | 依赖主线 | 交付物 |
|------|----------|--------|
| 平台 P1（P4 起） | 主框架 P1~P3 | FastAPI 包装主框架 + SSE 事件流接入；Agent 配置与部署 |
| 平台 P2 | 主框架 P2 | Checkpoint 时间旅行调试（读取快照回放） |
| 平台 P3 | 主框架 P3/P6 | Token 成本统计与告警、执行 Trace 可视化 |
| 平台 P4 | 主框架 P5 | MCP 工具市场（注册/发现/管理） |
| 平台 P5 | 主框架 P6 | 多租户隔离、权限/HITL 面板、WebSocket 实时事件 |

**技术栈**：FastAPI + React/Vue + PostgreSQL + Redis（仅平台自身元数据/多租户用，Agent 运行时仍由主框架负责）。

**完成定义**：平台能管理主框架跑出的 Agent，可视化 Trace 与成本，支持从 UI 触发 / 恢复任务。

**风险护栏**：仅在主框架 P4 之后启动；若主线进度吃紧，**平台可整体后移或砍为只读可观测面板**，绝不允许反噬主线资源。

---

## 运营（全程伴随）

- Demo 视频（随每个配套文档节点产出）
- 社区互动、版本发布节奏（语义化版本 + CHANGELOG）
- 主框架收口后统一做一次"开源发布"动作

---

## 风险与对策

| 风险 | 对策 |
|------|------|
| 范围过大导致每阶段都不够深 | 阶段间严格"完成定义"门禁，未达 DoD 不进下一阶段 |
| 契约在后期推翻导致返工 | Phase 0 契约版本化定义；P0~P1 允许修订并记 CHANGELOG，P2 起冻结只扩展；P1 DoD 钉死对 P2/P3 的接入点预留 |
| Benchmark 强行对标成熟框架反暴露短板 | 只对标**自家独有场景**的量化指标（token 节省率、恢复时间） |
| 配套 Web 平台吞噬主框架资源 | 主线 P4 之后启动，且可随时降级为只读可观测面板 |
| "生产级"口号落空 | 每阶段把"超时/重试/错误处理/可观测"作为 DoD 硬性条目 |

---

## 周期总览（主线 + 双配套并行）

| 阶段 | 周期 | 主线产出 | 并行配套 |
|------|------|----------|----------|
| P0 契约 | 1~2 周 | 地基 + 接口契约 | — |
| P1 内核 | 3~4 周 | 可跑的最小 Agent | — |
| P2 可靠 | 3~4 周 | 断点恢复 / 自愈 | 📄 配套二启动：《Checkpoint 设计》 |
| P3 上下文 | 4~5 周 | 可度量上下文效率 | 📄《Context Engineering》+ 压缩 Benchmark |
| P4 规划 | 3~4 周 | 任务拆解 / 并发 | 📄《预算治理对比》；🖥 配套一启动：FastAPI 包装 + SSE |
| P5 底座 | 4~5 周 | MCP / 沙箱 / 记忆 | 📄《MCP 实战》；🖥 Checkpoint 回放 + Trace 可视化 |
| P6 生产化 | 3~4 周 | 控制 / 多模型 / 可观测 / 文档 | 📄《从 0 到生产级》；🖥 MCP 市场 + 多租户/HITL 面板 |
| **主线合计** | **约 24~32 周** | **生产级框架** | **配套二全程伴随；配套一自 P4 起自然后收口** |

> 每一阶段都是"可运行、可测试、可演示"的增量；Phase 6 收口即为最终目标，而非另起炉灶。
> 主线优先：任何并行轨道都不得阻塞或拖慢主线的 DoD 门禁。
