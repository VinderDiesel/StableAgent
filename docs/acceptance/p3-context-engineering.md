# P3（推理调度 / Context Engineering）DoD 与验收测试清单

> 依据：`docs/Roadmap.md` Phase 3 + `docs/design-decisions/00-contracts.md`（P2 起冻结规则、
> P3 接入点预留）+ `docs/design-decisions/01-loop-runtime.md`（ADR-01 loop 契约）
> + `docs/design-decisions/02-checkpoint.md`（ADR-02，压缩产物与快照/恢复的交互必须遵守其结论）。
> 使用方式：每条都有"怎么验"，全部勾选即 P3 收口；未达 DoD 不进 P4。
> **状态：52/52 正式收口（2026-11-02，CI run 41384598912 在 Python 3.10 / 3.12 两个 job 全绿）。**

---

## 0. 前置约束（本阶段的硬边界，违反即不算完成）

- [x] **L1 `core/loop.py` 零修改**：压缩 / 能力视图 / 预算治理全部经 P1 预留的两个位点接入
      （`ContextAssembler.assemble()`、`tool_selector(specs, state)`）+ Agent 外层。
      验收方式：`git diff <P2收口提交>..HEAD -- stableagent/core/loop.py` 为空，结果记进本文件。
      （A+B 节落地后复核：`git diff 46441f0 -- stableagent/core/loop.py` 输出 0 行；
      D 节落地后同样 0 行——预算治理的三个动作全在引擎与 Agent 外层，loop 一行未动。）
- [x] **L2 组装器不得抛异常来表达"拒绝"**：`AgentLoop.advance()` 里 `assemble()` /
      `tool_selector()` 的调用**在 provider 的 try/except 之外**（`core/loop.py:60-61`），
      抛出即绕过 `error` 事件、直接破坏 F4。所以 `on_budget_exceeded="reject"` 必须由
      **Agent 外层**产生唯一终止事件，而不是让组装器 raise。
      验收方式：`test_d4` 式断言——reject 路径的流恰好一个终止事件且在末尾。
      （已落：`ContextEngine.assemble/select` 全程不 raise（超限只写报告与标志），拒绝由
      `_pipeline` 开头的 `preflight` 分支产生。`test_d5_reject_terminates_at_entry_without_touching_the_provider`
      断言事件流恰为 `[ERROR]` 且 `provider.calls == []`；`test_d7_budget_and_max_steps_are_two_separate_terminations`
      再证两条终止路径互不掩盖。）
- [x] **L3 运行时依赖仍为 3 个**（`pydantic` / `anyio` / `httpx`）：token 计数用**可注入估算器 +
      启发式默认实现**，不引入 `tiktoken` 等新依赖；真实 token 数只在 Provider 回报 `usage` 时
      做事后校准。验收方式：`python -c "import stableagent"` 在无额外包的环境通过 +
      `pyproject` 的 `dependencies` 条目数不变。
- [x] **L4 默认配置零回归**：`Config()`（`assembly="passthrough"`、`compression="none"`、
      `capability_filter="static"`、`max_context_tokens=None`）下，P3 代码路径全部短路，
      事件流与 P2 逐条一致。
      验收方式：新增等价测试 `test_p3_default_config_stream_identical_to_p2`，
      与 P1 重放等价 / P2 D4 测试互证。
      （落地形式比"在 P3 里断言没建引擎"更强：`tests/fixtures/l4_p2_default_stream.json`
      是在 **P2 收口提交 `46441f0` 的工作树**里跑 `tests/l4_default_stream_dump.py` 生成的金标准
      （事件流 + 每次发给 Provider 的 messages/tools + 终态快照 + `CONTRACT_VERSION` +
      `EventType` 全集，7252 字节），当前代码复现它并逐字节相等。重新生成步骤写在该模块 docstring 里。
      另一侧的结构断言：`test_d6_default_config_builds_no_engine_and_stays_silent`。）
- [x] **L5 压缩产物必须进快照，不能只进内存**：否则崩溃恢复后要么重算（多花一次模型调用）、
      要么与不中断运行不等价，直接违反 ADR-02 的 D4 结论。
      验收方式：开启压缩跑"崩溃→恢复"，断言恢复流与不中断流逐条一致（复用
      `tests/integration/test_crash_recovery.py` 的骨架参数化一份压缩版）。
      （已落：`ContextEngine.fold(state) -> state'` 在**每段入口**（`_pipeline` 开头）折一次，
      摘要的载体是 `messages` 里那条 `name=context_summary` 标记消息——**不给 `AgentState`
      加第二个字段**，理由见 L6。`test_l5_recovered_stream_with_compression_is_event_identical`
      除了逐条比对事件流，还多断言 `restarted.calls[0][0] == steady.calls[2][0]`
      （恢复后第一次发给 Provider 的上下文，与不中断跑的那一次完全相同，等于"零重算"）。
      合流性另有 `test_l5_folding_is_confluent_across_segment_boundaries` 兜住分段折叠；
      注入式 LLM 摘要器不满足合流，那正是"必须落盘"的适用场景，测试里写明了这条边界。）
