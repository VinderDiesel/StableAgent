# CHANGELOG

本文件遵循语义化版本；公共契约的版本与冻结规则见 `docs/design-decisions/00-contracts.md`。

## [Unreleased]

### 契约扩展（P3 本地收口，远端 CI 待确认；全部非破坏性，`CONTRACT_VERSION` 仍为 1.1.0）
- `ContextConfig` 新增 5 个带默认值字段：`keep_recent_messages=8` / `max_summary_chars=1200` /
  `tool_result_excerpt_chars=200` / `max_visible_tools=8`；`on_budget_exceeded` 的
  `compress` / `degrade` / `reject` 三个取值从"声明未实现"变为真实语义（见下方引擎条目）。
  既有 `Config()` 的 `compression="none"` + `capability_filter="static"` +
  `max_context_tokens=None` 组合下这些字段不参与任何代码路径
- **`AgentState` 未新增字段**：本阶段一度加过 `context_summary: str = ""`，D 节收口时删除。
  摘要的唯一载体是 `messages` 里那条 `role="system"` + `name="context_summary"` 标记消息，
  再加一个字段就是会与它漂移的第二份事实源；好处是旧快照兼容面更小（无字段可补）
- 未新增 `EventType` 成员：上下文决策走只读属性（`agent.context_reports`），
  沿用 P2"静默落盘 + 只读属性"的先例，也避免动终止事件序列（F4）
- `ContextConfig` 的四个既有占位项（`assembly` / `compression` / `capability_filter` /
  `max_context_tokens`）现在都有消费方；`ControlConfig.max_total_tokens` 仍为声明未实现
  （账单口径累计上限，属后续成本控制阶段，与 P3 的单次请求口径不同）
- 新模块 `stableagent/context/{tokens,compression,capability}.py`：三张注册表
  （`ASSEMBLERS` / `COMPRESSIONS` / `CAPABILITY_FILTERS`）+ `resolve_*` 工厂，
  与 P1 的组装位点同构；未知名一律 `KeyError` 带出已注册清单
- `resolve_assembler(config, summariser=None)`：新增第二个可选参数（位置参数量不变，
  旧的单参调用形状完全兼容）。`compression != "none"` 时返回 `CompressedAssembler` 包装，
  `== "none"` 时返回的对象与类型都与 P2 一致
- 新模块 `stableagent/context/budget.py`：`ContextReport`（每步一条：`step` / `action` /
  `limit` / `before` / `after` / `levels` / `levels_tried` / `tools_total` / `tools_kept` /
  `tools_dropped_tokens` / `orphans` / `within_budget` / `exhausted` /
  `estimated_prompt_tokens` / `observed_prompt_tokens`）+ `BudgetDecision`（入场判定）+
  `count_orphan_tool_calls`（"没拆散 `tool_call`/`tool_result` 配对"的自检，E4）
- 新模块 `stableagent/context/engine.py`：`ContextEngine`（压缩 → 能力视图 → 预算判定的组合，
  `plan` / `estimate` / `fold` / `assemble` / `select` / `preflight` / `observe`）+
  `Plan(keep_recent, max_visible_tools)`（frozen，可当适配器缓存的键）+
  `EngineAssembler`（把引擎的组装能力包成 loop 认识的适配器，L1 因此零改 loop）
- `Agent.__init__` 新增两个可选关键字注入项 `token_counter=` / `summariser=`（缺省用内置启发式
  计数与离线抽取式摘要；位置参数量与旧调用形状不变）
- `Agent` 新增只读属性 `context_reports`（返回副本，外部改不动引擎的事实源）；
  `capability_report` 改由引擎提供，**未启用动态视图时为 `None`**；构造时若显式传入
  `tool_selector`，引擎的配置副本强制 `capability_filter="static"` 且 `capability_report`
  为 `None`——显式选择器优先，不报一份"其实没生效"的结果
- **移除** `Agent` 内部的第二条能力视图通路（C 节留下的 `as_tool_selector` 装配路径与
  `Agent._capability`）：D 节把动态视图收进引擎后它已无调用方，覆盖率报告指为死代码。
  `stableagent.context.capability.as_tool_selector` 仍是公开 API，未删除
