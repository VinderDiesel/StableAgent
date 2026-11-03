# ADR-04：Ticket 编排内核与 Durable Outbox（P4）

- 状态：Accepted（2026-11-03 实现并收口，远端 CI run 41596456428 双绿，证据见账本）
- 日期：2026-11-02（决策）；2026-11-03（实测证据回填）
- 关联：[公共契约](00-contracts.md)、[ADR-01 Loop](01-loop-runtime.md)、[ADR-02 Checkpoint](02-checkpoint.md)、[ADR-03 Context](03-context-engineering.md)、[P4 实施规格](../specs/p4-ticket-orchestration-kernel.md)、[P4 验收账本](../acceptance/p4-ticket-orchestration.md)
- 代码入口：`stableagent/orchestration/{model,store,outbox,executor,scheduler,service,errors}.py`、`stableagent/core/agent.py`

## 背景

P0–P3 把"一次 Worker run"做成了可度量、可恢复的单元：`AgentLoop` 是纯函数式的步转移，
`AgentState` 是唯一快照，`ContextEngine` 治理历史与工具预算。但一次 run 仍然是短命的：
它没有身份、没有队列、没有依赖、没有外部副作用的可靠投递，也无法在编排进程崩溃后继续。

P4 要回答的是"多个长期任务如何被组织"：创建 `Ticket`、派生 `Assignment`、有限并发调度、
按依赖推进、把 Worker 的 `Event`/`AgentState` 映射成长期 `Report`，并且保证"启动 Worker、
投递输入、取消运行"这些副作用既不丢也不重。P4 明确不接 GitHub/飞书/MCP/Docker（P5/P6 范围）。

关键难点不在模型类，而在三处接缝：编排层与 Core 的驱动接缝、SQLite 事务与副作用的提交接缝、
以及崩溃重启后"谁有权继续驱动同一个 run"的所有权接缝。本 ADR 记录这三处接缝的取舍。

## 约束

1. `core/loop.py` 相对 P2 收口基线 `46441f0` 保持零 diff；Scheduler/Ticket/Outbox/权限逻辑一律不得进 loop。
2. Worker 的 `Event` 协议、F4"恰一个终止事件居尾"、`AgentState` 单快照源不变。
3. 运行时依赖仍为 3 个（`pydantic` / `anyio` / `httpx`）；编排层使用 stdlib `sqlite3`，不引入 `aiosqlite`/ORM。
4. P0–P3 既有测试（含 L4 默认流金标准 `tests/fixtures/l4_p2_default_stream.json`）必须继续全绿。
5. 全部验收离线确定性：无网络、无真实模型、无墙钟、无随机（ID 与时钟注入）。
6. `Ticket` 状态不得依赖自然语言文本判定；`Report` 是领域接口，Worker `Event` 只作诊断载荷。

## 决策

### 1. 包边界：`stableagent/orchestration/`，编排层自带版本，顶层不重导出

新增 `stableagent/orchestration/`，含 `model / store / outbox / executor / scheduler / service / errors`
七个模块与一个 `__init__.py`。依赖方向单向：`model` ← `store` ← (`outbox`, `scheduler`, `service`)，
`executor` 只依赖 `model` 与 Core 公开入口。`planning/` 保留为 P6+ LLM Planner 占位，其 docstring
改指 `orchestration/`，避免"Phase 4 交付 planner"的旧说明与实际包名混淆。

公开契约口径：`CONTRACT_VERSION` 保持 `1.1.0`（Core 的公共 payload 未被 P4 改变），
编排层另立 `ORCHESTRATION_SCHEMA_VERSION = "0.1.0"` 定义在 `stableagent/orchestration/__init__.py`，
并在 SQLite 里落 `schema_migrations` 表。P6 Beta 之前不承诺编排层记录结构稳定，因此顶层
`stableagent/__init__.py` 不重导出 Ticket/Assignment/Report（P7 再决定是否升为公共面）。
这样 P5/P6 兼容扩展编排字段时不需要动 Core 的冻结契约，也不需要把 Core 版本号拖进编排层的迭代。

### 2. 驱动模型：单一 asyncio 事件循环 + 信号量

`Scheduler` 在一个事件循环里运行。它每轮从 Store 推导可运行 Ticket，在事务内入队
`launch_assignment` effect，提交后由 `EffectDispatcher` 领取 due effect，用
`asyncio.Semaphore(max_concurrency)` 取槽并 `asyncio.create_task(executor.launch(...))` 驱动 Worker。
`OrchestrationService` 的领域命令保持同步：只写记录与入队 effect，绝不直接调用 executor。