- [x] **L6 契约只扩展不推翻**：本阶段预声明的扩展集（逐项进 CHANGELOG）：
      1. ~~`AgentState.context_summary: str = ""`~~ **实际未加此字段**（D1 收口时删掉）。
         理由：摘要已有唯一载体——`messages` 里那条 `role="system"` + `name="context_summary"`
         标记消息（`fold()` 把它写进快照，`compress()` 把它当既有成果复用）。再加一个
         `AgentState` 字段就是第二份事实源，会漂移、要同步、还得写迁移函数；
         `test_b7_summary_travels_in_messages_not_in_a_second_field` 直接断言
         `"context_summary" not in state.model_dump()`。兼容面因此更小：旧快照一个字段都不用补。
      2. `ContextConfig` 新增若干**带默认值**字段（保留窗口、摘要来源、最小工具数等）；
      3. **可选**新枚举成员 `EventType.CONTEXT`（默认不发事件，opt-in，见 D6）。
      除此之外不允许任何既有字段/不变式语义变化；`CONTRACT_VERSION` 抬不抬版由 ADR-03 明确决策
      并写理由（P2 先例：兼容扩展不抬版）。
      （本条待 ADR-03（G5）落笔后勾选：抬版的决策依据是它要求的，不在这里自说自话。
      目前已成立的部分：`CONTRACT_VERSION` 仍为 `1.1.0`（由 L4 金标准里的
      `contract_version` 字段把关，抬版会当场让这条测试红），且未新增 `EventType` 成员。）
- [x] **L7 全部验收离线确定性**：压缩/预算/能力视图的测试不得依赖网络、墙钟或随机；
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
- [x] **A3 估算可注入可替换**：Agent/Engine 接受 `token_counter=`，缺省用启发式；
      测试注入"每条 1 token"的桩，断言预算判定随之改变（证明决策链真的消费这个量）。
      （已落：`ContextEngine(config, token_counter=)` 与 `make_agent(..., token_counter=)` 都吃注入。
      `test_a3_the_injected_counter_is_the_one_that_decides`：同一份上下文、同一个上限 500，
      启发式判超限、`FlatCounter` 判达标——结论真的跟着注入的量走，不是配了个没人读的参数。
      `test_a3_a_counter_missing_a_method_fails_early`：缺方法的桩 `TypeError` 且点名缺哪个，
      不留到第 N 步才炸。）
- [x] **A4 事后校准**：Provider 回报 `usage.prompt_tokens` 时，记录 `(估算, 真实)` 对；
      `state.usage` 里以 `context_tokens_est` / `prompt_tokens` 并存（`usage` 是 `dict[str,int]`，
      **不构成契约变更**）。
      （已落，形态与预想不同：`prompt_tokens` 本来就在 `state.usage` 里（Provider 原样累加），
      再往快照写一份 `context_tokens_est` 就是同一个量的两处记录、且要为它动契约——估算值改放在
      `ContextReport.estimated_prompt_tokens` / `observed_prompt_tokens` 成对出现
      （`ContextEngine.observe()` 在每步快照产出后按 `step` 去重取**增量**，
      `preflight()` 在段入口重置基线，跨段不会把上一段的量算进本段）。
      `test_a4_observed_usage_pairs_with_the_estimate_per_step` 断言三对 `(估算, 真实)`；
      `test_a4_calibration_is_reporting_only_and_never_feeds_back` 让 Provider 谎报 usage，
      断言决策序列逐条不变——校准不回灌判定，否则快照之外的量会左右行为（违反 L5）。）
- [x] **A5 估算误差进 benchmark 报告**：`benchmarks` 输出估算相对真实值的 MAE 与偏差方向；
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
- [x] **B2 `summary`**：把被折叠的历史压成一条摘要，写进快照（L5；载体是 `messages` 里那条
      标记消息，不是 `AgentState` 的新字段——理由见 L6 第 1 条），
      由 assembler 在发给 Provider 时替换原始旧消息段。
      摘要生成器**可注入**：默认实现是**离线抽取式**（对 `Evidence` 的 `output`/`error` 与
      assistant 文本做定长摘录，确定性、零模型调用）；接真实模型的实现经 `summariser=` 注入。
      （已落：`ExtractiveSummariser` + `SummaryCompressor(config, summariser)`，
      注入用例 `test_b2_injected_summariser_replaces_default` 断言摘要器收到的正是折叠段本身。
      **"摘要进快照"那一半已随 D1 的 `ContextEngine.fold()` 收口**：
      loop 位点看不到也写不了快照（见上面决策 1），外层在每次进入 `_pipeline` 的边界压缩一次才是
      "一次模型调用换 N 步复用"的落点（验收见 L5 与 `test_l5_*`）。）
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
- [x] **B6 幂等**：同一状态重复 `assemble()` 不重复摘要、不使折叠前缀（`context_summary`
      标记消息，见 L6 第 1 条）增长
      （否则每步多花一次模型调用）。单测：连调两次，第二次输出与其摘要均不变。
      （比"连调两次"更强的形式：`compress(compress(m).kept) == compress(m)` 且第二次
      `folded == 0`（`test_b6_compress_is_idempotent`，三档参数化）。
      增量折叠的另一侧也测了：新段落进来时旧摘要原样保留、只并入新摘录（`test_b2_second_fold_*`），
      合并不超过 `max_summary_chars`。）
- [x] **B7 旧快照兼容**：旧快照不含折叠前缀也能载入，v1.0.0 / v1.1.0 fixture 在 P3 代码下
      仍可 `migrate_state()` → 恢复（扩 `tests/fixtures/`，不改 `AgentState.loads` 严格性）。
      （`test_b7_*` 三条：没有标记消息的 v1.1.0 payload 直接 `model_validate` → 无折叠前缀、
      往返无损（`AgentState.loads(state.snapshot()) == state`）；
      `test_b7_summary_travels_in_messages_not_in_a_second_field` 断言摘要只活在 `messages` 里
      （`"context_summary" not in state.model_dump()`）；
      v1.0.0 fixture 经 `migrate_state()` 仍恢复得到、无折叠前缀。
      **没动 `recovery.py`**：P3 没给 `AgentState` 加字段，迁移函数压根不需要 `setdefault`——
      这条比原计划更省：既没有"迁移函数"和"模型默认值"两处同步的问题，旧快照也没有任何新字段要补。）