- 运行时依赖仍为 3 个（`pydantic` / `anyio` / `httpx`）：token 估算为内置启发式，
  未引入 `tiktoken`

### Added（P3 D 节：预算治理）
- `ContextEngine` 一个实例同时服务 loop 位点与 Agent 外层：外层 `preflight()` 判的量与
  位点实际发出的量必然一致（`test_d1_*` 直接比对象身份），杜绝"外层以为没超、发出去爆了"
- 预算判据 `max_context_tokens` 钉死为"将要发出的组装后消息 + 实际注入的工具规格"
  （不是历史全长），边界语义为"等于上限不触发动作"
- 阶梯（`_ladder()`，`KEEP_FLOOR=1` / `TOOLS_FLOOR=1` / `LADDER_STEP=2`）：
  `compress` 先按 B3 顺序收缩历史再动工具视图，`degrade` 先加严能力视图再压历史，
  `reject` 在入场直接拒且**不 escalate、不动计划**；`degrade` 之后才升级，
  阶梯顺序与长度都有测试逐档写出
- **摘要进快照**（L5）：`ContextEngine.fold(state)` 在每段入口（`_pipeline` 开头）折叠一次并
  写回 `messages`，崩溃恢复不需要重算——`test_l5_recovered_stream_with_compression_is_event_identical`
  除逐条比对事件流外还断言恢复后首次请求的上下文与不中断那次完全相同
- 运行中压到地板仍超限 → 走 F3 的步边界暂停（`reason="budget_exceeded"`），语义与 P2 的
  `needs_human` 同形；恢复进同样紧的预算则直接 `FAILED`，不再重复暂停（防"暂停↔恢复"循环）；
  `_pipeline` 的 `finally` 复位 `pause_requested`，避免跨任务泄漏
- A4 事后校准：`ContextEngine.observe(state)` 把 Provider 回报的 `prompt_tokens` **增量**
  与估算成对记进报告（按 `step` 去重、段入口重置基线）；**只报告不回灌**，
  校准不参与任何决策（Provider 谎报 usage 时决策序列逐条不变）
- L4 跨版本等价证据：`tests/l4_default_stream_dump.py` +
  `tests/fixtures/l4_p2_default_stream.json`（P2 收口提交 `46441f0` 工作树产出的金标准：
  事件流 / 每次发给 Provider 的 messages+tools / 终态快照 / `CONTRACT_VERSION` /
  `EventType` 全集），`test_p3_default_config_stream_identical_to_p2` 断言当前代码逐字节复现
- 测试 +45（合计 265 passed, 1 skipped）：`test_context_engine.py` 42 条（含 E4 的阶梯孤儿扫描
  4 条）+ L4 跨版本等价 2 条 + B7 拆分 1 条；`stableagent/context/` 六个模块全部 100%，
  全库 98.84%；`core/loop.py` 相对 P2 收口提交零修改

### Added（P3 E/F：Evidence 与可复现证据）
- `stableagent/tools/evidence.py`：`Evidence` 的 canonical 定义；`tools/registry.py` 和顶层包
  保留同一个类对象的重导出，旧导入路径不变。P3 保持 Evidence 既有序列化字段，不新增默认 trace 字段，
  以维持 P2 默认 `tool_result` 事件金标准；`step` / `origin` / `parent_call_id` 延后至 P4 的
  Ticket/Assignment 领域记录。模型强制 `ok == (error is None)`；失败缺少错误说明时补齐
  确定性错误文本，避免 tool message 退化成字符串 `"None"`
- `benchmarks/context_compression_benchmark.py`：固定离线语料输出压缩 token/事实保留/tool pair、
  独立 reference Provider usage 校准、200+ 工具 resident 护栏和第 3 步 SQLite checkpoint 恢复影响