这个选择同时解决了三件事，代价是接受"同步 SQLite 调用会短暂阻塞事件循环"——在 P4 的
离线规模下不成立为问题，并且写进下面的原子性不变式后它反而成了工具。

### 3. 事务原子性：事务体内禁止 await，同步段天然不可分割

`Store.transaction()` 是上下文管理器，进入时置 `_in_tx`，重入抛 `ReentrantTransactionError`；
在事务体内 `await` 被禁止（异步 handler 只能在提交之后由 dispatcher 调用）。由于驱动循环是单线程，
任何同步的 Store 方法对其它协程而言不可分割，因此"Ticket 状态变更与 effect 入队在同一事务"
是可证明的，而不是靠约定。

由此得出两条硬规则，测试各自把守：**提交前不得启动 Worker**（启动只由被领取的 effect 触发）；
**effect handler 的 I/O 不持有事务**（dispatcher 调用 handler 前断言 `not store.in_transaction`）。

### 4. 恢复所有权：内存句柄不是事实源

`Scheduler._tasks: dict[assignment_id, asyncio.Task]` 只作取消句柄；一切可运行性从 Store 推导
（规格 WP-4.5.1）。`assignment_id` 已在 `_tasks` 中时拒绝第二次驱动并抛 `DuplicateLaunchError`，
因此 Scheduler 重启不会并发恢复同一 Assignment（同一 `worker_run_id` 另有数据库唯一键兜底）。

`recover()` 把崩溃时为 `running` 的 Assignment 标 `interrupted`，以含 `attempt` 的幂等键重新入队
`launch_assignment`，executor 走 `Agent.stream_recover(worker_run_id)`——不重放 Event、不复制 state，
沿用 ADR-02 的快照恢复语义。已 `ended` 的 Assignment 不得被再次启动，测试以 Provider 调用次数断言。

### 4.1 补充（规格修订）：`worker_run_id` 在第一个 step 边界注册，而不是启动前

规格 WP-4.4.5 要求"每次启动前注册 Assignment 的 `worker_run_id`"，但 Core 在 `Agent._drive()` 内部
才生成 run_id（`stableagent/core/agent.py:325`），公开事件流里它只出现在 `CHECKPOINT` 事件的
`data["run_id"]`，`agent.last_state.run_id` 同样只在 step 边界之后可读（`core/agent.py:240`）。
P4 不改 Core 来迁就这条表述，改为精确化注册时机：

1. `AgentExecutor` 驱动 `agent.stream(task)`，在**第一个 step 边界事件**处把 run_id 单条 UPDATE 写入
   `Assignment.worker_run_id`；
2. 未启用 checkpoint 的 Worker 配置（`Config.checkpoint.backend == "memory"` 或
   `auto_save_every < 1`）一律拒绝启动并抛 `WorkerConfigError`——没有快照就没有可恢复的 Assignment，
   编排层不该假装能恢复；
3. 崩溃发生在第一个 step 边界之前时 run_id 仍为 `NULL`，`recover()` 以同一 Assignment、同一 `attempt`
   重新入队 `launch_assignment` 并覆盖注册。其重复风险就是 ADR-02 后果 1 已声明的"一步的崩溃窗口
   意味着工具调用必须可重入"，不新增不变式。

对应规格文本的同步修订见 [P4 规格第 6 节第 7 条](../specs/p4-ticket-orchestration-kernel.md)。

### 5. Event→Report 映射与默认噪音

映射固定：`DONE→ready`、`ERROR→failed`、`PAUSED(needs_human)→waiting_for_human`、
`PAUSED(budget_exceeded)→blocked`；`progress` 默认只在 `TOOL_RESULT` 落一条，
放开到 `THINKING`/`TOOL_CALL` 由配置决定。这是 ADR-03 "默认不增加事件噪音"结论在编排层的延续：
Report 一旦默认高频，长期存储与 `list_tickets` 的信噪比就垮了。

Report 判定只读结构化字段（`Event.type`、`AgentStatus`、`PauseReason`、`ErrorRecord`），
不从消息文本猜状态；`TicketInput`/`Report.details` 承载原因。

### 6. 唯一被批准的 Core 扩展：`aresume(extra_input=...)`

规格要求"后续输入只能在 Worker 空闲/暂停边界投递，不抢占正在执行的 `advance()`"，而 Core 现状是
`Agent.stream/arun(task)` 只在开始时接一次任务、`aresume()` 不接受新输入。为编排层打开的第二条路
（直接改 checkpoint 快照）会摧毁 ADR-02 的单快照源审计性。

