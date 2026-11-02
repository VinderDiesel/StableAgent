# P3（推理调度 / Context Engineering）DoD 与验收测试清单

> 依据：`docs/Roadmap.md` Phase 3 + `docs/design-decisions/00-contracts.md`（P2 起冻结规则、
> P3 接入点预留）+ `docs/design-decisions/01-loop-runtime.md`（ADR-01 loop 契约）
> + `docs/design-decisions/02-checkpoint.md`（ADR-02，压缩产物与快照/恢复的交互必须遵守其结论）。
> 使用方式：每条都有"怎么验"，全部勾选即 P3 收口；未达 DoD 不进 P4。
> **状态：11/52 进行中（A 节度量地基 + B 节历史压缩已落，L1 复核 0 diff，见文末进度记录）。**

---

## 0. 前置约束（本阶段的硬边界，违反即不算完成）

- [x] **L1 `core/loop.py` 零修改**：压缩 / 能力视图 / 预算治理全部经 P1 预留的两个位点接入
      （`ContextAssembler.assemble()`、`tool_selector(specs, state)`）+ Agent 外层。
      验收方式：`git diff <P2收口提交>..HEAD -- stableagent/core/loop.py` 为空，结果记进本文件。
      （A+B 节落地后复核：`git diff 46441f0 -- stableagent/core/loop.py` 输出 0 行。）
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

- [x] **A1 `TokenCounter` 协议 + 启发式默认实现**（`context/tokens.py`）：
      `count(text) -> int`、`count_messages(messages) -> int`、`count_tools(specs) -> int`；
      默认启发式 = `ceil(chars / 4)` + 每条消息固定开销，**纯函数、可单测、无依赖**。
      （落地补充：CJK/全角按 ≈1 token/字计价，否则中文任务上估算会系统性偏低。）
- [x] **A2 工具规格也要计入成本**：`count_tools` 对 `ToolRegistry.specs()` 的 JSON 结构计价
      （name/description/parameters）。能力视图的收益必须算这块，否则等于没测。
      （`sort_keys` 定序已锁：键序不同计数必须相等。）
- [ ] **A3 估算可注入可替换**：Agent/Engine 接受 `token_counter=`，缺省用启发式；
      测试注入"每条 1 token"的桩，断言预算判定随之改变（证明决策链真的消费这个量）。
      （协议面与 `resolve_token_counter` 的缺方法早失败已完成并有测试；
      **Agent/Engine 的注入参数随 D1 一起落**，故本条不勾。）
- [ ] **A4 事后校准**：Provider 回报 `usage.prompt_tokens` 时，记录 `(估算, 真实)` 对；
      `state.usage` 里以 `context_tokens_est` / `prompt_tokens` 并存（`usage` 是 `dict[str,int]`，
      **不构成契约变更**）。**未做**：写入点在引擎与外层（D1/D5），那里才是估算与真实值同时可见
      的唯一位置；未接线前不占名、不写半成品。
- [ ] **A5 估算误差进 benchmark 报告**：`benchmarks` 输出估算相对真实值的 MAE 与偏差方向；
      README/blog 只引用带误差标注的数字。
- [x] **A6 单测**：同一输入两次计数相等（确定性）；空消息、超长消息、非 ASCII、
      JSON schema 嵌套这几类边界各 1 条。

## B. 历史压缩（`context/compression.py`）

> **本节的两条设计决策（写在这里，因为代码形状就是被它俩决定的）**
> 1. **压缩是快照的纯函数**：`ContextAssembler.assemble()` 只拿到 `state.messages`
>    （`core/loop.py:60`），拿不到快照的其余部分，也写不回快照。所以默认路径不在进程里
>    留任何缓存——同一份 `messages` 必得同一份上下文，L5 因此以"根本没有第二份状态"的形式成立。
>    缓存的载体是上下文里的一条 `role="system"` + `name="context_summary"` 标记消息：
>    输入里有它就当作"已折叠段的既有成果"，输出至多一条，绝不叠加。
> 2. **压缩的开关只有一个：`ContextConfig.compression`**。`assembly` 保持 `passthrough`
>    时压缩照样生效（`resolve_assembler` 组装 `CompressedAssembler`），避免出现
>    "配了压缩却因为 assembly 没换而静默失效"。`compression="none"`（默认）返回 None，路径短路。

- [x] **B1 `truncation`**：保留 system + 最近 `keep_recent_messages` 条，丢弃最旧；
      **不得拆散 `tool_call` / `tool_result` 配对**（截断点落在配对中间会让 Provider 报错）。
      单测断言：任意截断结果里不存在"有 `tool_calls` 的 assistant 消息其后没有对应 tool 消息"。
      （`partition()` 的边界回退规则：保留段首条若是 `tool`，边界往前挪到它的 assistant。
      验收：`test_b1_truncation_never_splits_tool_pairs` 对 keep=0..12 全扫，
      `test_b1_boundary_rule_holds_for_all_strategies` 对三档策略同样全扫——同一不变式跑 39 组参数。
      额外常驻规则：**首条 user 消息（任务本体）也常驻**，否则截断会把"当初要干什么"丢掉。）
