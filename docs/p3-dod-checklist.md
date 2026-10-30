# P3（推理调度 / Context Engineering）DoD 与验收测试清单

> 依据：`docs/Roadmap.md` Phase 3 + `docs/design-decisions/00-contracts.md`（P2 起冻结规则、
> P3 接入点预留）+ `docs/design-decisions/01-loop-runtime.md`（ADR-01 loop 契约）
> + `docs/design-decisions/02-checkpoint.md`（ADR-02，压缩产物与快照/恢复的交互必须遵守其结论）。
> 使用方式：每条都有"怎么验"，全部勾选即 P3 收口；未达 DoD 不进 P4。
> **状态：0/52 未开始。**

---

## 0. 前置约束（本阶段的硬边界，违反即不算完成）

- [ ] **L1 `core/loop.py` 零修改**：压缩 / 能力视图 / 预算治理全部经 P1 预留的两个位点接入
      （`ContextAssembler.assemble()`、`tool_selector(specs, state)`）+ Agent 外层。
      验收方式：`git diff <P2收口提交>..HEAD -- stableagent/core/loop.py` 为空，结果记进本文件。
- [ ] **L2 组装器不得抛异常来表达"拒绝"**：`AgentLoop.advance()` 里 `assemble()` /
      `tool_selector()` 的调用**在 provider 的 try/except 之外**（`core/loop.py:60-61`），
      抛出即绕过 `error` 事件、直接破坏 F4。所以 `on_budget_exceeded="reject"` 必须由
      **Agent 外层**产生唯一终止事件，而不是让组装器 raise。
      验收方式：`test_d4` 式断言——reject 路径的流恰好一个终止事件且在末尾。
- [ ] **L3 运行时依赖仍为 3 个**（`pydantic` / `anyio` / `httpx`）：token 计数用**可注入估算器 +
      启发式默认实现**，不引入 `tiktoken` 等新依赖；真实 token 数只在 Provider 回报 `usage` 时
      做事后校准。验收方式：`python -c "import stableagent"` 在无额外包的环境通过 +
      `pyproject` 的 `dependencies` 条目数不变。
- [ ] **L4 默认配置零回归**：`Config()`（`assembly="passthrough"`、`compression="none"`、
      `capability_filter="static"`、`max_context_tokens=None`）下，P3 代码路径全部短路，
      事件流与 P2 逐条一致。
      验收方式：新增等价测试 `test_p3_default_config_stream_identical_to_p2`，
      与 P1 重放等价 / P2 D4 测试互证。
- [ ] **L5 压缩产物必须进快照，不能只进内存**：否则崩溃恢复后要么重算（多花一次模型调用）、
      要么与不中断运行不等价，直接违反 ADR-02 的 D4 结论。
      验收方式：开启压缩跑"崩溃→恢复"，断言恢复流与不中断流逐条一致（复用
      `tests/integration/test_crash_recovery.py` 的骨架参数化一份压缩版）。
- [ ] **L6 契约只扩展不推翻**：本阶段预声明的扩展集（逐项进 CHANGELOG）：
      1. `AgentState.context_summary: str = ""`（带默认值，旧快照缺字段即取默认）；
      2. `ContextConfig` 新增若干**带默认值**字段（保留窗口、摘要来源、最小工具数等）；
      3. **可选**新枚举成员 `EventType.CONTEXT`（默认不发事件，opt-in，见 D6）。
      除此之外不允许任何既有字段/不变式语义变化；`CONTRACT_VERSION` 抬不抬版由 ADR-03 明确决策
      并写理由（P2 先例：兼容扩展不抬版）。
- [ ] **L7 全部验收离线确定性**：压缩/预算/能力视图的测试不得依赖网络、墙钟或随机；
      模型调用一律走脚本 Provider 或注入的摘要器。
      验收方式：CI 无 secrets 也能全绿（沿用现有 workflow，不加任何 env 要求）。

## A. 度量地基：token 估算与校准（没有这一步，后面全是玄学）

- [ ] **A1 `TokenCounter` 协议 + 启发式默认实现**（`context/tokens.py`）：
      `count(text) -> int`、`count_messages(messages) -> int`、`count_tools(specs) -> int`；
      默认启发式 = `ceil(chars / 4)` + 每条消息固定开销，**纯函数、可单测、无依赖**。
- [ ] **A2 工具规格也要计入成本**：`count_tools` 对 `ToolRegistry.specs()` 的 JSON 结构计价
      （name/description/parameters）。能力视图的收益必须算这块，否则等于没测。