- `docs/evidence/p3-context-benchmark-2026-11-02.md`：记录命令、环境、固定变量与本机结果；
  `hybrid` 在该语料中节省 51.16% 估算 token 且保留全部事实，200 工具动态视图节省 98.59% 工具 token，
  summary 恢复段的最新 snapshot payload 从 4,056 降至 2,594 字节。reference usage 是离线模拟口径，
  不作为真实模型 tokenizer 精度声明
- `tests/unit/test_evidence.py` 与 `tests/unit/test_context_benchmark.py`：覆盖 Evidence 兼容/不变式/追溯字段，
  以及 benchmark 的质量、校准、resident 护栏和 checkpoint 输出形状

### P2 收口
- DoD **34/34**：最后一项 F5（CI 3.10/3.12 矩阵全绿）由 run 41032998523 关闭，
  两个 job 均为 `126 passed, 1 skipped` / 覆盖率 98.20% / 心脏模块 96.8–100%，
  与本地预跑数值逐条一致；证据记在 `docs/acceptance/p2-checkpoint-recovery.md` 的 F5 条目与复核记录
- 崩溃恢复用例的击杀时序竞态修复（详见 `02-checkpoint.md` 后果 5）：只在编排侧改步数，
  运行时代码零改动

### 许可与打包
- 采用 **Apache License 2.0**：新增 `LICENSE`（apache.org 原文，未修改），README 增补 License 段
  与 badge；此前仓库无许可证文件
- `pyproject.toml` 按 PEP 639 声明 `license = "Apache-2.0"` + `license-files = ["LICENSE"]`，
  并补齐 Python 3.10/3.11/3.12 等分类符；已验证 wheel 内含
  `stableagent-0.1.0.dist-info/licenses/LICENSE` 且 `License-Expression: Apache-2.0`
- 未加 `py.typed`（`Typing :: Typed` 分类符同样未声明）：留待 P6 mypy strict 收口时一并处理

### 契约扩展 v1.1.0（P2，全部非破坏性，`CONTRACT_VERSION` 不抬版本）
- `EventType.RETRY` 新增成员（兼容扩展）：失败自愈的重试通知；消费方按字符串解析
- `CheckpointConfig` 新增 7 个带默认值字段（旧 `Config()` 构造行为完全不变）：
  - `sqlite_path` / `redis_key_prefix` / `history_limit`：后端参数
  - `retry_max_attempts` / `retry_base_delay_seconds` / `retry_backoff_factor` /
    `retry_max_delay_seconds`：指数退避预算
- `Agent.__init__` 新增关键字参数 `redis_client`（注入项，缺省 `None`）；
  `checkpoint_store` / `fallback` / `sleep` 亦为 P2 新增的关键字注入项
- `OpenAIProvider.from_config(ProviderConfig, ...)`：新增构造入口，把
  `request_timeout_seconds` / `api_base` / `model` 贯通到 httpx 客户端（验收 C5）
- **未动**：`Event` / `Message` / `AgentState` / `Provider` / `Tool` / `Evidence`
  的既有字段与不变式；`core/loop.py` 零修改（见 `docs/acceptance/p2-checkpoint-recovery.md` F1 的 diff 证明）

### Added（P2）
- `core/checkpoint/storage.py`：`CheckpointStore` 统一异步协议（`save/load/records/delete`）
  + `MemoryCheckpointStore` / `SqliteCheckpointStore` / `RedisCheckpointStore`
  （Redis 客户端为注入项，本包不硬依赖 redis 库）+ `build_store` 按配置选后端
- `core/checkpoint/manager.py`：`CheckpointManager`——步边界触发判据、同 run 写锁、
  `(run_id, step)` 幂等覆盖写、历史裁剪；存储故障降级为计数不打断运行（验收 B5）
- `core/checkpoint/recovery.py`：`MIGRATIONS` 版本迁移链 + `SnapshotVersionError`
  （高于/未知版本显式报错）+ `RetryPolicy` 指数退避（`sleep` 可注入）+
  `RecoveryDirector` 重试/转人工/认失败决策 + `repair_for_retry` / `pause_for_human`