- [x] **B8 与 checkpoint 的交互**：摘要进快照后 payload 变大，`payload_bytes` 与恢复耗时的
      变化要在 F5 里量化（不许悄悄劣化 P2 已发布的基线）。
      （D1 已把 `fold()` 接到段入口，摘要现在**真的**进快照了，这条测量随之可以做——
      归 F5，与 `benchmarks/recovery_time_benchmark.py` 一起跑。）

## C. 动态能力视图（`context/capability.py`）

- [x] **C1 `CAPABILITY_FILTERS` 注册表**：键为 `ContextConfig.capability_filter`，
      `static`（全量，默认，行为不变）之外至少 1 个真筛选器（任务相关性：关键词/最近使用/
      工具族前缀，任选实现但须确定性排序）。
      （`static` + `relevance`。相关性 = 任务实词对 `name + description` 的命中计数，
      分词只做"拉丁词跑 + CJK 单字"，不引第三方分词器（L3）。
      两处判据是踩出来的，写死在测试里：`_MIN_TERM_LENGTH=3`（"on/an" 做子串会命中
      content、analytics，筛选退化成噪声）、虚词表（the/for/and… 让每个英文描述都满分）。
      `list_runs` 会因名字里含 "run" 被召回——**同族工具一起召回是有意的**。）
- [x] **C2 经 `tool_selector` 位点接入**，不改 loop、不在 loop 内联判据（L1）。
      （`Agent.__init__` 里按配置装配选择器：`capability_filter="static"` 或调用方自带
      `tool_selector` 时**根本不建筛选器**（`_capability=None`），默认路径零开销；
      显式选择器优先，且此时 `agent.capability_report` 为 None——不报一份"其实没生效"的结果。
      `test_c2_*` 两条：配置真的到达 Provider（发出去的工具列表从 10 缩到 2）、显式传入的赢。）
- [x] **C3 召回护栏（本节的硬指标）**：筛选**永不**丢掉本次运行里模型实际要调的工具。
      做法：已调用过的工具名进常驻集合（resident set），断言筛选结果 ⊇ resident set；
      违反即 reject 该策略，不允许"平均召回率"糊过去。
      （常驻集合取自 `assistant.tool_calls` 与 `tool` 消息的 name 两处。
      `test_c3_guard_holds_for_every_state_and_every_cap` 跑 3 任务 × 3 常驻组合的笛卡儿积；
      `test_c3_resident_beats_the_visible_cap` 断言 **常驻优先于 `max_visible_tools`**——
      上限与护栏冲突时让位的是上限，因为裁掉一个正在用的工具比多花 token 贵得多。）
- [x] **C4 兜底**：筛选结果为空时回退全量并计数（空工具列表 = 直接废掉 Agent）。
      （`fallback=True` 进 `CapabilityReport`；`test_c4_*` 覆盖"词面全不命中"与"空注册表"两种空。）
- [x] **C5 排序稳定**：输出顺序由名称字典序决定，不依赖 dict 插入序或集合哈希
      （否则 prompt 变化会污染 benchmark 复现）。
      （`test_c5_output_order_is_lexicographic_not_insertion_or_hash`：同一份规格正反两种插入序
      喂进去，输出逐条相等且等于名称字典序。`static` 例外——它保持原序，因为"默认行为不变"
      包含顺序，见 `test_c1_static_filter_returns_everything_in_original_order`。）
- [x] **C6 计量闭环**：被裁掉的工具 token 数记进 `state.usage`（如 `tools_tokens_dropped`），
      让"省了多少"可被断言，而不是只由脚本作者口头声明。
      （已落：`CapabilityReport.dropped_tokens` 用 A 节的 `count_context` 对**实际被裁掉的那批规格**
      算出，测试里独立重算一遍对比（`test_c6_dropped_tokens_are_measured_not_declared`），
      并断言 `0 < dropped_tokens < 全量`（`test_c6_saving_never_exceeds_the_full_tool_list`）。
      **"记进 `state.usage`" 改成记进 `ContextReport`**（D1 收口）：`tool_selector(specs, state)`
      只能返回规格，写快照要改 loop（L1 禁止），改 `state.usage` 更是会污染已经 yield 出去的快照。
      所以落点是引擎的报告：`ContextReport.tools_kept` / `tools_dropped_tokens`，每步一条
      （`test_d4_report_makes_every_level_readable` 断言这些字段在真实一步里的数值）。）

## D. 预算治理（`context/budget.py` + `context/engine.py`）

- [x] **D1 `ContextEngine` 抽象**（`context/engine.py`）：组合
      `压缩 → 能力视图 → 预算判定`，对 loop 暴露 `assemble()` 与 `select()` 适配器，
      对外层暴露 `preflight(state, specs) -> BudgetDecision`。**同一个引擎实例**同时服务两侧，
      杜绝"外层判的量与真发的量不一致"。
      （已落：`ContextEngine` + `EngineAssembler` + `Plan(keep_recent, max_visible_tools)`。
      `Agent.__init__` 只在任一 P3 开关打开时建引擎，建了就把**两侧**都交给它：
      `assembler=EngineAssembler(self._context)`、`tool_selector=self._context.select`。
      `test_d1_the_same_engine_serves_the_loop_and_the_outer_layer` 直接比对象身份
      （`agent._assembler._engine is engine` 且 `agent._tool_selector.__self__ is engine`），
      并断言外层估的量 == 真发出去的那份（`engine.estimate(sent) == FIRST_VIEW ==
      report.estimated_prompt_tokens`）。
      顺带删掉了 P2 遗留的**第二条能力视图通路**（`as_tool_selector` + `Agent._capability`）：
      引擎接管后它就是唯一事实源，覆盖一条没人走的路径等于留一份会腐烂的假象；
      覆盖率报告把这条指出来（`agent.py:151` 死代码）才删的，不是顺手重构。
      调用方自带 `tool_selector` 时引擎仍在、但配置副本强制 `capability_filter="static"`，
      且 `agent.capability_report` 返回 None——选择器被接管还报筛选结果就是在说谎。）