因此 P4 引入一个带默认值的兼容扩展：`Agent.resume(...)` / `Agent.aresume(...)` 接受
`extra_input: str | None = None`，在进入 `_continue_pairs` 之前把 `Message(role="user", content=extra_input)`
追加到 state 的副本上。`core/loop.py` 一行不动；`extra_input=None` 时行为与现在逐字节一致，
由 L4 金标准重新 dump 比对把关（重新生成流程见 `tests/l4_default_stream_dump.py`）。
`deliver_input` effect handler 是它唯一的调用方。

该扩展排在 M2，使 M1 的走骨架阶段保持 Core 零改动，把"Core 是否被动过"这个风险隔离在单一里程碑里。

### 7. 状态机集中与优先级

状态只能由 `model.py` 的三个纯转换函数（`next_ticket_status` / `next_assignment_status` /
`next_effect_status`）或 `service` 命令推进，Repository 不提供任意字段更新。非法转换、未知 ID、
环状依赖、已结束 Ticket 写入、跨 Ticket 报告一律抛带稳定 `code` 的 `OrchestrationError` 子类。

调度优先级直接实现为可测的排序键 `(被唤醒过的优先, queued_at, created_at)`，
对应规格 WP-4.5.6 的"人工唤醒 > 已排队 > 新建"，不写成待办。

## 被否决方案

| 方案 | 拒绝原因 |
|---|---|
| 线程池 + `Agent.run()` 同步 API 驱动 Worker | `Agent.run()` 内部是 `asyncio.run`，不能在被运行的循环里调用；线程方案要为每个 Assignment 起独立循环，取消与背压退化成标志位轮询，SQLite 单写者与线程亲和性另需约束，测试时序不确定。 |
| 子进程 per Assignment | 隔离最强，但需要 IPC 与进程生命周期管理；规格把执行环境/sandbox 推到 P5，P4 引入会撑大范围。P4 的子进程只用于崩溃演练（整个编排进程被 SIGKILL）。 |
| 编排层把 user 消息直接写进 P2 `CheckpointStore` 最新快照再 `stream_recover` | 绕过"AgentState 是快照唯一来源"与版本化迁移契约，恢复语义不可审计，违反规格第 1 节边界的精神。 |
| 为投递新输入新增 `EventType` 或给 `AgentState` 加输入字段 | 改变公共事件/快照 payload，直接撞 L4 金标准；一个默认 `None` 的可选参数兼容面最小。 |
| P4 不支持暂停中注入，输入只在下个 Assignment 边界生效 | 会让 `deliver_input` effect 基本空转，规格目标 4/5 名存实亡；真要降级就得先改规格并记 ADR，代价高于一个可选参数。 |
| `Ticket`/`Assignment`/`Report` 现在纳入 `CONTRACT_VERSION` 并 bump 1.2.0 | 顶层重导出后 P5/P6 的字段扩展都成了破坏性变更风险；规格第 2 节要求先保持内部 API 明确。 |
| 内存队列承载待调度 Assignment | 崩溃即丢；规格 WP-4.5.1 要求可运行性只从 Store 推导。 |
| 用内存标志实现 effect 幂等 | 重启后失效，无法满足"任意崩溃不重复已确认副作用"的退出条件；幂等必须是 SQLite 唯一键。 |
| 在 `Store.transaction()` 内 `await` executor | 事务持有时长的下限变成"一次模型往返"，重启窗口内既没提交也没法判定 effect 是否该重放，直接丢副作用。 |
| 让 Scheduler 判断是否值得重试真实模型错误 | 重试策略属于 Core 的 provider 层与 P5 的授权层；P4 只对 effect 做注入式 backoff。 |

## 后果与代价

- 事件循环单线程 + 同步 sqlite：`tick()` 期间存在短暂阻塞，编排规模变大后需要换成 WAL/连接池或独立写线程，
  届时本 ADR 需 superseding 记录。P4 以 `benchmarks/` 脚本在固定规模下复跑单轮 `tick()` 与 `recover()` 耗时，
  结果记入 `docs/evidence/p4-orchestration-benchmark-<yyyy-mm-dd>.md`（账本 H7），只验证该代价的量级，
  不外推为性能承诺。
- `ORCHESTRATION_SCHEMA_VERSION` 独立意味着使用者要显式选择编排层；顶层导入面在 P6 之前不承诺。
- 走骨架优先（M1 只有 `launch_assignment`）会让 M2 引入 `deliver_input`/`cancel` 时重构 dispatcher 的
  handler 注册与保序逻辑一次；这是为了让集成风险在第一个里程碑暴露而接受的代价。