- `core/errors.py`：`ErrorClass` 三分类与 `classify_error` / `classify_error_detail`
  （判据优先级：状态码 > 类型名 > 消息特征 > 默认不可重试）
- `Agent` 可靠性外层：`checkpoint`/`retry` 事件注入（终止态静默落盘以保 F4）、
  `recover/arecover/stream_recover`、`checkpoint_history`、`last_state`/`last_checkpoint`
- `examples/03_checkpoint_resume.py`：kill → 重启 → 续跑 的离线确定性演示（含 `--phase selftest`）
- `docs/design-decisions/02-checkpoint.md`：ADR-02（一致性/并发/迁移/后端取舍/重试边界）
- 测试 +77（合计 126 passed, 1 skipped）：`test_crash_recovery.py`（subprocess + SIGKILL
  的进程级崩溃恢复）、三种后端共享一致性断言、版本迁移、退避序列、失败自愈各分类路径、
  D4 恢复流与不中断流逐条等价；全库覆盖率 98%，`core/checkpoint/` 99–100%

### Fixed（P2）
- `build_store`：`backend="redis"` 时 `CheckpointConfig.redis_key_prefix` 此前是死配置
  （只有直接构造 `RedisCheckpointStore` 才生效）；改经 `redis_client` 注入路径统一读取
- `examples/03_checkpoint_resume.py` + `test_crash_recovery.py`：3 步设计的击杀点与终止事件
  在同一次注入里到达，外部 `SIGKILL` 与子进程自然退出竞速，Python 3.10 预跑 CI 矩阵时偶发
  "kill 没杀死"。演示改为 4 步（击杀点之后保留一整轮 provider 往返），断言按 `TOTAL_STEPS`
  推导；可击杀窗口从十几毫秒放大到 `delay` 的线性量级（ADR-02 后果 5）。**框架代码零改动**，
  这是编排侧的时序设计问题，不是恢复语义问题

### 契约修订 v1.0.0 -> v1.1.0（P1，全部非破坏性）
- `EventType` 新增成员 `paused`（兼容性扩展，消费方按字符串解析）
- `Message` 新增字段 `tool_calls: list[ToolCall] = []`
- `AgentState` 新增字段 `usage: dict[str, int] = {}`
- `ControlConfig` 新增字段 `max_steps: int = 10`
- `ToolCall` 定义移入 `core/state.py`（解除 Message 循环引用），
  `providers/base.py` 保留重导出，**旧导入路径不变**
- P1 语义决策 F1~F5（详见 `docs/acceptance/p1-core-runtime.md` 第 0 节）：
  advance 纯转移接口 / max_steps→error+FAILED / pause 步边界生效、非法态 no-op /
  终止事件恰一且居尾 / 暂停表达=新枚举成员 PAUSED

### Added（P1）
- `core/loop.py`：AgentLoop——`advance(state) -> (state, events)` 纯转移 +
  `drive()` 事件引擎（可快照、可重入，P2/P3 接入点已按 DoD 预留）
- `context/assembly.py`：上下文组装位点（默认 passthrough，注册表式扩展）
- `providers/openai.py`：sync / async / SSE 三形态 + `ToolCallAssembler`
  流式 function calling 重组
- `Agent.run/arun/stream` + `pause()/resume()` 最小实现（内存态，P2 换持久化）
- `examples/01_basic_research_agent.py`（真实 OpenAI，需 OPENAI_API_KEY）、
  `examples/02_loop_offline_demo.py`（零依赖离线演示）
- 测试 49 个（总覆盖率 99%，`core/loop.py` 100%），含 C2 断点重放等价参数化测试

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

### Fixed
- `OpenAIProvider._parse_response`：网关 `usage` 含嵌套 `*_details` 对象时 `int()` 崩溃，
  改为只累计数值字段（真实网关端到端 A1 验证时发现）

### 修订窗口约定
- P0~P1：契约允许修订，必须在本文件记录并标注破坏性/非破坏性。
- P2 起：契约冻结，只允许向后兼容扩展（新增带默认值的可选字段 / 新增枚举成员 / 新增配置节）。