- [x] **D2 `max_context_tokens` 语义钉死**：比较对象 = 本次将要发出的
      `messages(组装后) + tool specs` 的估算总量（A1+A2），不是历史全长。
      边界单测：恰好等于上限（不触发动作）、上限 -1（触发）。
      （`estimate(view, specs)` = `count_context(counter, 组装后的视图, 实际选中的规格)`——
      三段都是真发出去的，没有"历史全长"这种宽估。
      `test_d2_estimate_is_the_payload_about_to_be_sent_not_the_history_length` 用一个
      "历史很长但视图很小"的状态证明判的是视图不是历史；
      `test_d2_hard_limit_boundary_is_inclusive` 参数化 `(SHARED_VIEW, True)` 与
      `(SHARED_VIEW - 1, False)`——**等于上限不发**动作，越界一格才触发；
      `test_d2_select_side_records_the_same_verdict` 断言 loop 位点那侧得到同一个结论。）
- [x] **D3 `compress`**：按 B3 顺序逐级压缩直到达标；压到位则继续，压不动了走 D5 升级。
      （阶梯在 `_ladder()`：`compress` 先按 `keep_recent` 收缩历史、再动工具上限，
      `degrade` 反之——**顺序就是策略**，两条序列在测试里逐档写出：
      `[(8,8),(4,8),(2,8),(1,8),(1,4),(1,2),(1,1)]`（`test_d3_ladder_is_bounded_deterministic_and_ordered`，
      含 `KEEP_FLOOR=1` / `TOOLS_FLOOR=1` / `LADDER_STEP=2` 的边界）。
      达标路径 `test_d3_compress_shrinks_history_before_touching_the_tool_view`：
      上限 95 时落在 `Plan(4,8)`，视图从 7 条缩到 5 条、`after=90 ≤ 95`、工具一个没裁。
      升级在**下一次模型调用**才可见（`advance()` 一次做完副作用，没有步中刹车，见 ADR-01），
      `test_d3_escalated_plan_applies_to_the_next_step` 断言下一帧真的变短、
      `FACT-0` 已被折掉而窗口内的 `FACT-2` 原样保留、`levels == ("truncation",)`——
      截断档不产生摘要标记，这是 B1 的取舍，写进断言而不是注释。
      压不动路径 `test_d3_compress_exhausts_when_the_floor_is_still_over_the_limit`：
      上限 49 < 阶梯地板 50 时 `levels_tried == 7 == len(_ladder())` 且 `exhausted`，
      计划**不**被改成半档（`engine.plan` 仍是原计划）。）
- [x] **D4 `degrade`**：先裁工具规格（C 的能力视图加严），再压历史；每级动作可观测
      （`ContextReport` 记录 `action`、`levels_tried`、`before/after` 估算）。
      （`test_d4_degrade_tightens_the_view_before_shrinking_history`：同一份数据、同一个上限，
      `degrade` 落在 `Plan(8,1)`（历史一条不动、视图裁到 1 个规格），
      与 D3 的 `Plan(4,8)` 形成对照——两个动作的差别必须能在计划上读出来。
      `test_d4_report_makes_every_level_readable` 逐字段断言
      `(action, levels_tried, exhausted) == ("degrade", 4, False)`、
      `(before, after, estimate, dropped) == (270, 110, 95, 6)`、`tools 10 → 2`、`orphans == 0`。
      运行中触发由 `test_d4_mid_run_degrade_reaches_the_next_provider_call` 用三步脚本量出
      `levels_tried == [0, 3, 6]`（第 1 步达标、第 2 步降到 3 档、第 3 步降到 6 档），
      并断言第 3 次调用发给 Provider 的工具列表确实变短。）
- [x] **D5 `reject`**：**在 Agent 外层**产生唯一 `error` 事件 + `AgentStatus.FAILED`
      （L2 的具体落点：外层 preflight 命中即不调 `drive()`），错误详情可判读、
      且**不进重试**（属 `non_retryable`：再跑一遍预算判定必然同样超限——这条要写进 ADR-03 并被
      单测锁住：reject 后 `provider_calls` 不增加）。
      （入场：`_pipeline` 开头 `fold()` → `preflight()`，命中即 yield 一条
      `EventType.ERROR(reason="budget_exceeded")` + `FAILED` 并 `return`，不进 `drive()`、不进重试环。
      `test_d5_reject_terminates_at_entry_without_touching_the_provider` 断言事件流恰为
      `[ERROR]` 且 `provider.calls == []`（重试过一次就会多一次调用，这条断言同时锁住"不进重试"）；
      `test_d5_reject_does_not_silently_degrade_quality_at_entry` 锁住"拒 ≠ 偷偷降质量"：
      reject 的入场判定不动计划，`engine.plan` 保持默认；
      `test_d5_reject_persists_a_failed_snapshot` 用 `auto_save_every=1` 的内存 store 验 `FAILED`
      快照确实落盘（可审计，不是只发个事件）。
      detail 文案含 `on_budget_exceeded=reject` 与 `estimate > limit` 两个数，可判读。
      **运行中**超限是另一条路：不能紧急制动（`advance()` 已把整步副作用做完），
      只能按 F3 在步边界暂停 —— `test_d5_mid_run_exhaustion_pauses_at_the_step_boundary`
      断言 `status == PAUSED`、`error` 含 "budget"、`provider.calls == 2`（不多烧一次）。
      `test_d5_resuming_into_a_still_tight_budget_fails_instead_of_re_pausing` 防止"暂停→恢复→又暂停"
      的死循环：恢复后入场判定同样超限就直接 ERROR 终止。
      `test_d5_late_exhaustion_does_not_leave_the_loop_paused` 是收尾发现的坑：暂停标志若不复位，
      下一个任务一进来就停在第 0 步——现在 `_pipeline` 的 `finally` 里清。）