- [ ] **A3 估算可注入可替换**：Agent/Engine 接受 `token_counter=`，缺省用启发式；
      测试注入"每条 1 token"的桩，断言预算判定随之改变（证明决策链真的消费这个量）。
- [ ] **A4 事后校准**：Provider 回报 `usage.prompt_tokens` 时，记录 `(估算, 真实)` 对；
      `state.usage` 里以 `context_tokens_est` / `prompt_tokens` 并存（`usage` 是 `dict[str,int]`，
      **不构成契约变更**）。
- [ ] **A5 估算误差进 benchmark 报告**：`benchmarks` 输出估算相对真实值的 MAE 与偏差方向；
      README/blog 只引用带误差标注的数字。
- [ ] **A6 单测**：同一输入两次计数相等（确定性）；空消息、超长消息、非 ASCII、
      JSON schema 嵌套这几类边界各 1 条。

## B. 历史压缩（`context/compression.py`）

- [ ] **B1 `truncation`**：保留 system + 最近 `keep_recent_messages` 条，丢弃最旧；
      **不得拆散 `tool_call` / `tool_result` 配对**（截断点落在配对中间会让 Provider 报错）。
      单测断言：任意截断结果里不存在"有 `tool_calls` 的 assistant 消息其后没有对应 tool 消息"。
- [ ] **B2 `summary`**：把被折叠的历史压成一条摘要，写入 `AgentState.context_summary`（L5），
      由 assembler 在发给 Provider 时替换原始旧消息段。
      摘要生成器**可注入**：默认实现是**离线抽取式**（对 `Evidence` 的 `output`/`error` 与
      assistant 文本做定长摘录，确定性、零模型调用）；接真实模型的实现经 `summariser=` 注入。
- [ ] **B3 `hybrid`**：先折叠工具结果（`Evidence` 文本是 token 大头），再对更早的消息做摘要，
      最后才截断；三档顺序有单测锁死（顺序换了，质量保留率就变了）。
- [ ] **B4 注册表接入**：`COMPRESSIONS: dict[str, type[...]]` + `resolve_compression(config)`，
      与 `ASSEMBLERS` 同构；未知名 `KeyError` 带出已注册清单（照 `resolve_assembler` 的错误文案）。
- [ ] **B5 质量保留的**确定性**度量**：固定语料任务里在已知位置埋 N 条"答案必需事实"，
      断言每种策略下：(a) 事实是否仍在组装后的上下文里（fact-retention 率，子串判定）；
      (b) 最终答案的可断言部分正确。指标 = `token 节省率` 与 `质量保留率` **成对**出现，
      禁止只报节省率。
- [ ] **B6 幂等**：同一状态重复 `assemble()` 不重复摘要、不使 `context_summary` 增长
      （否则每步多花一次模型调用）。单测：连调两次，第二次输出与 `context_summary` 均不变。
- [ ] **B7 旧快照兼容**：`context_summary` 缺省 `""`，v1.0.0 / v1.1.0 fixture 在 P3 代码下
      仍可 `migrate_state()` → 恢复（扩 `tests/fixtures/`，不改 `AgentState.loads` 严格性）。
- [ ] **B8 与 checkpoint 的交互**：摘要进快照后 payload 变大，`payload_bytes` 与恢复耗时的
      变化要在 F5 里量化（不许悄悄劣化 P2 已发布的基线）。

## C. 动态能力视图（`context/capability.py`）

- [ ] **C1 `CAPABILITY_FILTERS` 注册表**：键为 `ContextConfig.capability_filter`，
      `static`（全量，默认，行为不变）之外至少 1 个真筛选器（任务相关性：关键词/最近使用/
      工具族前缀，任选实现但须确定性排序）。
- [ ] **C2 经 `tool_selector` 位点接入**，不改 loop、不在 loop 内联判据（L1）。
- [ ] **C3 召回护栏（本节的硬指标）**：筛选**永不**丢掉本次运行里模型实际要调的工具。
      做法：已调用过的工具名进常驻集合（resident set），断言筛选结果 ⊇ resident set；
      违反即 reject 该策略，不允许"平均召回率"糊过去。
- [ ] **C4 兜底**：筛选结果为空时回退全量并计数（空工具列表 = 直接废掉 Agent）。
- [ ] **C5 排序稳定**：输出顺序由名称字典序决定，不依赖 dict 插入序或集合哈希
      （否则 prompt 变化会污染 benchmark 复现）。