- `Agent` 恢复入口多一个可选参数，Core 的"零改动"退出条件在 P4 表述为"`loop.py` 零 diff + 一处向后兼容扩展"，
  必须在账本里以 L4 dump 结果证明默认路径未变。
- 优先级三维排序键一旦测试锁死，改动会牵动既有调度测试，视同契约。

## 迁移与兼容

- 编排层使用独立 SQLite 文件或同文件的独立表集与 `schema_migrations`，**不复用** P2 checkpoint 表；
  旧库无需迁移（P4 是新表）。
- `Mandate.policy_version = "p4-local-v1"`，字段集最小；P5 只能以带默认值的兼容扩展加入授权字段并升版本号。
- P5 复用 P4 的 `WorkerExecutor` 协议：协议签名变化必须走新 ADR，P5 只提供新实现。
- 旧快照与旧事件流不受影响：P4 不写 `AgentState`，只读 Worker 产出并映射为 Report。

## 测试与证据（实测，2026-11-03）

全仓 `424 passed, 1 skipped`，行覆盖率 98.46%；下述用例全部离线确定性（注入 ID/clock、脚本化
Provider、无网络/墙钟/随机），账本条目以 `A1`–`L8` 引用。

- `tests/unit/test_orchestration_model.py`（25）：A1–A4、A6。关键用例
  `test_ticket_round_trips_and_is_deterministic`、`test_legal_ticket_transitions`（9 条参数化）、
  `test_assignment_attempt_starts_at_one_and_rejects_zero`、
  `test_ready_is_not_completed_and_completed_is_terminal`、`test_m2_effect_kinds_close_the_whitelist`
  （effect kind 白名单三值收口）。
- `tests/unit/test_orchestration_store.py`（19）：B1–B7、L6。关键用例
  `test_transaction_is_atomic_for_both_ticket_and_effect`、
  `test_nested_transaction_is_rejected_and_flag_clears`、
  `test_reopen_loses_nothing_and_unique_keys_hold`、
  `test_concurrent_reader_only_sees_committed_state`、
  `test_runnable_order_is_woken_then_queued_then_created`。
- `tests/unit/test_outbox.py`（9）：C3–C7、L6（handler 不持事务）。关键用例
  `test_enqueue_is_idempotent_on_kind_and_key`、`test_handler_never_sees_an_open_transaction`、
  `test_handler_exception_retries_then_fails_without_memory_flags`、
  `test_orphan_running_effects_are_reclaimed`、`test_deferrable_error_backs_off_without_burning_budget`。
- `tests/unit/test_agent_executor.py`（13）：D1–D7、F2 的 Worker 侧。关键用例
  `test_checkpoint_required_worker_is_refused_before_any_io`、
  `test_run_id_is_registered_once_at_the_first_step_boundary`、
  `test_recover_uses_stream_recover_and_never_replays_events`、
  `test_done_emits_ready_report_with_verbatim_diagnostic_payload`、
  `test_deliver_resumes_parked_agent_and_consumes_input`。
- `tests/unit/test_scheduler.py`（32）：E1–E9、L7。关键用例
  `test_two_slots_cap_concurrent_workers`、`test_delivery_waits_for_park_boundary_and_keeps_sequence`、
  `test_woken_ticket_precedes_older_queued_ones`、`test_recover_leaves_ended_tickets_alone`、
  `test_deadline_timeout_cancels_assignment_and_blocks_ticket`、
  `test_queued_downstream_waits_for_upstream_before_launching`、
  `test_launch_effect_for_blocked_ticket_is_voided`。
- `tests/unit/test_orchestration_service.py`（22）：A5、C2、E4–E6、G4。关键用例
  `test_all_ten_commands_exist`、`test_stable_error_matrix`、`test_view_is_derived_only_from_records`、
  `test_retry_ticket_adds_an_attempt_and_keeps_history`、
  `test_add_dependency_rejects_direct_and_indirect_cycles`。
- `tests/unit/test_orchestration_boundaries.py`（13）：约束 1/2/6、L1、L3–L5、L8、B7。关键用例
  `test_core_layers_never_import_orchestration`（core/context/providers/tools 四层 AST 扫描）、
  `test_loop_py_is_byte_identical_to_p4_base`、`test_agent_state_dump_has_no_ticket_fields`、
  `test_orchestration_version_is_independent_and_not_reexported`、
  `test_every_error_carries_a_stable_code`（G5 的清单来源）。
- `tests/unit/test_l4_default_equivalence.py`（2）：约束 4、决策 6 的默认路径。
  `test_p3_default_config_stream_identical_to_p2` 与 `test_l4_golden_covers_the_three_p3_switches`；
  另以 `PYTHONPATH=. .venv/bin/python -m tests.l4_default_stream_dump |
  diff - tests/fixtures/l4_p2_default_stream.json` 复核，无输出即逐字节相等（L2）。