- [x] **D6 可观测性**：默认**不发**每步上下文事件（沿用 P2"静默落盘 + 只读属性"的先例），
      `agent.context_reports` 提供历史；若要把 `EventType.CONTEXT` 变成 opt-in，
      须证明 F4 未被破坏，并在 L6 里登记为新增枚举成员。
      （**没加** `EventType.CONTEXT`：加枚举成员是契约动作，而"只读属性"这条通道已经够用，
      P2 的静默落盘先例照搬。`test_d6_default_config_builds_no_engine_and_stays_silent` 断言
      `"context" not in {event.value for event in EventType}`、默认配置下 `agent._context is None`、
      `context_reports == []`、事件流仍为 `[THINKING, DONE]` 且发给 Provider 的是**全量**规格。
      `test_d6_reports_are_one_per_step_and_the_property_is_a_copy`：三步跑完 `len(reports) == 3`、
      `steps == [0, 1, 2]`（记的是"发出时已完成几步"）、宽松上限下 `action == "none"` 且
      `within_budget`，属性返回的是副本，外部 append 改不动引擎的事实源。）
- [x] **D7 与 `ControlConfig.max_steps` / `max_total_tokens` 的关系写清**：
      预算超限与步数耗尽是两条独立终止路径，各自有测试，不得互相掩盖（F2 的先例）。
      （`test_d7_max_steps_still_terminates_when_the_budget_is_ample`：引擎开着、上限给足，
      步数耗尽仍是 `error(reason="max_steps_exceeded")` + `FAILED`，预算不插队。
      `test_d7_budget_and_max_steps_are_two_separate_terminations` 把两条路径放进同参数化里比对：
      `reasons == ["max_steps_exceeded", "budget_exceeded"]`，且宽松那路真调了模型 1 次
      （`loose_provider.calls == 1`）、预算那路一次没调（`tight_provider.calls == []`）——
      两条路径的**可观测后果**不同，不是同一个 error 换个文案。
      `max_total_tokens` 仍**不在本阶段实现**：它是账单口径的累计上限（属后续"成本控制"阶段），
      P3 的 `max_context_tokens` 是单次请求口径，两者不能混为一个开关。此条写进 ADR-03。）
- [x] **D8 单测三态齐全**：compress / degrade / reject 各 ≥2 条（达标路径 + 压不动路径），
      Roadmap 明确要求"预算超限时行为确定"。
      （`test_d8_every_mode_has_a_determined_answer_at_the_same_limit` 参数化同一上限 95 下三态结论
      （`within_budget` 分别 True/True/False，且 `decision.action` 始终记下配置的动作，
      即使阶梯把它救回来也照记——D4 可观测的另一半）；
      `test_d8_every_mode_bottoms_out_deterministically` 参数化"压到底也不行"：
      `reject` 在入场直接拒（`levels_tried == 0`、不动 `exhausted_reject` 标志），
      `compress`/`degrade` 走满 7 档后 `exhausted is True`；
      `test_d8_reachability_of_the_escalated_plan_differs_by_action_not_by_data` 断言同一份数据下
      `compress → Plan(4,8)` 与 `degrade → Plan(8,1)` 落在**不同**计划上：
      结论由动作决定，不由数据偶然决定。D 节共 42 条，全在
      `tests/unit/test_context_engine.py`。）

## E. Evidence 增强与追溯（Roadmap 交付物 `tools/evidence.py`）

- [x] **E1 位置决策成文**：`Evidence` 现在 `tools/registry.py`。要么按 P1 `ToolCall` 先例
      移到 `tools/evidence.py` 并在 `registry.py` 保留重导出（导入路径不破），
      要么在 ADR-03 记录"不迁"的理由。**不许默默略过 Roadmap 的交付物清单。**
- [x] **E2 增强字段全带默认值**（L6）：可选 `step` / `origin` / 父子调用链字段，
      用于压缩时判断"哪条证据可折叠"。既有不变式 `ok == (error is not None)` 不得被破坏
      （扩一条 property 测试）。
- [x] **E3 压缩以 Evidence 文本为主要目标**：工具结果通常最占 token 且最可摘要；
      B3 的顺序必须体现这一点，并有"摘要后 user/assistant 原文未被改写"的断言。
- [x] **E4 截断安全性**：任何压缩/截断都不得产生"孤儿 `tool_call`"（同 B1 判据），
      把这条列为 `ContextReport` 的自检项，每次组装都验。
      （`count_orphan_tool_calls(view)` 在 `ContextEngine.select()` 里**每次组装都算**，
      进 `ContextReport.orphans`（`context/engine.py`），不是测试里临时算的量。
      `test_e4_no_ladder_level_produces_an_orphan_tool_call` 对 3 种动作 × 整条阶梯（7 档）
      逐档扫，并要求"确实折叠过"（最严档比原语料短）——否则"零孤儿"会因为什么都没切而假通过。
      `test_e4_orphan_count_is_a_per_report_self_check_not_a_one_off` 反手删掉最后一条 `tool`
      结果，断言同一处自检报出 `orphans == 1`：这个字段是判据，不是常数 0。
      B1 的边界回退（`test_b1_*` 两条、39 组参数）与本条互补：B1 管"不主动切散配对"，
      E4 管"任何来源的残缺配对都能被当场发现"。）

## F. Benchmark 与对外证据（Roadmap 的量化指标）

- [x] **F1 `benchmarks/context_compression_benchmark.py`**：固定长任务（离线脚本 Provider），
      输出每策略 `估算 token 节省率` + `质量保留率`（B5 的事实命中）+ 真实 `usage` 校准误差（A5）。
      结论句式必须是"hybrid 比 truncation 省 X%，事实保留率从 A 到 B"这种成对表述。