- [x] **B2 `summary`**：把被折叠的历史压成一条摘要，写入 `AgentState.context_summary`（L5），
      由 assembler 在发给 Provider 时替换原始旧消息段。
      摘要生成器**可注入**：默认实现是**离线抽取式**（对 `Evidence` 的 `output`/`error` 与
      assistant 文本做定长摘录，确定性、零模型调用）；接真实模型的实现经 `summariser=` 注入。
      （已落：`ExtractiveSummariser` + `SummaryCompressor(config, summariser)`，
      注入用例 `test_b2_injected_summariser_replaces_default` 断言摘要器收到的正是折叠段本身。
      **`AgentState.context_summary` 字段的写入点随 D1 的 `ContextEngine` 接线**：
      loop 位点看不到也写不了快照（见上面决策 1），外层在每次进入 `_pipeline` 的边界压缩一次才是
      "一次模型调用换 N 步复用"的落点。B2 的"摘要进上下文"已闭环，"摘要进快照"归 D1，
      与 A3/A4/A5 的处理方式一致：未接线前不写半成品。）
- [x] **B3 `hybrid`**：先折叠工具结果（`Evidence` 文本是 token 大头），再对更早的消息做摘要，
      最后才截断；三档顺序有单测锁死（顺序换了，质量保留率就变了）。
      （顺序证据用两条测试钉死，不靠注释：`test_b3_hybrid_level_order_is_locked` 断言
      `levels == ("tool_excerpt", "summary", "truncation")`；
      `test_b3_summary_sees_excerpted_tool_results_not_originals` 用记录型摘要器证明
      摘要器看到的工具文本已被摘录过——即"先折工具"确实发生在"再摘要"之前。
      第一档只动折叠段，保留段的证据保持原样（`test_b3_recent_window_stays_verbatim`）。）
- [x] **B4 注册表接入**：`COMPRESSIONS: dict[str, type[...]]` + `resolve_compression(config)`，
      与 `ASSEMBLERS` 同构；未知名 `KeyError` 带出已注册清单（照 `resolve_assembler` 的错误文案）。
      （`test_b4_*` 6 条：注册表内容、未知名文案、默认短路、单一开关、
      既有 assembly 报错文案不变、压缩经 loop 位点真的到达 Provider
      ——与 passthrough 同一脚本对跑，第三条调用的消息数更少且带摘要占位。）
- [x] **B5 质量保留的**确定性**度量**：固定语料任务里在已知位置埋 N 条"答案必需事实"，
      断言每种策略下：(a) 事实是否仍在组装后的上下文里（fact-retention 率，子串判定）；
      (b) 最终答案的可断言部分正确。指标 = `token 节省率` 与 `质量保留率` **成对**出现，
      禁止只报节省率。
      （`test_b5_retention_pairs_with_savings_on_fixed_corpus`：13 条消息、6 个 `FACT-i` 埋在工具结果里，
      三档策略各算 (节省率, 保留率) 并断言相对关系——truncation 省最多只留 2/6，
      summary 保留 6/6 但节省率更低，hybrid 保留 6/6 且节省率 ≥ summary。
      绝对数值留给 F 节的 benchmark 报告，这里锁的是**关系**，防止以后改坏了没人知道。）
- [x] **B6 幂等**：同一状态重复 `assemble()` 不重复摘要、不使 `context_summary` 增长
      （否则每步多花一次模型调用）。单测：连调两次，第二次输出与 `context_summary` 均不变。
      （比"连调两次"更强的形式：`compress(compress(m).kept) == compress(m)` 且第二次
      `folded == 0`（`test_b6_compress_is_idempotent`，三档参数化）。
      增量折叠的另一侧也测了：新段落进来时旧摘要原样保留、只并入新摘录（`test_b2_second_fold_*`），
      合并不超过 `max_summary_chars`。）
- [x] **B7 旧快照兼容**：`context_summary` 缺省 `""`，v1.0.0 / v1.1.0 fixture 在 P3 代码下
      仍可 `migrate_state()` → 恢复（扩 `tests/fixtures/`，不改 `AgentState.loads` 严格性）。
      （`test_b7_*`：无该字段的 v1.1.0 payload 直接 `model_validate` → `""` 且往返无损；
      v1.0.0 fixture 经 `migrate_state()` 仍恢复得到、`context_summary == ""`。
      **没动 `recovery.py`**：字段带默认值即兼容，往 `_backfill_v1_1_0` 里加 `setdefault`
      是重复劳动，还会让"迁移函数"和"模型默认值"变成两处需要同步的事实源。）
