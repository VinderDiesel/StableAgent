# CHANGELOG

本文件遵循语义化版本；公共契约的版本与冻结规则见 `docs/design-decisions/00-contracts.md`。

## [Unreleased]

### P2 收口
- DoD **34/34**：最后一项 F5（CI 3.10/3.12 矩阵全绿）由 run 41032998523 关闭，
  两个 job 均为 `126 passed, 1 skipped` / 覆盖率 98.20% / 心脏模块 96.8–100%，
  与本地预跑数值逐条一致；证据记在 `docs/p2-dod-checklist.md` 的 F5 条目与复核记录
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
  的既有字段与不变式；`core/loop.py` 零修改（见 `docs/p2-dod-checklist.md` F1 的 diff 证明）

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
- P1 语义决策 F1~F5（详见 `docs/p1-dod-checklist.md` 第 0 节）：
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