- [x] **F2 工具爆炸场景**：注册 ≥200 个工具规格，测能力视图的注入 token 下降幅度（C6）
      与召回护栏（C3）同时成立；这是 Roadmap 点名的独家场景，只对标自家基线。
- [x] **F3 变量隔离**：P2 的教训——`--steps` 扫描会同时改变"条数"与"单条大小"两个量。
      F1/F2 每项测量只动一个变量，其余钉死；清单里明确写出每档控制了哪些量。
- [x] **F4 数字可复现**：benchmark 打印所用参数与命令；README/blog 引用的数字标注
      "本机/日期/命令"，并注明波动范围（P2 已出现同配置跨轮次 ±30% 的情况）。
- [x] **F5 不劣化 P2 基线**：摘要进快照后重跑 `benchmarks/recovery_time_benchmark.py`，
      `payload_bytes` 与恢复耗时变化量化入档（B8）。

## G. 零回归与工程门禁

- [x] **G1** L4 默认配置等价测试绿色；P1/P2 既有测试**一条都不许改断言来迁就 P3**
      （若必须改，须在 ADR-03 说明为什么那是契约变更而非实现细节）。
- [x] **G2 CI 心脏模块扩列**：`.github/workflows/ci.yml` 的 `FOCUS` 增加 `context/` 各模块，
      维持 ≥90% 门槛（心脏代码从严的既有取向）；扩列后本地先跑一遍确认达标。
- [x] **G3 门禁**：`ruff check stableagent tests examples benchmarks` /
      `ruff format --check ...` / `mypy` / `pytest` 全绿，全库覆盖率 ≥70%（现 98.2% 不得下滑）。
- [x] **G4 CHANGELOG**：P3 契约扩展逐条标注非破坏性；`00-contracts.md` 增补"P3 扩展记录"，
      并更新"组装位点"索引（把 `compression` / `capability` / `budget` / `engine` 的文件路径钉进去）。
- [x] **G5 ADR-03** `docs/design-decisions/03-context-engineering.md`：估算 vs 真实 token、
      压缩产物入快照的理由与代价、reject 走外层而非抛异常、能力视图的召回护栏设计、
      与 checkpoint/恢复的交互；含**被否决方案**（至少：在 loop 内联压缩、组装器抛
      `BudgetExceeded`、引入 tiktoken、摘要只放内存、用 LLM 当质量评审）。
- [x] **G6 CI 全绿**：3.10/3.12 矩阵 + 心脏闸门（最后一条，需要将当前变更提交并推送后以 GitHub Actions 验证）。
      （本地等价预检已在 Python 3.10 / 3.11 / 3.12 通过；远端 workflow 只会运行远端已提交内容，
      未经维护者授权不得为验证而提交或推送工作区改动。）
      **已关闭（2026-11-02）**：经维护者授权后提交并推送 `main`，远端 run 41384598912 针对 commit `707dff2`
      两个 job 全部 success。逐 job 数值见本文末尾“远端 CI 收口记录”。

## H. 配套二并行轨道（Roadmap：P3 期间启动）

- [x] **H1** `docs/blog/02-context-engineering.md` 初稿：《Context Engineering：把 token 效率
      做成可度量的能力》，素材取 F1/F2 实测与 ADR-03。
- [x] **H2** README 更新：Roadmap 表 P3 状态、能力段从"未来"改为"已交付 + 数字"，
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
（`ContextConfig` 三个带默认值字段、`AgentState.context_summary: str = ""`
——后者已在 D1 收口时删除，摘要只活在那条标记消息里，见 L6 与 D 节记录。）

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

### 2026-11-02 · C 节动态能力视图（C1 / C2 / C3 / C4 / C5 / C6 勾选）

新增 `stableagent/context/capability.py`（`CapabilityFilter` / `StaticFilter` /
`RelevanceFilter` / `CapabilityReport` / `CAPABILITY_FILTERS` / `as_tool_selector`）、
`tests/unit/test_capability.py`（23 条）；`core/agent.py` 按配置装配选择器并加只读
`capability_report`；`ContextConfig` 加 `max_visible_tools: int = 8`。

```
$ git diff 46441f0 -- stableagent/core/loop.py                 → 0 行（L1 复核）
$ .venv/bin/ruff check stableagent tests examples benchmarks   → All checks passed!
$ .venv/bin/mypy                                               → Success: no issues found in 25 source files
$ .venv/bin/pytest -q                                          → 220 passed, 1 skipped / 98.61%
$ .venv/bin/pytest --cov=stableagent/context                   → capability.py 81 stmts 0 miss 100%
$ /tmp/sa-3.10/bin/python -m pytest -q                         → 220 passed, 1 skipped
$ /tmp/sa-3.12/bin/python -m pytest -q                         → 220 passed, 1 skipped
```

三处判断：

1. **相关性判据的词面要做减法，不是加法**。第一版把 user/assistant 文本里所有
   `[a-z0-9_]+` 与 CJK 单字都当任务词，结果 10 个工具里选出 6 个——因为 "a"、"on"、"the"
   这类词做**子串**命中：`on` 命中 `content`，`an` 命中 `analytics`。
   改成"拉丁词取 3 字母起 + 虚词表"，同一任务收敛到 2 个工具。
   这类退化不会报错，只会让"筛选"悄悄变成"没筛"，所以用 `test_c1_two_letter_words_are_not_task_terms`
   把判据本身钉住，而不是只钉结果。
2. **护栏优先于上限**。`max_visible_tools` 与常驻集合冲突时按常驻走（可以超上限）：
   上限是省 token 的旋钮，护栏是正确性的地板，两者不是一个量级。
3. **`static` 不排序**。默认路径连工具顺序都不动（P2 的事件流一致性包含发给 Provider 的
   tools 列表形状），要动态视图就得显式改名——但配置项 `compression` 那种"单一开关"
   的坑在这里不存在，因为 `capability_filter` 本身就是开关。