- [ ] **B8 与 checkpoint 的交互**：摘要进快照后 payload 变大，`payload_bytes` 与恢复耗时的
      变化要在 F5 里量化（不许悄悄劣化 P2 已发布的基线）。
      （待 D1 把摘要写进快照后与 F5 一并测：现在 `messages` 里多出的摘要消息只影响
      发给 Provider 的上下文，不影响快照，量了会误导。）

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

### 2026-11-02 · A 节度量地基（A1 / A2 / A6 勾选）

新增 `stableagent/context/tokens.py`（估算协议 + 启发式实现 + `count_context` +
`resolve_token_counter`）与 `tests/unit/test_token_counter.py`（15 条）。

```
$ .venv/bin/ruff check stableagent tests examples benchmarks   → All checks passed!
$ .venv/bin/ruff format --check ...                            → 51 files already formatted
$ .venv/bin/mypy                                               → Success: no issues found in 23 source files
$ .venv/bin/pytest -q                                          → 141 passed, 1 skipped / 98.19%
$ .venv/bin/pytest --cov=stableagent/context/tokens            → tokens.py 51 stmts 0 miss 100%
```

过程中改的两处判断，记录以免被后人当成随手改：

1. **启发式必须分域计价**。原计划 `ceil(chars/4)` 一刀切，在中文任务上会把
   "恢复"两个字估成 0.5→1 token 的级别，而 BPE 实际接近 1 token/字——预算阈值若按此估算
   设定，会在中文语料上**系统性高估容量**，等于把超限判断推迟到真实请求里爆掉。
   现实现按 CJK/全角 ≈1 token/字、其余 ≈4 字/1 token，`test_a6_wide_chars_cost_more_per_char_than_ascii`
   锁住方向。
2. **计数必须定序**。`_dump` 用 `sort_keys=True`：否则同一个工具规格因键序不同给出不同计数，
   "裁掉 N 个工具省了多少 token"就掺进了哈希噪声，F1/F2 的差值不可复现。

A3 只落了协议与早失败校验，`token_counter=` 的注入参数留给 D1 的引擎；A4/A5 未动——
写入点在引擎与 Agent 外层，未接线前不写半成品（避免 P2 那种"数字先于链路存在"的尴尬）。

### 2026-11-02 · B 节历史压缩（B1 / B2 / B3 / B4 / B5 / B6 / B7 勾选）

新增 `stableagent/context/compression.py`（三档压缩 + `Summariser` 协议 +
`ExtractiveSummariser` + `COMPRESSIONS` / `resolve_compression`）、
`tests/unit/test_compression.py`（56 条）；扩展 `assembly.py`（`CompressedAssembler`，
`resolve_assembler` 多收一个可选 `summariser=`）与 `core/state.py`
（`ContextConfig` 三个带默认值字段、`AgentState.context_summary: str = ""`）。

```
$ git diff 46441f0 -- stableagent/core/loop.py                 → 0 行（L1 复核）
$ .venv/bin/ruff check stableagent tests examples benchmarks   → All checks passed!
$ .venv/bin/ruff format --check ...                            → 53 files already formatted
$ .venv/bin/mypy                                               → Success: no issues found in 24 source files
$ .venv/bin/pytest -q                                          → 197 passed, 1 skipped / 98.49%
$ .venv/bin/pytest --cov=stableagent/context                   → compression.py 111 stmts 0 miss 100%
                                                                  assembly.py 22 stmts 0 miss 100%
$ /tmp/sa-3.10/bin/python -m pytest -q                         → 197 passed, 1 skipped
$ /tmp/sa-3.12/bin/python -m pytest -q                         → 197 passed, 1 skipped
```

三处判断，记下来免得被当成随手改：

1. **压缩不在进程里缓存**。 assembler 位点只拿到 `state.messages`，写不回快照，
   所以任何"内存缓存 + 快照字段"的双写方案都会造出第二个事实源，L5/D4 反而更脆。
   改成纯函数 + 上下文内的 `context_summary` 标记消息承载既有成果：恢复后不需要重算，
   也无处可算错。B6 的幂等测试因此能写成更强的形式（二次压缩输出逐条相等、`folded == 0`）。
2. **保留段边界只往前挪、不往后挪**。`partition()` 遇到"保留段首条是 `tool`"时把边界
   前移到它的 `assistant`，代价是可能多留几条消息（超预算一点点），
   换来的是"永不发出 Provider 会拒绝的上下文"。反过来往后挪会直接产出残缺配对。
3. **没往 `_backfill_v1_1_0` 加 `setdefault("context_summary", "")`**。字段带默认值即兼容，
   加进去只是把同一事实写两处；B7 用测试而不是迁移函数来钉这个结论。

未勾选的两项各有归属：**B8**（摘要进快照后 `payload_bytes` / 恢复耗时对 P2 基线的影响）
等 D1 接线后与 F5 一并测；**B2 的"摘要进快照"那一半**同样落在 D1 的边界压缩上。