- [ ] **C6 计量闭环**：被裁掉的工具 token 数记进 `state.usage`（如 `tools_tokens_dropped`），
      让"省了多少"可被断言，而不是只由脚本作者口头声明。

## D. 预算治理（`context/budget.py` + `context/engine.py`）

- [ ] **D1 `ContextEngine` 抽象**（`context/engine.py`）：组合
      `压缩 → 能力视图 → 预算判定`，对 loop 暴露 `assemble()` 与 `select()` 适配器，
      对外层暴露 `preflight(state, specs) -> BudgetDecision`。**同一个引擎实例**同时服务两侧，
      杜绝"外层判的量与真发的量不一致"。
- [ ] **D2 `max_context_tokens` 语义钉死**：比较对象 = 本次将要发出的
      `messages(组装后) + tool specs` 的估算总量（A1+A2），不是历史全长。
      边界单测：恰好等于上限（不触发动作）、上限 -1（触发）。
- [ ] **D3 `compress`**：按 B3 顺序逐级压缩直到达标；压到位则继续，压不动了走 D5 升级。
- [ ] **D4 `degrade`**：先裁工具规格（C 的能力视图加严），再压历史；每级动作可观测
      （`ContextReport` 记录 `action`、`levels_tried`、`before/after` 估算）。
- [ ] **D5 `reject`**：**在 Agent 外层**产生唯一 `error` 事件 + `AgentStatus.FAILED`
      （L2 的具体落点：外层 preflight 命中即不调 `drive()`），错误详情可判读、
      且**不进重试**（属 `non_retryable`：再跑一遍预算判定必然同样超限——这条要写进 ADR-03 并被
      单测锁住：reject 后 `provider_calls` 不增加）。
- [ ] **D6 可观测性**：默认**不发**每步上下文事件（沿用 P2"静默落盘 + 只读属性"的先例），
      `agent.context_reports` 提供历史；若要把 `EventType.CONTEXT` 变成 opt-in，
      须证明 F4 未被破坏，并在 L6 里登记为新增枚举成员。
- [ ] **D7 与 `ControlConfig.max_steps` / `max_total_tokens` 的关系写清**：
      预算超限与步数耗尽是两条独立终止路径，各自有测试，不得互相掩盖（F2 的先例）。
- [ ] **D8 单测三态齐全**：compress / degrade / reject 各 ≥2 条（达标路径 + 压不动路径），
      Roadmap 明确要求"预算超限时行为确定"。

## E. Evidence 增强与追溯（Roadmap 交付物 `tools/evidence.py`）

- [ ] **E1 位置决策成文**：`Evidence` 现在 `tools/registry.py`。要么按 P1 `ToolCall` 先例
      移到 `tools/evidence.py` 并在 `registry.py` 保留重导出（导入路径不破），
      要么在 ADR-03 记录"不迁"的理由。**不许默默略过 Roadmap 的交付物清单。**
- [ ] **E2 增强字段全带默认值**（L6）：可选 `step` / `origin` / 父子调用链字段，
      用于压缩时判断"哪条证据可折叠"。既有不变式 `ok == (error is not None)` 不得被破坏
      （扩一条 property 测试）。
- [ ] **E3 压缩以 Evidence 文本为主要目标**：工具结果通常最占 token 且最可摘要；
      B3 的顺序必须体现这一点，并有"摘要后 user/assistant 原文未被改写"的断言。
- [ ] **E4 截断安全性**：任何压缩/截断都不得产生"孤儿 `tool_call`"（同 B1 判据），
      把这条列为 `ContextReport` 的自检项，每次组装都验。

## F. Benchmark 与对外证据（Roadmap 的量化指标）

- [ ] **F1 `benchmarks/context_compression_benchmark.py`**：固定长任务（离线脚本 Provider），
      输出每策略 `估算 token 节省率` + `质量保留率`（B5 的事实命中）+ 真实 `usage` 校准误差（A5）。
      结论句式必须是"hybrid 比 truncation 省 X%，事实保留率从 A 到 B"这种成对表述。
- [ ] **F2 工具爆炸场景**：注册 ≥200 个工具规格，测能力视图的注入 token 下降幅度（C6）
      与召回护栏（C3）同时成立；这是 Roadmap 点名的独家场景，只对标自家基线。
- [ ] **F3 变量隔离**：P2 的教训——`--steps` 扫描会同时改变"条数"与"单条大小"两个量。
      F1/F2 每项测量只动一个变量，其余钉死；清单里明确写出每档控制了哪些量。