### 2026-11-02 · D 节预算治理（D1~D8 + A3/A4 + L2/L4/L5 + E4 勾选，进度 31/52）

新增 `stableagent/context/budget.py`（`BudgetAction` / `ContextReport` / `BudgetDecision` /
`count_orphan_tool_calls`）、`stableagent/context/engine.py`（`ContextEngine` / `Plan` /
`EngineAssembler`）、`tests/l4_default_stream_dump.py` + `tests/fixtures/l4_p2_default_stream.json`
（L4 跨版本金标准）、`tests/unit/test_context_engine.py`（42 条）、
`tests/unit/test_l4_default_equivalence.py`（2 条）；
改 `core/agent.py`（两侧接同一引擎 + 段入口 `fold`/`preflight` + `observe` + 暂停标志复位）、
`core/state.py`（**删掉** B 节临时加的 `AgentState.context_summary`）、
`context/compression.py`（`SUMMARY_NAME` 语义改为"进快照的唯一载体"）。

```
$ git diff 46441f0 -- stableagent/core/loop.py                 → 0 行（L1 复核，D 节未动 loop）
$ .venv/bin/ruff check stableagent tests examples benchmarks   → All checks passed!
$ .venv/bin/ruff format --check ...                            → 60 files already formatted
$ .venv/bin/mypy                                               → Success: no issues found in 27 source files
$ .venv/bin/pytest -q                                          → 265 passed, 1 skipped / 98.84%
$ .venv/bin/pytest --cov=stableagent/context                   → engine.py 146 / budget.py 40 /
                                                                  compression.py 111 / capability.py 81 /
                                                                  assembly.py 22 / tokens.py 51 全 100%
$ core/agent.py                                                → 240 stmts 9 miss 96%（9 行全是 P2 遗留分支：
                                                                  310 / 389 / 400 / 402 / 410 / 421 / 427 / 432 / 435）
$ /tmp/sa-3.10/bin/python -m pytest -q --no-cov                → 265 passed, 1 skipped
$ /tmp/sa-3.12/bin/python -m pytest -q --no-cov                → 265 passed, 1 skipped
L4 金标准：7252 字节，P2 收口提交工作树 == 当前工作树，diff 为空
```

五处判断（都有对应的坑，不是事后合理化）：

1. **摘要进快照靠 `fold()`，不新增 `AgentState` 字段**。B 节先加了 `context_summary: str = ""`
   占位，D1 接线时发现它必然与 `messages` 里那条标记消息漂移——两处真相、还要写迁移函数，
   而标记消息本身就是"进快照"的载体。删字段后旧快照兼容反而更简单（一个字段都不用补），
   L6/B2/B7 三处文案同步改写。落点在**每段入口**而非 loop 位点：位点看不到也写不回快照，
   段入口折一次才是"一次模型调用换 N 步复用"。
2. **`reject` 是不对称的**。入场命中 = 直接拒、不 escalate、不动计划、不烧模型；
   运行中命中 = 只能到步边界暂停（ADR-01：`advance()` 一次做完整步副作用，没有步中紧急制动）。
   把两条混成一条会写出"入场悄悄降质量"的实现——`test_d5_reject_does_not_silently_degrade_quality_at_entry`
   专门盯这个。另一条连环坑：恢复进同样紧的预算必须 `FAILED` 而不是再 `PAUSED`，
   否则"暂停↔恢复"永动（`test_d5_resuming_into_a_still_tight_budget_fails_instead_of_re_pausing`）。
3. **暂停标志要在 `finally` 里复位**。预算暂停走的是引擎自己置 `loop.pause_requested`，
   正常 F3 暂停路径不会替它清；不复位则**下一个任务**一进来就停在第 0 步。这类跨任务的状态泄漏
   是 P2 "静默落盘"没遇到过的形状，测试 `test_d5_late_exhaustion_does_not_leave_the_loop_paused` 盯住。
4. **L4 用跨版本金标准，不用"P3 里断言没建引擎"**。原计划只写结构断言，但"没建引擎"证明不了
   "发的东西一样"。改成把 P2 收口提交的实际输出（事件流 + 每次 provider 请求 + 终态快照 +
   `CONTRACT_VERSION` + `EventType` 全集）存成 fixture 比字节，金标准本身由
   `tests/l4_default_stream_dump.py` 在两版代码上跑同一段脚本产出，重生成步骤写在模块 docstring 里。
   顺带把"不许抬 `CONTRACT_VERSION`"也纳入这条测试（真要抬版就是一次显式契约决策，见 L6）。
5. **删掉了 Agent 的第二条能力视图通路**。C 节的 `as_tool_selector` + `Agent._capability` 在引擎
   接管后没人走；是覆盖率报告把 `agent.py:151` 指成死代码才删的（连带删导入）。
   留着等于多养一份"看起来还在生效"的假象，比少 2% 覆盖率贵得多。

一处必须记下的坑：**中文语料在英文工具描述上一个词都不命中**，会走 C4 的全量兜底。
D 节第一版测试拿 `test_compression.corpus()` 的中文任务跑，`SHARED_VIEW` 预期 110、实得 270，
19 条同时红。修法不是改判据，是 `loaded()` 把首条 user 换成英文 `TASK` 并在 docstring 里
写明这个陷阱——预算测试要的是"视图真的被裁过"，不是"相关性筛选在中文上表现如何"。

仍未勾选的都有归属：**L6/L7** 等 ADR-03（G5）与 CI（G6）；**A5/B8/F1~F5** 是 benchmark 轨道；
**E1~E3** 是 Evidence 增强；**G/H** 是收口与对外。


## 本地收口记录（2026-11-02）

本次补齐了 E1–E3、F1–F5、G2/G4/G5 与 H1/H2，并重新验证既有 P3 实现。验收账本当前为 **51/52**：仅 G6 的远端 GitHub Actions 仍待当前变更被维护者提交并推送后验证。