- `tests/integration/test_ticket_recovery.py`（1）：E8、C5。子进程双 Ticket 跑到第 2 个 Report 后
  SIGKILL；新进程 `recover()` 后 `interrupted_assignments=1`，先 ended 的 Assignment 不重跑，
  恢复进程 Provider 调用数恰为 `[3]`（G2）。
- 示例：`examples/04_ticket_orchestration.py --phase run`（父/子 Ticket + 依赖 + 两个脚本 Worker）
  与 `tests/integration/test_ticket_example.py`（2）覆盖 run 面；`--phase selftest` 在子进程被
  SIGKILL 后输出 `SELFTEST ok=True killed=True completed=2`（G1、M3.1、M3.2）。
- 基准：`benchmarks/orchestration_benchmark.py`，50 Ticket / 100 Assignment、3 轮中位数：
  `tick` 34.9 ms、`recover` 38.3 ms、`drain` 122.3 ms；原始 JSON 与复现步骤见
  [证据 2026-11-03](../evidence/p4-orchestration-benchmark-2026-11-03.md)。只用于验证本 ADR
  "后果与代价"第一条的量级，不外推为性能承诺（H7）。
- CI：`.github/workflows/ci.yml` 的心脏覆盖率闸门为 18 个文件（新增
  `orchestration/{model,store,outbox,executor,scheduler,service}.py`），每个 ≥90%；
  `tests/unit/test_ci_focus_gate.py`（1）锁死路径清单与步骤名（M3.3、H5）。
- 解释器矩阵：`uv venv --python 3.10|3.11|3.12` 三套环境各自全绿、覆盖率 98.46%、18/18
  闸门文件 ≥90%（M3.4、H4）；基准用例为矩阵之后新增，当前全仓（3.12）425 项。

## 前置 ADR 遗留项的处置（2026-11-02 补充）

[ADR-02 的"代价与遗留"](02-checkpoint.md) 有两条把责任交给 P4。原文保留，这里只做日期明确的处置：

1. **后果 1（"幂等性检查留给 P4"）**：P4 关闭其中"重复启动 / 重复投递"这一层——由
   `Effect.kind + idempotency_key` 的 SQLite 唯一键、`(ticket_id, attempt)` 唯一键和
   `DuplicateLaunchError` 三处实现（账本 B4、B5、C3、L7）。**工具自身副作用的可重入审计不在 P4**：
   P4 的 Worker 只跑离线脚本 Provider，没有外部写操作，该审计随 P5 的真实仓库写操作一并设计。
2. **后果 4（"Provider 调用整体的硬超时留待 P4 的预算治理，因为二者共享同一个 deadline 模型"）**：
   拆成两层归属。
   - **外层 Assignment deadline 归 P4**（M2 新增账本项 E9）：`deadline_at` 超时由 Scheduler 用
     `ensure_future` + `asyncio.wait(timeout=budget)` 包住 executor 任务（3.10 兼容，不用
     `asyncio.timeout`），超时后由 `service.expire_assignment()` 直接落 Assignment `cancelled`、
     Ticket `blocked` 并写 `reason=deadline_exceeded` 的 BLOCKED Report；不再入队
     `cancel_assignment`（已无在途 task 可取消）。它只依赖本 ADR 决策 2 的单循环模型，不动 Core。
   - **内层 Provider 单次调用硬超时不属于 P4**：它要在 `stableagent/core/` 的调用点包裹
     `asyncio.timeout`，与决策 6 锁定的"P4 只允许一处 Core 兼容扩展"直接冲突，因此改归
     P7 [WP-7.2 多 Provider 与成本路由](../specs/p7-production-hardening-ecosystem.md)。
     deadline 模型不再"共享一个入口"：P4 管任务级，P7 管单次调用级，两者以
     BLOCKED Report 的结构化原因与 `ProviderConfig.request_timeout_seconds` 分别可观测。

本处置不改写 ADR-02 的结论；在 ADR-02 原条目处只追加了指向本文的日期说明。

## 阶段交接

P5 开始前必须保存：P4 的 SQLite recovery 集成基线、Worker `Event`→Report 映射契约、
`WorkerExecutor` 协议签名、`ORCHESTRATION_SCHEMA_VERSION` 与 `schema_migrations` 现状。
P5 只扩展 Mandate 授权字段、`WorkerExecutor` 实现（worktree/sandbox）、新 effect handler 与输入来源元数据，
不得替换 P4 的 ID、状态机或 outbox 语义。