- [ ] **F4 数字可复现**：benchmark 打印所用参数与命令；README/blog 引用的数字标注
      "本机/日期/命令"，并注明波动范围（P2 已出现同配置跨轮次 ±30% 的情况）。
- [ ] **F5 不劣化 P2 基线**：摘要进快照后重跑 `benchmarks/recovery_time_benchmark.py`，
      `payload_bytes` 与恢复耗时变化量化入档（B8）。

## G. 零回归与工程门禁

- [ ] **G1** L4 默认配置等价测试绿色；P1/P2 既有测试**一条都不许改断言来迁就 P3**
      （若必须改，须在 ADR-03 说明为什么那是契约变更而非实现细节）。
- [ ] **G2 CI 心脏模块扩列**：`.github/workflows/ci.yml` 的 `FOCUS` 增加 `context/` 各模块，
      维持 ≥90% 门槛（心脏代码从严的既有取向）；扩列后本地先跑一遍确认达标。
- [ ] **G3 门禁**：`ruff check stableagent tests examples benchmarks` /
      `ruff format --check ...` / `mypy` / `pytest` 全绿，全库覆盖率 ≥70%（现 98.2% 不得下滑）。
- [ ] **G4 CHANGELOG**：P3 契约扩展逐条标注非破坏性；`00-contracts.md` 增补"P3 扩展记录"，
      并更新"组装位点"索引（把 `compression` / `capability` / `budget` / `engine` 的文件路径钉进去）。
- [ ] **G5 ADR-03** `docs/design-decisions/03-context-engineering.md`：估算 vs 真实 token、
      压缩产物入快照的理由与代价、reject 走外层而非抛异常、能力视图的召回护栏设计、
      与 checkpoint/恢复的交互；含**被否决方案**（至少：在 loop 内联压缩、组装器抛
      `BudgetExceeded`、引入 tiktoken、摘要只放内存、用 LLM 当质量评审）。
- [ ] **G6 CI 全绿**：3.10/3.12 矩阵 + 心脏闸门（最后一条，需真推送）。

## H. 配套二并行轨道（Roadmap：P3 期间启动）

- [ ] **H1** `docs/blog/02-context-engineering.md` 初稿：《Context Engineering：把 token 效率
      做成可度量的能力》，素材取 F1/F2 实测与 ADR-03。
- [ ] **H2** README 更新：Roadmap 表 P3 状态、能力段从"未来"改为"已交付 + 数字"，
      且**不得**把未做的能力写进 description/feature 列表（当前 README 已刻意如此，别退步）。

---

## 验收顺序建议

```
L1~L7（钉边界，尤其 L2 与 L5，这两条决定了后面所有设计的形状）
 → A（度量：没有可信的 token 量，B/C/D 全是自证）
 → B（压缩）与 C（能力视图）可并行，B 先行因其决定快照字段
 → D（预算，依赖 A+B+C 三者的量与动作）
 → E（Evidence，穿插进行；E1 的位置决策越早越好）
 → F（benchmark 与对外证据，全部实现完成后）
 → G（门禁与文档收口）→ H（blog / README）
```

**先做 A 的理由**：预算判据依赖一个可信的 token 量。若先写 D 再补 A，
所有预算阈值测试都要重写——P2 的经验是"判据不牢的测试比没测试更贵"。

## 已知风险与对策（开工前先认，不要中途发现）

| 风险 | 对策 |
|---|---|
| 估算 token ≠ 真实 BPE token，优化了错的量 | A4/A5：每次比对 Provider 回报的 `usage`，误差进 benchmark；结论只报相对趋势 |
| 摘要需要模型调用 → 破坏离线确定性测试（L7） | B2：默认抽取式（零模型调用）；LLM 摘要器是注入项，测试用桩 |
| 能力视图裁掉必需工具，Agent 变笨 yet 数字更漂亮 | C3 召回护栏（硬断言）+ C4 兜底；F2 必须成对报告节省率与召回 |
| 压缩产物入快照，快照变大、恢复变慢 | B8/F5：量化并守住 P2 基线；必要时 `history_limit` 与摘要长度进配置 |
| reject 语义诱导"在组装器里抛异常"，破坏 F4 | L2 写成前置硬约束，并在 D5 用测试锁死（外层产生唯一终止事件） |
| 摘要替换历史后与 P2 的 D4 恢复等价冲突 | L5 + B6 幂等 + F 的压缩版崩溃恢复用例，逐条比对 |

## 验收记录

（待填：每条的验证命令与真实输出，格式沿用 `docs/p2-dod-checklist.md` 的"验收记录/复核记录"两节。）