> 后续说明（同日）：本段是推送前快照，保留原文不重写。G6 已由其后的“远端 CI 收口记录”关闭，账本现为 52/52。

### 本地验证证据

```bash
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
/tmp/sa-3.10/bin/python -m pytest -q --no-cov
/tmp/sa-3.12/bin/python -m pytest -q --no-cov
```

结果：ruff/format 通过；mypy 在 28 个源文件上通过；Python 3.11 全量测试为 **283 passed, 1 skipped**、全库覆盖率 **98.99%**；Python 3.10 与 3.12 均为 **283 passed, 1 skipped**。扩展后的核心覆盖率闸门通过：`context/{assembly,tokens,compression,capability,budget,engine}.py` 和 `tools/evidence.py` 均为 100%，既有 core 心脏模块为 96.8–100%。

### P3 新增证据

- Evidence canonical 类型位于 `stableagent/tools/evidence.py`；`registry.py` 和顶层包保留同一类对象重导出。P3 决定**不**在 Evidence 添加默认 trace 字段，因为即使值为 `None` 也会改变 P2 默认 `tool_result` 事件的序列化金标准；P4 将在 Ticket/Assignment 记录中承载调用链追溯。
- [P3 Context Engineering 基准记录](../evidence/p3-context-benchmark-2026-11-02.md) 固定了离线参数、质量/节省成对指标、200 工具 resident 护栏和 summary checkpoint 影响。
- [ADR-03](../design-decisions/03-context-engineering.md) 已记录 ContextEngine、预算、摘要快照、Evidence 与被否决方案；[P3 对外文章](../blog/02-context-engineering.md) 只引用该离线证据。

### G6 远端验证步骤

维护者确认本工作区中所有改动后，提交并推送包含本次 P3 变更的分支；GitHub Actions 必须在 Python 3.10 / 3.12 均通过 ruff、format、mypy、pytest、全库 ≥70% 覆盖率和扩展后的核心 ≥90% 覆盖率门禁。记录 workflow run URL/ID 后，才可将 G6 勾选并宣布 P3 **52/52 正式收口**。

## 远端 CI 收口记录（2026-11-02）

P3 **52/52 正式收口**。证据链：本地门禁 fresh 复跑 → 分 4 个提交 → 推送 `main` → 远端矩阵全绿。

### 推送前的本地复核（全部为本轮实跑，不是沿用上一轮结论）

```bash
.venv/bin/ruff check stableagent tests examples benchmarks   # All checks passed!
.venv/bin/ruff format --check stableagent tests examples benchmarks   # 65 files already formatted
.venv/bin/mypy                                                # Success: no issues found in 28 source files
.venv/bin/pytest -q                                           # 283 passed, 1 skipped / 98.99%
/tmp/sa-3.10/bin/python -m pytest -q --no-cov                 # 283 passed, 1 skipped (3.10.20)
/tmp/sa-3.12/bin/python -m pytest -q --no-cov                 # 283 passed, 1 skipped (3.12.13)
git diff 46441f0 -- stableagent/core/loop.py | wc -l           # 0（L1 复核：P3 全程未动 loop）
.venv/bin/python benchmarks/context_compression_benchmark.py --tools 200
```

benchmark 复跑与 [基准记录](../evidence/p3-context-benchmark-2026-11-02.md) 逐项一致：hybrid 629 估算
token / 节省 51.16% / 事实保留 100%，truncation 节省 64.29% 但只留 25% 事实，summary MAE 124；
200 工具场景裁掉 8,570 token（节省 98.59%）后 `resident_audit` 仍在保留集合内、`fallback=false`；
checkpoint payload 4,056 → 2,594 字节。恢复耗时本轮为 0.250 / 0.427 ms，与记录的 0.237 / 0.272 ms
同为本机 SQLite 噪声量级——按该记录的约束，**不据此声称稳定的延迟改进**。

### 提交划分

推送把本地领先的 10 个提交一起带上 `origin/main`，其中本轮新增 4 个：`e079c14`（Evidence 迁至
`tools/evidence.py`，registry 重导出）、`31b7b55`（离线 benchmark 与其形状断言）、
`09a88a1`（CI FOCUS 扩到 12 个文件）、`707dff2`（文档重组与 P3 记录）。

### 远端结果

- Run：<https://github.com/VinderDiesel/StableAgent/actions/runs/41384598912>（ID 41384598912，head SHA `707dff24195d63ffe410ad58fda73a2741b9745c`）
- `quality (3.12)` success，30s；`quality (3.10)` success，33s
- 两个 job 逐条相同：ruff `All checks passed!`、format `65 files already formatted`、
  mypy `Success: no issues found in 28 source files`、pytest `283 passed, 1 skipped`、
  总覆盖率 **98.99%**（门槛 70%）
- 心脏闸门（≥90% 每文件）：`context/{assembly,budget,capability,compression,engine,tokens}.py`
  与 `tools/evidence.py`、`core/checkpoint/manager.py` 均 **100.0%**；
  `core/checkpoint/storage.py` 99.3%、`recovery.py` 99.0%、`core/errors.py` 98.3%、
  `core/loop.py` **96.8%**
- 唯一标注是 Actions runner 层面的弃用提示（checkout@v4 / setup-python@v5 的 Node 20、
  ubuntu-latest 迁移到 Ubuntu 26），与 P3 判定无关

### 收口后的状态

L1~L7、A~H 全部勾选，52/52。P4 的进入条件（`specs/README.md` 规则 4：仅当阶段全部退出条件具备
可复现证据才可启动下一阶段）自此满足；P4 启动时把 [P3 Spec](../specs/p3-worker-context-runtime.md)
第 7 节“阶段交接”作为输入，并沿用本记录的估算误差范围与 benchmark 基线位置。

