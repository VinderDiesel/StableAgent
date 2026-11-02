# P4（Ticket 编排内核）实施计划与验收账本

> 依据：`docs/Roadmap.md` Phase 4 + [P4 实施规格](../specs/p4-ticket-orchestration-kernel.md)
> + [ADR-04 编排与 Outbox](../design-decisions/04-ticket-orchestration.md)
> + [ADR-02 Checkpoint](../design-decisions/02-checkpoint.md)（`stream_recover` 语义与单快照源）
> + [ADR-03 Context](../design-decisions/03-context-engineering.md)（默认不增加事件噪音的结论）。
>
> **状态：Active（2026-11-02 计划经维护者确认，实现尚未开始；DoD 0/60，里程碑步骤 0/30，
> M1 任务级步骤 0/50）。**
>
> 使用方式：本文件既是 P4 的实施计划（第 1 节的里程碑步骤 + 第 6 节的 M1 任务级步骤），
> 也是 P4 的验收账本（第 0、2 节）。
> 每个可勾选事项都写明需求来源、可执行验证方法、对应测试或命令、证据占位；全部勾选且第 3 节命令
> 在 CI 复现后 P4 才算收口。计划推进时把实际输出追加到对应条目，不重写已记录的历史输出。
>
> **计数口径**：DoD = 第 0 节 L1~L8（8 项）+ 第 2 节 A~H（52 项）= 60 项；
> 里程碑步骤 = M1 的 10 项 + M2 的 12 项 + M3 的 8 项 = 30 项。里程碑与 DoD 的归属见第 1.4 节。
> 第 6 节的 50 个 Step 是 M1 十个里程碑步骤的执行粒度（Task↔M1.x 对照见第 6 节开头），
> 不计入 30 项；M2/M3 的同粒度计划在各自里程碑开工前追加为第 7、8 节。

---

## 0. 前置硬边界（违反任一条即不算完成）

- [ ] **L1 `core/loop.py` 零修改**（来源：spec 完成条件、§1 必须保持的边界）。
      验收方式：`git diff 46441f0..HEAD -- stableagent/core/loop.py` 输出 0 行，结果记进本条。
      证据：（待记录）。未满足归属：视为架构越界，回到 ADR-04 重议接缝，不改 loop。
- [ ] **L2 Core 只允许一处兼容扩展**（来源：ADR-04 决策 6）。
      即 `Agent.resume()/aresume()` 新增 `extra_input: str | None = None`，其余 Core 公共 payload 不变。
      验收方式：`git diff 46441f0..HEAD --stat -- stableagent/core/` 仅出现 `agent.py`；
      `tests/l4_default_stream_dump.py` 重新 dump 与 `tests/fixtures/l4_p2_default_stream.json` 逐字节相等。
      证据：（待记录）。未满足归属：任何额外 Core 改动须先写 superseding ADR。
- [ ] **L3 运行时依赖仍为 3 个**（`pydantic` / `anyio` / `httpx`）：编排层用 stdlib `sqlite3`，
      不引入 `aiosqlite`、ORM 或调度库。验收方式：`pyproject.toml` 的 `dependencies` 条目数不变 +
      `tests/unit/test_p3_contract.py` 的 L3 断言仍绿。证据：（待记录）。
- [ ] **L4 依赖方向单向**（来源：spec §1 边界、ADR-04 决策 1）。
      验收方式：新增 `tests/unit/test_orchestration_boundaries.py`，AST 扫描 `stableagent/core/**`
      与 `stableagent/context/**`，断言无任何 `orchestration` 导入。证据：（待记录）。
- [ ] **L5 `AgentState` 与 checkpoint 表不被编排层污染**（来源：spec §1）。
      `Ticket` 与 Worker `run_id` 不同值，关系只存于 `Assignment.worker_run_id`；P4 表独立且带
      `schema_migrations`。验收方式：`test_orchestration_store.py` 断言 P4 库不含 P2 表名，
      且 `AgentState.model_dump()` 无 Ticket 字段。证据：（待记录）。
- [ ] **L6 事务边界**（来源：spec §1"同一事务写入 + 提交前不得启动 Worker"、ADR-04 决策 3）。
      `Store.transaction()` 重入抛 `ReentrantTransactionError`；事务体内禁止 `await`；
      dispatcher 调 handler 前断言 `not store.in_transaction`；Worker 只能由提交后被领取的
      `launch_assignment` 启动。验收方式：`test_orchestration_store.py` + `test_outbox.py` 的
      "事务内异常回滚且 effect 不落库"、"service 不触达 executor"两条断言。证据：（待记录）。
- [ ] **L7 单个 `worker_run_id` 同时最多一个驱动者**（来源：spec §1、WP-4.4.5、ADR-04 决策 4）。
      验收方式：`test_scheduler.py` 断言重复驱动抛 `DuplicateLaunchError`；重启后同一 Assignment
      只有一个在跑的任务句柄。证据：（待记录）。
- [ ] **L8 全部验收离线确定性**（来源：spec §4、使用规则 2）。ID 与时钟经注入，无网络、无真实模型、
      无墙钟、无随机。验收方式：CI 无 secrets 全绿 + 同一测试连跑两次结果一致。证据：（待记录）。

---

## 1. 实施顺序（走骨架优先）

每步顺序执行：**先写/更新测试 → 跑到红 → 实现 → 跑到绿 → 跑该里程碑的出口命令**。
命令统一用 `.venv/bin/` 前缀，工作目录为仓库根。

### M1：端到端骨架（Core 零改动，一个 Ticket 跑完并能在 SIGKILL 后恢复）

- [ ] **M1.1** 建包骨架：`orchestration/__init__.py`（含 `ORCHESTRATION_SCHEMA_VERSION = "0.1.0"`，
      顶层 `stableagent/__init__.py` 不重导出）+ `errors.py` 的 `OrchestrationError` 家族与稳定 `code`；
      `planning/__init__.py` docstring 改指 `orchestration/`。验证：`.venv/bin/mypy`、`.venv/bin/ruff check .`。
- [ ] **M1.2** `model.py`：7 模型 + 4 枚举 + `IdFactory`/`Clock` 协议与默认实现 +
      三个纯状态转换函数（先写 `test_orchestration_model.py` 的转换矩阵与序列化 round-trip）。
- [ ] **M1.3** `store.py`：8 张表 + `schema_migrations` + `transaction()`（重入与 `in_transaction` 守卫）+
      唯一键 `(ticket_id, attempt)` / `worker_run_id` / `kind + idempotency_key`。
      先写 `test_orchestration_store.py`：重开无损、回滚、唯一键冲突、排序分页。
- [ ] **M1.4** `outbox.py`（仅 `launch_assignment`）：事务内入队、提交后领取、成功才标 `succeeded`、
      幂等键重复入队只更新 payload。先写 `test_outbox.py` 的"commit 后 crash""重复 dispatch""内存标志不算幂等"。
- [ ] **M1.5** `executor.py`：`WorkerExecutor` 协议 + `AgentExecutor.launch()`（只调 `stream`）+
      `DONE→ready` / `ERROR→failed` / `PAUSED→waiting|blocked` 映射，progress 默认只在 `TOOL_RESULT` 落一条。
      先写 `test_agent_executor.py`（`FakeProvider` + 注入 Agent 工厂）。
- [ ] **M1.6** `scheduler.py` 单槽版：`await tick()` / `await drain()`，只从 Store 推导可运行 Ticket，
      `_tasks` 只作取消句柄，`DuplicateLaunch` 防护。先写 `test_scheduler.py` 的骨架用例。
- [ ] **M1.7** `service.py`：`create_ticket` / `queue_ticket` / `complete_ticket` / `get_ticket` /
      `recover`（M1 只处理 `running→interrupted` 后重启一个 Assignment）。
- [ ] **M1.8** `tests/unit/test_orchestration_boundaries.py`（L4 依赖方向扫描 + L1 loop 零 diff 断言）。
- [ ] **M1.9** `tests/integration/test_ticket_recovery.py` 第一版：子进程建库、跑到 2 个 Assignment 后
      SIGKILL，新进程 `recover()` 断言终态正确且已完成 Assignment 不重跑（Provider 调用次数硬断言）。
      沿用 P2 `test_crash_recovery.py` 的"只在编排侧改步数"做法，避免击杀时序竞态。
- [ ] **M1 出口**：`.venv/bin/pytest -q`（P0–M1 全绿）+ `.venv/bin/mypy` +
      `.venv/bin/ruff check stableagent tests examples benchmarks` +
      `.venv/bin/ruff format --check stableagent tests examples benchmarks` +
      `git diff 46441f0..HEAD -- stableagent/core/loop.py`（应为空）。

### M2：宽度（输入投递、取消、DAG、真并发、优先级、Core 扩展）

- [ ] **M2.1** 输入模型落地：`submit_input` + `TicketInput.sequence/consumed_at` + `deliver_input` effect
      （同一 Assignment 按 `sequence` 保序，不同 Ticket 可并发）。先写保序与并发用例。
- [ ] **M2.2** `AgentExecutor.deliver()` 与恢复路径：`await agent.stream_recover(run_id)` 用于崩溃恢复；
      空闲/暂停边界投递。先写"运行中不得抢占 `advance()`"的用例（断言投递只发生在 PAUSED 之后）。
- [ ] **M2.3** Core 扩展：`Agent.resume()/aresume(extra_input=None)` 追加 `Message(role="user")` 到 state 副本，
      默认 `None` 行为不变。测试先行：`tests/unit/test_agent_recovery.py` 增用例 +
      重新 dump L4 比对 fixture（逐字节相等）。
- [ ] **M2.4** `retry_ticket`：新 Assignment（`attempt+1`）、不覆盖历史、幂等键含 attempt。
- [ ] **M2.5** `cancel_ticket` + `cancel_assignment` effect：只取消未结束 Ticket，不删历史；
      槽位释放语义写进测试。
- [ ] **M2.6** effect 失败路径：注入式 backoff、`max_attempts` 耗尽 → `failed` 并派生 `blocked`/`failed` Report。
- [ ] **M2.7** DAG：`add_dependency` + 直接/间接环检测（`CyclicDependencyError`）+ `block` / `continue`
      两策略（`continue` 时把失败依赖作为 Input/metadata 注入）。
- [ ] **M2.8** 并发与优先级：`max_concurrency` 槽位从 effect 领取起占到 Assignment 终态/waiting/blocked；
      排序键 `(唤醒过的优先, queued_at, created_at)` 并锁死顺序测试。
- [ ] **M2.9** 全 5 种 `Report.kind` 与 `list_tickets` 过滤/排序/分页；`RecoverySummary` 字段定稿。
- [ ] **M2.10** `recover()` 全语义：running→interrupted、pending/retry_wait effect 续跑、
      已 `ended` 不重跑、幂等规则新旧 effect 共用。
- [ ] **M2.11** Assignment deadline（E9）：`deadline_at` → `asyncio.timeout` 包住 executor 任务 →
      超时写 `cancel_assignment` effect 并派生 `blocked` Report。先写注入 clock 的超时用例，
      确认 Core 调用点未被改动（`git diff --stat -- stableagent/core/` 仍只出现 `agent.py`）。
- [ ] **M2 出口**：M1 出口命令全集 + `pytest -q tests/unit/test_outbox.py tests/unit/test_agent_executor.py tests/unit/test_scheduler.py`
      + L4 dump 比对结果记进 L2 条目。

### M3：对外可见与收口

- [ ] **M3.1** `examples/04_ticket_orchestration.py`：父/子 Ticket + 依赖 + 两个脚本 Worker，
      打印 Ticket/Assignment/Report 时间线；可离线运行（无 key、无网络），命令写进 docstring。
- [ ] **M3.2** 示例即测试：`tests/integration/` 或 example 的 selftest 分支覆盖"崩溃→恢复"一次，
      防止示例腐烂（P2 的 `examples/03` 先例）。
- [ ] **M3.3** CI FOCUS 从 12 个文件扩到 18 个：新增 `stableagent/orchestration/{model,store,outbox,executor,scheduler,service}.py`，
      每文件 ≥90%；步骤名同步更新。
- [ ] **M3.4** 本地矩阵 Python 3.10 / 3.11 / 3.12 等价验证（全量 pytest + 覆盖率 + FOCUS 门槛）。
- [ ] **M3.5** ADR-04 的"测试与证据"小节把计划条目替换为实际测试名与结果；账本逐项回填证据。
- [ ] **M3.6** 状态同步：`docs/specs/p4-*.md` 退出清单勾选、`docs/Roadmap.md` P4 段、
      `docs/README.md` 阶段索引、`docs/acceptance/README.md` 索引、根 `README.md` 阶段表、`CHANGELOG.md`。
      编排层版本与"未进公共面"的口径要在 CHANGELOG 写明。
- [ ] **M3.7** 编排基准离线记录：单轮 `tick()` 耗时、`recover()` 到全部 effect 重放的墙钟耗时
      （注入式 clock 记录、固定 Assignment 规模），写入
      `docs/evidence/p4-orchestration-benchmark-<yyyy-mm-dd>.md` + `benchmarks/` 下的可复跑脚本。
      只报本机量级，不外推性能承诺（对应 ADR-04"同步 sqlite 阻塞循环"那条代价）。
- [ ] **M3 出口**：经维护者确认后提交并推送 `main`，GitHub Actions 3.10 / 3.12 全绿，
      把 run URL/ID 记进本账本，才允许把状态行改为"收口"。

### 1.4 里程碑与 DoD 归属

| 里程碑 | 该里程碑内可勾选的 DoD 项 | 项数 |
|---|---|---|
| M1 | L1、L3、L4、L5、L6、L7、L8；A1~A6；B1~B7；C1~C3；D1、D2、D3、D5、D7；E1；G2（第一版） | 30 |
| M2 | L2；C4~C7；D4、D6；E2~E9；F1~F4；G4、G5 | 21 |
| M3 | G1、G3；H1~H7 | 9 |

三个里程碑的出口命令都必须复核 L1（`loop.py` 零 diff）；L2/F3 只有在 M2 的 Core 扩展落地后才可勾选。
合计 60 项，与首屏计数一致。

---

## 2. DoD 明细（按工作包）

### A. 模型、ID、时间与状态机（spec WP-4.1）

- [ ] **A1** `Ticket`/`Mandate`/`Assignment`/`TicketInput`/`Report`/`Dependency`/`Effect` 字段与语义符合
      spec §2.1，序列化 round-trip 稳定。验证：`test_orchestration_model.py`。证据：（待记录）。
- [ ] **A2** ID 与时钟注入，测试不读墙钟（`IdFactory` 序列工厂 + 固定 `Clock`）。证据：（待记录）。
- [ ] **A3** 状态转换只经集中纯函数，Repository 无任意字段更新入口（无私有 setter 暴露）。
      验证：转换矩阵全绿 + 断言 `Store` 无 `update_ticket(status=...)` 类接口。证据：（待记录）。
- [ ] **A4** 模型级校验：Ticket 不得依赖自身、Assignment 必属某 Ticket、`attempt >= 1`、
      `worker_run_id` 启动前可空启动后必填。验证：`test_orchestration_model.py` 的拒绝用例。证据：（待记录）。
- [ ] **A5** `TicketView` 只由持久化记录派生，无缓存事实源。证据：（待记录）。
- [ ] **A6** Ticket 状态机七个中间态语义齐备：`open→queued→running→waiting|ready|blocked|failed|cancelled→completed`，
      且 `ready` 不自动等同 `completed`（需 `complete_ticket()`）。证据：（待记录）。

### B. SQLite Store 与迁移（spec WP-4.2）

- [ ] **B1** 独立 schema 与 `schema_migrations`，不复用 P2 checkpoint 表。证据：（待记录）。
- [ ] **B2** 八张表齐备：tickets、mandates、assignments、inputs、reports、dependencies、effects、schema_migrations。
- [ ] **B3** `Store.transaction()` 内 domain mutation 与 `enqueue_effect()` 原子；异常时两者都不落库。
      验证：注入异常的回滚用例。证据：（待记录）。
- [ ] **B4** 三处唯一键生效并给出可分类错误（含 `(ticket_id, attempt)`）。证据：（待记录）。
- [ ] **B5** `Effect.kind + idempotency_key` 的语义在 docstring 与 ADR 中一致记录。证据：（待记录）。
- [ ] **B6** 重开数据库、schema 迁移、查询排序、分页、并发读取均有离线测试。证据：（待记录）。
- [ ] **B7** Store 层不含调度或策略判断（无 import scheduler/executor）。验证：`test_orchestration_boundaries.py`。

### C. Durable Outbox（spec WP-4.3）

- [ ] **C1** effect kind 只有 `launch_assignment`、`deliver_input`、`cancel_assignment`；未提前引入 P5/P6 种类。
- [ ] **C2** 创建/重试 Assignment 在同事务创建 `launch_assignment`；输入写入后创建 `deliver_input`。
- [ ] **C3** dispatcher 在 commit 后拉取 due effects；同 idempotency key 的 pending 重复入队只更新 payload，
      不新增副作用（SQLite 唯一键证明，非内存标志）。验证：`test_outbox.py`。证据：（待记录）。
- [ ] **C4** handler 成功才标 `succeeded`；失败用注入式 backoff 进 `retry_wait`，达 `max_attempts` 标 `failed`
      并派生 `blocked`/`failed` Report。证据：（待记录）。
- [ ] **C5** handler 的 I/O 不持有事务；重启后 pending/retry_wait 继续执行。
      验证："handler 成功前 crash"用例 + 集成恢复测试。证据：（待记录）。
- [ ] **C6** 按 kind 保序：同一 Assignment 的输入按 `sequence` 投递；不同 Ticket 并发。证据：（待记录）。
- [ ] **C7** spec 要求的七类 outbox 场景测试齐备（commit 后 crash、handler 前 crash、重复 dispatch、
      失败重试、超限失败、同 Ticket 保序、不同 Ticket 并发）。证据：（待记录）。

### D. WorkerExecutor 与 Event→Report（spec WP-4.4）

- [ ] **D1** `WorkerExecutor` 协议四方法签名与 spec 一致，测试桩可实现（P5 可替换）。
- [ ] **D2** `AgentExecutor` 由 Assignment 保存 Agent 配置版本与 `worker_run_id`；首次 `stream()`，
      恢复 `stream_recover()`，绝不手工重放 Event 或复制 state。证据：（待记录）。
- [ ] **D3** Event→Report 映射：`THINKING`/`TOOL_CALL`/`TOOL_RESULT`→可选 progress，`PAUSED`→waiting/blocked，
      `DONE`→ready，`ERROR`→failed；默认只在 `TOOL_RESULT` 落 progress。证据：（待记录）。
- [ ] **D4** Worker Event 原样保留为诊断载荷，但 Ticket 状态判定不读自然语言文本。
      验证：断言映射输入为 `EventType`/`AgentStatus`/reason 字段。证据：（待记录）。
- [ ] **D5** 启动前注册 `worker_run_id`，同 run 禁止两个 executor 并行驱动（L7 同源）。
- [ ] **D6** 初始输入作为 task 进入 Worker；后续输入只在空闲/暂停边界投递，不抢占执行中的 `advance()`。
- [ ] **D7** 核心测试不需要真实 LLM：FakeExecutor + FakeProvider 覆盖映射、恢复、取消、重复 launch 防护。

### E. Scheduler、并发与 DAG（spec WP-4.5）

- [ ] **E1** 可运行性只从 Store 推导，无不可恢复的内存队列。验证：`test_scheduler.py` 重开用例。
- [ ] **E2** `max_concurrency` 限制 running Assignment 数；槽位从 effect 领取起占到终态/waiting/blocked 释放。
- [ ] **E3** 子 Ticket 只由 API/测试显式创建；未引入 LLM Planner。
- [ ] **E4** DAG 添加前检测直接环与间接环，拒绝并抛稳定错误。
- [ ] **E5** 依赖满足判定：所有依赖 `completed` 才可运行。
- [ ] **E6** `block` 使下游 `blocked` 且写结构化原因；`continue` 允许下游运行并把失败依赖作为 Input/metadata。
- [ ] **E7** 优先级 `(唤醒过的优先, queued_at, created_at)` 实现并被顺序测试锁死（ADR-04 决策 7）。
- [ ] **E8** `recover()` 覆盖 spec WP-4.5.7 全部语义：running→interrupted、幂等重启、旧新 effect 同规则。
- [ ] **E9** 外层 Assignment deadline（来源：[ADR-02 后果 4](../design-decisions/02-checkpoint.md) 中属于 P4 的那一半，
      处置见 [ADR-04](../design-decisions/04-ticket-orchestration.md)）：`deadline_at` 超时由 Scheduler 以
      `asyncio.timeout` 包住 executor 任务，写 `cancel_assignment` effect，Assignment 进 `cancelled`、
      Ticket 进 `blocked` 并带结构化原因；不触碰 Core 调用点。
      验证：`test_scheduler.py` 用注入式 clock 触发超时，断言无墙钟等待。
      **不在本项范围**：Provider 单次调用的内层硬超时，归 P7 WP-7.2。证据：（待记录）。

### F. 输入投递与 Core 扩展（ADR-04 决策 6）

- [ ] **F1** `submit_input(ticket_id, text)` 落库为未消费 `TicketInput` 并创建 `deliver_input` effect。
- [ ] **F2** `Agent.resume()/aresume(extra_input=None)`：追加 `Message(role="user")` 到 state 副本后才继续；
      无 PAUSED 态时沿用既有 `ValueError`。验证：`test_agent_recovery.py` 新用例。证据：（待记录）。
- [ ] **F3** L4 默认路径复验：重新 dump 与既有 fixture 逐字节相等；`extra_input` 默认值不改变任何 payload。
      证据：（待记录 dump 与 diff 结果）。
- [ ] **F4** `core/loop.py` 仍零 diff（L1 复核），且 `git diff --stat -- stableagent/core/` 只出现 `agent.py`（L2）。

### G. 本地接口、示例与故障演练（spec WP-4.6）

- [ ] **G1** `examples/04_ticket_orchestration.py` 可离线运行并打印时间线（父/子 Ticket + 依赖 + 两个脚本 Worker）。
- [ ] **G2** 集成测试：子进程 + SIGKILL + 新进程 `recover()`，终态正确、已完成 Assignment 不重复启动。
      证据：（待记录测试名与运行输出）。
- [ ] **G3** ADR-04 记录 Ticket/AgentState 边界、状态机、outbox 事务、幂等键、输入投递语义、
      单驱动规则与被否决方案（已成文，实现后把"计划"字样改为实际证据）。
- [ ] **G4** `OrchestrationService` 十个命令全部可用且非法输入抛稳定 `OrchestrationError` 子类
      （spec 原文写 `UserError`，Core 无此基类，口径见 spec 第 6 节第 8 条）
      （未知 ID、非法状态、环、已结束写入、跨 Ticket 报告）。证据：（待记录）。
- [ ] **G5** `errors.py` 的 code 清单在账本里登记，供 P5/P6 映射。证据：（待记录）。

### H. 质量与发布

- [ ] **H1** spec §4 必测场景七类（模型/Store/Outbox/Executor/Scheduler/集成/回归）逐条有对应测试。
- [ ] **H2** 本地：`ruff check` 0 error、`ruff format --check` 干净、`mypy` 无问题、`pytest -q` 全绿。
      证据：（待记录输出）。
- [ ] **H3** 覆盖率：全库 ≥ 现有基线（P3 收口为 98.99%），orchestration 六个心脏文件每文件 ≥90%。
      证据：（待记录 `--cov-report=term-missing` 摘录）。
- [ ] **H4** Python 3.10 / 3.11 / 3.12 本地等价验证通过。证据：（待记录）。
- [ ] **H5** CI FOCUS 扩到 18 文件并本地复现门槛逻辑；步骤名与注释同步。证据：（待记录）。
- [ ] **H6** 远端 CI 收口：经授权推送后 GitHub Actions 3.10 / 3.12 全绿，run URL/ID 记入本账本，
      且 P0–P3 用例零回归。证据：（待记录）。
- [ ] **H7** 编排成本的记录方式符合"不把未经复跑的数字写成当前结果"：单轮 `tick()` 与
      `recover()` 全部 effect 重放的耗时，在固定规模（计划取 50 Ticket / 100 Assignment）下由
      `benchmarks/` 脚本复跑并记入 `docs/evidence/p4-orchestration-benchmark-<yyyy-mm-dd>.md`，
      同时给出命令、参数、日期与波动范围。该记录只为验证 ADR-04 承认的"同步 sqlite 短暂阻塞事件循环"
      代价，不作任何对外性能承诺。证据：（待记录）。

---

## 3. 阶段验证命令

```bash
# 单包
.venv/bin/pytest -q tests/unit/test_orchestration_model.py tests/unit/test_orchestration_store.py
.venv/bin/pytest -q tests/unit/test_outbox.py tests/unit/test_agent_executor.py tests/unit/test_scheduler.py
.venv/bin/pytest -q tests/unit/test_orchestration_boundaries.py
.venv/bin/pytest -q tests/integration/test_ticket_recovery.py

# 心脏边界复核
git diff 46441f0..HEAD -- stableagent/core/loop.py            # 必须为空
git diff 46441f0..HEAD --stat -- stableagent/core/            # 只允许 agent.py
.venv/bin/python tests/l4_default_stream_dump.py              # 与 fixture 逐字节一致

# 全库门禁
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
.venv/bin/pytest --cov=stableagent --cov-report=xml --cov-report=term-missing
```

## 4. 已知风险与预案

- **同步 sqlite 阻塞事件循环**：P4 接受并在 ADR-04 记录；用单轮 `tick()` 耗时离线基准量化，不外推承诺。
- **M2 引入保序/多 handler 时重构 dispatcher**：走骨架的既定代价；M1 只实现 `launch_assignment`，
  注册表接口预留但不提前实现队列语义。
- **SIGKILL 演练的击杀竞态**：P2 已踩过（见 `docs/design-decisions/02-checkpoint.md` 后果 5）。
  只在编排侧控制步数，不用 sleep 赌时机；击杀点由 Assignment 完成计数触发。
- **示例腐烂**：`examples/04` 必须有一条 selftest 分支进 CI，否则 P2/P3 的先例（示例即测试）失效。
- **优先级排序键一旦锁死即成契约**：改动会牵动调度测试，需要在 ADR 层记录再改。

## 5. 未满足时的归属

任一 DoD 未达：属于 L1/L2/L4/L6/L7 的越界问题一律先改设计与 ADR，再改实现（specs/README 规则 5）；
属于功能缺口的记在本条为"延期到 M2/M3"并写明阻塞原因，不得把未验证的能力标为完成
（documentation-standards §2 状态标签规则）。

---

## 6. M1 任务级实施计划（writing-plans 产出，2026-11-02）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development`（推荐）或
> `superpowers:executing-plans` 逐任务实施；每个 Step 是一步动作，勾选推进。
> 本计划只覆盖 **M1**；M2/M3 在 M1 出口命令全绿后各写一份同粒度计划（规格使用规则 4）。

**Goal:** 交付 `stableagent/orchestration/` 的端到端骨架——一个 Ticket 被持久化、经 durable outbox
启动一个 Worker、把 Worker 事件映射成 Report，并在子进程被 SIGKILL 后由新进程 `recover()` 跑完且不重跑已完成工作。

**Architecture:** 单 asyncio 事件循环驱动；`Store`（stdlib `sqlite3`）是唯一持久层，"状态变更 + effect
入队"在同一事务内完成，Worker 只能由提交后被领取的 effect 启动；`AgentExecutor` 通过 `AssignmentSink`
协议回调 `OrchestrationService` 写入，保证 service 是唯一写入口。Core 只在 `core/agent.py` 之外保持零改动，
M1 甚至不动 `agent.py`（`extra_input` 扩展属 M2）。

**Tech Stack:** Python 3.10+、Pydantic v2、stdlib `sqlite3` / `asyncio` / `uuid`、pytest（`asyncio_mode=auto`）、
ruff、mypy。

**Spec:** [P4 实施规格](../specs/p4-ticket-orchestration-kernel.md)（含第 6 节八条决策补充）
与 [ADR-04](../design-decisions/04-ticket-orchestration.md)。两份必须与本计划一起阅读。

### Global Constraints（每个任务都隐含遵守）

- 运行时依赖恰为三个：`pydantic>=2.7`、`anyio>=4.3`、`httpx>=0.27`；禁止新增（不用 `aiosqlite`、不用 ORM）。
- `git diff 46441f0..HEAD -- stableagent/core/loop.py` 必须为空；M1 期间 `stableagent/core/` 与
  `stableagent/context/` 一行不改。
- `stableagent/orchestration/**` 不得被 `core/**` 或 `context/**` 导入（依赖单向，L4）。
- 顶层 `stableagent/__init__.py` 不重导出编排类型；版本常量是
  `stableagent/orchestration/__init__.py` 里的 `ORCHESTRATION_SCHEMA_VERSION = "0.1.0"`。
- `Store.transaction()` 内禁止 `await`；提交之前不得启动 Worker（L6）。
- 时间一律 `int` 毫秒（`clock.now_ms()`），ID 一律 `ids.new_id(prefix)`；测试注入序列工厂，禁墙钟与随机。
- 测试全离线：无网络、无真实 Provider、无 secrets。
- 代码风格：`line-length = 100`，ruff `select = ["E","F","W","I","UP","B","SIM"]`，mypy `python_version = "3.10"`
  且 `files = ["stableagent"]`；文档文件名小写 ASCII，中文正文用 ASCII 双引号（沿用 P3 账本约定）。
- 提交信息风格沿用仓库现状：小写开头、无 scope 括号、说清"为什么"（见 `git log`）。

### 文件结构（M1）

```text
stableagent/orchestration/
  __init__.py     # 版本常量 + 包内导出（不进顶层）
  errors.py       # OrchestrationError 家族 + 稳定 code
  model.py        # 枚举、IdFactory/Clock 协议、7 个领域模型、3 个纯状态转换函数
  store.py        # schema/迁移/transaction/仓储方法（无调度与策略判断）
  outbox.py       # EffectDispatcher：领取 due effect、幂等、reclaim
  executor.py     # WorkerExecutor 协议 + AssignmentSink 协议 + AgentExecutor
  scheduler.py    # Scheduler.tick()/drain()、槽位、_tasks 取消句柄
  service.py      # OrchestrationService（唯一写入口）+ TicketView + RecoverySummary
tests/
  support.py                                      # 修改：追加 SequenceIds / FixedClock / RecordingSink / StubAgent
  unit/test_orchestration_model.py                # 新
  unit/test_orchestration_store.py                # 新
  unit/test_outbox.py                             # 新
  unit/test_agent_executor.py                     # 新
  unit/test_scheduler.py                          # 新
  unit/test_orchestration_service.py              # 新
  unit/test_orchestration_boundaries.py           # 新
  integration/ticket_recovery_child.py            # 新（子进程入口，M3 的 examples/04 复用同一形状）
  integration/test_ticket_recovery.py             # 新
```

`46441f0` 是 P2 收口提交（L1 基线）。`AssignmentSink` 定义在 `executor.py`，由 `service.py` 实现——
这条方向是故意的：executor 不知道 service 存在，只认识协议。

### 任务 ↔ 里程碑步骤与 DoD 对照

第 1 节的 `M1.x` 是记账单位（首屏"里程碑步骤 0/30"数的是它们）；本节的 Task 是执行单位。
一个 Task 的 5 个 Step 全勾完，才回来勾对应的 `M1.x`；勾 DoD 项以第 2 节每条自己写的验证命令为准，
下表只是"这项证据在哪个 Task 产生"的索引，不扩大第 1.4 节的 M1 归属。

| Task | 里程碑步骤 | 本 Task 产生的 M1 归属 DoD 证据 |
|---|---|---|
| 1 | M1.1（并提前建 M1.8 的边界测试第一版） | L4 前半（方向扫描 + 版本不重导出） |
| 2 | M1.2 | A1、A2、A4 前半 |
| 3 | M1.2 | A3、A6 |
| 4 | M1.3 | B1、B2、B3、L6 前半；B4/B6 前半 |
| 5 | M1.3 | B4、B6 收尾（唯一键、重开、排序、分页） |
| 6 | M1.4 | B5、C1、C3、L6 中段、E1 前半 |
| 7 | M1.5 | A4 收尾、D1、D2、D3、D5、D7 |
| 8 | M1.6 | E1、L7 |
| 9 | M1.7 | A5、C2、L6 收尾 |
| 10 | M1.8 + M1.9 + M1 出口 | L1、L3、L4 收尾、L5、L8、B7、G2 |

第 1.4 节 M1 行的 30 项在这里全部有落点：L1、L3~L8 七项，A1~A6 六项，B1~B7 七项，C1~C3 三项，
D1、D2、D3、D5、D7 五项，E1、G2 两项。带"前半/收尾"的项只在收尾那个 Task 之后才勾。

两处与第 1 节顺序的有意偏差，都在账本里说明：

- `tests/unit/test_orchestration_boundaries.py`（M1.8）在 Task 1 就建立，因为"包不存在"是最早的红灯；
  Task 10 只补 L1 零 diff 与 L5 断言，不新建第二个边界文件。
- Task 5 只实现 M1 用得上的排序键（唤醒过的优先 → `queued_at` → `created_at`）与分页；
  E7 的完整锁定与 `max_concurrency` 宽度在 M2.8，届时复用同一批用例再补断言。

M1 期间 Task 7/8/9 的测试会顺手覆盖一些 M2 归属项的前半（`D4` 的"只读结构化字段"、
`E2` 的单槽占位、`G4` 的前七个命令），这些项**不在 M1 勾选**，等 M2 计划补齐后半再勾。

---

### Task 1: 包骨架、版本常量与稳定错误码

**Files:**
- Create: `stableagent/orchestration/__init__.py`、`stableagent/orchestration/errors.py`
- Modify: `stableagent/planning/__init__.py`（docstring 指向新包）
- Test: `tests/unit/test_orchestration_boundaries.py`

**Interfaces:**
- Produces: `ORCHESTRATION_SCHEMA_VERSION: str = "0.1.0"`；
  `OrchestrationError(code: str, message: str)`（继承 `ValueError`、带 `.code`；Core 里没有
  `UserError` 基类可继承，口径见规格第 6 节第 8 条）与子类
  `IllegalTransitionError` / `UnknownTicketError` / `UnknownAssignmentError` / `ReentrantTransactionError` /
  `DuplicateLaunchError` / `WorkerConfigError` / `IdempotencyConflictError`。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/test_orchestration_boundaries.py
"""L4 依赖方向 + 编排层版本口径（ADR-04 决策 1）。"""

import ast
import pathlib

import pytest

import stableagent
from stableagent.orchestration import ORCHESTRATION_SCHEMA_VERSION
from stableagent.orchestration.errors import IdempotencyConflictError, OrchestrationError

ROOT = pathlib.Path(stableagent.__file__).parent


def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("package", ["core", "context", "providers", "tools"])
def test_core_layers_never_import_orchestration(package: str) -> None:
    for module in (ROOT / package).rglob("*.py"):
        assert "stableagent.orchestration" not in _imports_of(module), module


def test_orchestration_version_is_independent_and_not_reexported() -> None:
    assert ORCHESTRATION_SCHEMA_VERSION == "0.1.0"
    assert "Ticket" not in dir(stableagent)  # 顶层不重导出（P6 Beta 前不承诺稳定）


def test_every_error_carries_a_stable_code() -> None:
    assert issubclass(OrchestrationError, ValueError)
    assert IdempotencyConflictError("x", "y").code == "orchestration.idempotency_conflict"
```

- [ ] **Step 2: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_boundaries.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'stableagent.orchestration'`

- [ ] **Step 3: 最小实现**

```python
# stableagent/orchestration/errors.py
"""编排层错误：每个实例带一个稳定 code，P5/P6 按 code 映射，不靠异常类型文案。"""

from __future__ import annotations


class OrchestrationError(ValueError):
    """所有编排层用户可读错误的基类。

    Core 里没有 `UserError` 基类（规格第 6 节第 8 条记录这一口径）：L2 禁止为错误类型再动 Core，
    所以本类直接继承 `ValueError`，用 `.code` 提供机器可判的稳定标识。
    """

    code = "orchestration.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class IllegalTransitionError(OrchestrationError):
    def __init__(self, machine: str, current: str, action: str) -> None:
        super().__init__(
            "orchestration.illegal_transition",
            f"{machine} 不允许从 {current} 执行 {action}",
        )


class UnknownTicketError(OrchestrationError):
    def __init__(self, ticket_id: str) -> None:
        super().__init__("orchestration.unknown_ticket", f"没有 ticket_id={ticket_id} 的记录")


class UnknownAssignmentError(OrchestrationError):
    def __init__(self, assignment_id: str) -> None:
        super().__init__(
            "orchestration.unknown_assignment", f"没有 assignment_id={assignment_id} 的记录"
        )


class ReentrantTransactionError(OrchestrationError):
    def __init__(self) -> None:
        super().__init__(
            "orchestration.reentrant_transaction",
            "Store.transaction() 不可重入：事务体内禁止再次开启事务或 await",
        )


class DuplicateLaunchError(OrchestrationError):
    def __init__(self, worker_run_id: str) -> None:
        super().__init__(
            "orchestration.duplicate_launch",
            f"worker_run_id={worker_run_id} 已有驱动者，拒绝第二个（L7 单 run 单驱动）",
        )


class WorkerConfigError(OrchestrationError):
    def __init__(self, reason: str) -> None:
        super().__init__("orchestration.worker_config", f"Worker 配置不可编排：{reason}")


class IdempotencyConflictError(OrchestrationError):
    def __init__(self, kind: str, key: str) -> None:
        super().__init__(
            "orchestration.idempotency_conflict", f"effect {kind}/{key} 已处于终态"
        )
```

```python
# stableagent/orchestration/__init__.py
"""P4 控制平面：Ticket / Assignment / Report / Effect 与调度。

编排层版本独立于 Core 的 CONTRACT_VERSION：P6 Beta 之前不承诺记录结构稳定，
因此本包类型不在 stableagent 顶层重导出（决策见 ADR-04 决策 1）。
"""

from __future__ import annotations

ORCHESTRATION_SCHEMA_VERSION = "0.1.0"

__all__ = ["ORCHESTRATION_SCHEMA_VERSION"]
```

把 `stableagent/planning/__init__.py` 的 docstring 改为
`"""任务规划占位：P6+ 的 LLM Planner。P4 的编排内核在 stableagent/orchestration/。"""`。

- [ ] **Step 4: 跑到绿**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_boundaries.py && .venv/bin/mypy && .venv/bin/ruff check stableagent`
Expected: PASS（4 个 parametrize + 2 条断言全绿，mypy 无问题）

- [ ] **Step 5: 提交**

```bash
git add stableagent/orchestration/ stableagent/planning/__init__.py tests/unit/test_orchestration_boundaries.py
git commit -m "feat: open the orchestration package with its own version and stable codes"
```

**关闭 DoD：** L4 前半（依赖方向扫描 + 版本不重导出）。G5 的 code 清单登记属 M2，本任务不勾；
L4 要等 Task 10 补上 L1/L5 断言才整条勾。

---

### Task 2: 领域模型与注入式 ID/时钟

**Files:**
- Create: `stableagent/orchestration/model.py`（模型 + 协议部分）
- Modify: `tests/support.py`（追加 `SequenceIds` / `FixedClock`）
- Test: `tests/unit/test_orchestration_model.py`

**Interfaces:**
- Produces: `IdFactory`（`new_id(prefix: str) -> str`）、`Clock`（`now_ms() -> int`）、
  `Uuid4Id`、`SystemClock`；枚举 `TicketStatus` / `AssignmentStatus` / `EffectState` / `EffectKind` /
  `ReportKind` / `TicketAction` / `AssignmentAction` / `EffectAction`；
  模型 `Mandate` / `Ticket` / `Assignment` / `TicketInput` / `Report` / `Dependency` / `Effect`。
- Consumes: Task 1 的 `errors`。

- [ ] **Step 1: 写测试资产**

```python
# tests/support.py 追加（不改动既有类）
class SequenceIds:
    """确定性 ID：同前缀按序号递增，测试可读且可断言。"""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]:04d}"


class FixedClock:
    """可推进的毫秒时钟；测试绝不读墙钟。"""

    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self._now = start

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> None:
        self._now += ms
```

- [ ] **Step 2: 写失败的测试**

```python
# tests/unit/test_orchestration_model.py
"""A1/A2/A4：字段语义、序列化 round-trip、模型级校验与确定性 ID。"""

from __future__ import annotations

import pytest

from stableagent.orchestration.model import (
    Assignment,
    Dependency,
    Mandate,
    Ticket,
)
from tests.support import FixedClock, SequenceIds


def _ticket(ids: SequenceIds, clock: FixedClock) -> Ticket:
    return Ticket(
        id=ids.new_id("tk"),
        title="写一份恢复报告",
        task="统计 checkpoint 事件数量",
        created_at=clock.now_ms(),
        mandate_id=ids.new_id("md"),
    )


def test_ticket_round_trips_and_is_deterministic() -> None:
    ids, clock = SequenceIds(), FixedClock()
    ticket = _ticket(ids, clock)
    assert ticket.id == "tk-0001" and ticket.status.value == "open"
    assert Ticket.model_validate_json(ticket.model_dump_json()) == ticket


def test_mandate_defaults_are_the_p4_local_shape() -> None:
    mandate = Mandate(id="md-1", granted_at=0)
    assert (mandate.origin_kind, mandate.requester, mandate.policy_version) == (
        "local",
        "local",
        "p4-local-v1",
    )


def test_assignment_attempt_starts_at_one_and_rejects_zero() -> None:
    with pytest.raises(ValueError):
        Assignment(id="as-1", ticket_id="tk-1", attempt=0)


def test_worker_run_id_is_nullable_until_launch() -> None:
    assignment = Assignment(id="as-1", ticket_id="tk-1", attempt=1)
    assert assignment.worker_run_id is None
    assert assignment.model_copy(update={"worker_run_id": "r1"}).worker_run_id == "r1"


def test_dependency_rejects_self_reference() -> None:
    with pytest.raises(ValueError):
        Dependency(ticket_id="tk-1", depends_on_ticket_id="tk-1")


def test_assignment_belongs_to_a_ticket_by_construction() -> None:
    # ticket_id 无默认值：缺了就构造不出来，A4 的"必属某 Ticket"是结构保证而非运行时检查。
    with pytest.raises(ValueError):
        Assignment(id="as-1", attempt=1)
```

- [ ] **Step 3: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_model.py -v`
Expected: FAIL —`ModuleNotFoundError: ... orchestration.model`

- [ ] **Step 4: 实现模型**

```python
# stableagent/orchestration/model.py（模型与协议部分；状态机在 Task 3 追加到同一文件）
"""P4 领域记录。新字段一律带默认值（兼容扩展规则），时间用毫秒整数，ID 由注入工厂生成。"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator


class IdFactory(Protocol):
    def new_id(self, prefix: str) -> str: ...


class Clock(Protocol):
    def now_ms(self) -> int: ...


class Uuid4Id:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


class SystemClock:
    def now_ms(self) -> int:
        import time

        return time.time_ns() // 1_000_000


class TicketStatus(str, Enum):
    OPEN = "open"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class AssignmentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    ENDED = "ended"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class EffectState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EffectKind(str, Enum):
    LAUNCH_ASSIGNMENT = "launch_assignment"


class ReportKind(str, Enum):
    PROGRESS = "progress"
    WAITING_FOR_HUMAN = "waiting_for_human"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


class Mandate(BaseModel):
    id: str
    origin_kind: Literal["local"] = "local"
    requester: str = "local"
    scope: str = ""
    policy_version: str = "p4-local-v1"
    granted_at: int


class Ticket(BaseModel):
    id: str
    title: str
    task: str
    created_at: int
    status: TicketStatus = TicketStatus.OPEN
    mandate_id: str
    parent_id: str | None = None
    ended_at: int | None = None
    queued_at: int | None = None
    woken_at: int | None = None


class Assignment(BaseModel):
    id: str
    ticket_id: str
    attempt: int = Field(default=1, ge=1)
    status: AssignmentStatus = AssignmentStatus.PENDING
    worker_run_id: str | None = None
    started_at: int | None = None
    ended_at: int | None = None
    checkpoint_run_id: str | None = None
    failure: str | None = None
    worker_config_version: str | None = None
    worker_config: dict[str, Any] = Field(default_factory=dict)


class TicketInput(BaseModel):
    id: str
    ticket_id: str
    sequence: int
    kind: Literal["task", "text"] = "task"
    payload: str
    created_at: int
    delivered_at: int | None = None
    consumed_at: int | None = None


class Report(BaseModel):
    id: str
    ticket_id: str
    assignment_id: str
    kind: ReportKind
    summary: str
    created_at: int
    event_step: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Dependency(BaseModel):
    ticket_id: str
    depends_on_ticket_id: str
    on_failure: Literal["block", "continue"] = "block"

    @model_validator(mode="after")
    def _reject_self_dependency(self) -> Dependency:
        if self.ticket_id == self.depends_on_ticket_id:
            raise ValueError(f"A ticket cannot depend on itself: {self.ticket_id}")
        return self


class Effect(BaseModel):
    id: str
    kind: EffectKind
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    state: EffectState = EffectState.PENDING
    attempts: int = 0
    due_at: int
    last_error: str | None = None
```

- [ ] **Step 5: 跑到绿并提交**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_model.py && .venv/bin/mypy`
Expected: PASS

```bash
git add stableagent/orchestration/model.py tests/support.py tests/unit/test_orchestration_model.py
git commit -m "feat: give the control plane its records, ids and clocks"
```

**关闭 DoD：** A1、A2、A4 前半（`attempt >= 1`、`ticket_id` 必填、依赖自指拒绝、run_id 可空）。
A4 的"启动后必填"半边由 Task 7 的行为断言闭合，到那时才整条勾 A4。

---

### Task 3: 三个纯状态转换函数与跨记录校验

**Files:**
- Modify: `stableagent/orchestration/model.py`（追加 action 枚举与转换函数）
- Test: `tests/unit/test_orchestration_model.py`（追加转换矩阵）

**Interfaces:**
- Produces: `TicketAction`（`QUEUE/START/WAIT/DELIVER/BLOCK/FAIL/CANCEL/COMPLETE`）、
  `AssignmentAction`（`LAUNCH/END/FAIL/CANCEL/INTERRUPT/RELAUNCH`）、
  `EffectAction`（`CLAIM/SUCCEED/RETRY/FAIL/CANCEL/RECLAIM`）、
  `next_ticket_status(current, action) -> TicketStatus`、
  `next_assignment_status(...)`、`next_effect_status(...)`、`TERMINAL_TICKET_STATUSES`、
  `assert_assignment_belongs_to(ticket, assignment) -> None`。非法组合抛 `IllegalTransitionError`。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/test_orchestration_model.py 追加
import pytest

from stableagent.orchestration.errors import IllegalTransitionError
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    Assignment,
    AssignmentAction,
    AssignmentStatus,
    EffectAction,
    EffectState,
    TicketAction,
    TicketStatus,
    next_assignment_status,
    next_effect_status,
    next_ticket_status,
)


@pytest.mark.parametrize(
    ("current", "action", "expected"),
    [
        (TicketStatus.OPEN, TicketAction.QUEUE, TicketStatus.QUEUED),
        (TicketStatus.QUEUED, TicketAction.START, TicketStatus.RUNNING),
        (TicketStatus.RUNNING, TicketAction.WAIT, TicketStatus.WAITING),
        (TicketStatus.WAITING, TicketAction.START, TicketStatus.RUNNING),
        (TicketStatus.RUNNING, TicketAction.DELIVER, TicketStatus.READY),
        (TicketStatus.RUNNING, TicketAction.BLOCK, TicketStatus.BLOCKED),
        (TicketStatus.RUNNING, TicketAction.FAIL, TicketStatus.FAILED),
        (TicketStatus.READY, TicketAction.COMPLETE, TicketStatus.COMPLETED),
        (TicketStatus.OPEN, TicketAction.CANCEL, TicketStatus.CANCELLED),
    ],
)
def test_legal_ticket_transitions(current, action, expected) -> None:
    assert next_ticket_status(current, action) is expected


def test_ready_is_not_completed_and_completed_is_terminal() -> None:
    # A6：ready 只是"可收口"，必须显式 complete_ticket 才进 completed。
    assert TicketStatus.READY is not TicketStatus.COMPLETED
    assert next_ticket_status(TicketStatus.READY, TicketAction.COMPLETE) is TicketStatus.COMPLETED
    assert TicketStatus.READY not in TERMINAL_TICKET_STATUSES
    assert {TicketStatus.COMPLETED, TicketStatus.CANCELLED, TicketStatus.FAILED} == (
        TERMINAL_TICKET_STATUSES
    )
    with pytest.raises(IllegalTransitionError):
        next_ticket_status(TicketStatus.COMPLETED, TicketAction.CANCEL)


def test_terminal_assignment_may_have_no_run_id() -> None:
    # 注册发生在第一个 step 边界（ADR-04 决策 4.1），崩在注册之前的 Assignment 终态也没有 run_id。
    # 因此"启动后必填"不是模型不变量（RUNNING 早于注册就会误伤），而是 Task 7 的行为断言：
    # launch 路径必须把首个 CHECKPOINT 的 run_id 注册回来。
    ended = Assignment(
        id="as-1", ticket_id="tk-1", attempt=1, status=AssignmentStatus.ENDED, worker_run_id=None
    )
    assert ended.worker_run_id is None


def test_terminal_assignment_cannot_be_relaunched() -> None:
    assert next_assignment_status(AssignmentStatus.INTERRUPTED, AssignmentAction.RELAUNCH) is (
        AssignmentStatus.RUNNING
    )
    with pytest.raises(IllegalTransitionError):
        next_assignment_status(AssignmentStatus.ENDED, AssignmentAction.RELAUNCH)


def test_effect_claim_and_retry_paths() -> None:
    assert next_effect_status(EffectState.PENDING, EffectAction.CLAIM) is EffectState.RUNNING
    assert next_effect_status(EffectState.RUNNING, EffectAction.RETRY) is EffectState.RETRY_WAIT
    assert next_effect_status(EffectState.RETRY_WAIT, EffectAction.CLAIM) is EffectState.RUNNING
    with pytest.raises(IllegalTransitionError):
        next_effect_status(EffectState.SUCCEEDED, EffectAction.CLAIM)
```

- [ ] **Step 2: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_model.py -k transition or assignment or effect`
Expected: FAIL —`ImportError: cannot import name 'TicketAction'`

- [ ] **Step 3: 实现转换表**

```python
# stableagent/orchestration/model.py 追加
class TicketAction(str, Enum):
    QUEUE = "queue"
    START = "start"
    WAIT = "wait"
    DELIVER = "deliver"
    BLOCK = "block"
    FAIL = "fail"
    CANCEL = "cancel"
    COMPLETE = "complete"


class AssignmentAction(str, Enum):
    LAUNCH = "launch"
    END = "end"
    FAIL = "fail"
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    RELAUNCH = "relaunch"


class EffectAction(str, Enum):
    CLAIM = "claim"
    SUCCEED = "succeed"
    RETRY = "retry"
    FAIL = "fail"
    CANCEL = "cancel"
    RECLAIM = "reclaim"


_TICKET_TABLE: dict[tuple[TicketStatus, TicketAction], TicketStatus] = {
    (TicketStatus.OPEN, TicketAction.QUEUE): TicketStatus.QUEUED,
    (TicketStatus.OPEN, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.QUEUED, TicketAction.START): TicketStatus.RUNNING,
    (TicketStatus.QUEUED, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.RUNNING, TicketAction.WAIT): TicketStatus.WAITING,
    (TicketStatus.RUNNING, TicketAction.DELIVER): TicketStatus.READY,
    (TicketStatus.RUNNING, TicketAction.BLOCK): TicketStatus.BLOCKED,
    (TicketStatus.RUNNING, TicketAction.FAIL): TicketStatus.FAILED,
    (TicketStatus.RUNNING, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.WAITING, TicketAction.START): TicketStatus.RUNNING,
    (TicketStatus.WAITING, TicketAction.BLOCK): TicketStatus.BLOCKED,
    (TicketStatus.WAITING, TicketAction.FAIL): TicketStatus.FAILED,
    (TicketStatus.WAITING, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.BLOCKED, TicketAction.QUEUE): TicketStatus.QUEUED,
    (TicketStatus.BLOCKED, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.READY, TicketAction.COMPLETE): TicketStatus.COMPLETED,
    (TicketStatus.READY, TicketAction.CANCEL): TicketStatus.CANCELLED,
}

_ASSIGNMENT_TABLE: dict[tuple[AssignmentStatus, AssignmentAction], AssignmentStatus] = {
    (AssignmentStatus.PENDING, AssignmentAction.LAUNCH): AssignmentStatus.RUNNING,
    (AssignmentStatus.PENDING, AssignmentAction.CANCEL): AssignmentStatus.CANCELLED,
    (AssignmentStatus.RUNNING, AssignmentAction.END): AssignmentStatus.ENDED,
    (AssignmentStatus.RUNNING, AssignmentAction.FAIL): AssignmentStatus.FAILED,
    (AssignmentStatus.RUNNING, AssignmentAction.CANCEL): AssignmentStatus.CANCELLED,
    (AssignmentStatus.RUNNING, AssignmentAction.INTERRUPT): AssignmentStatus.INTERRUPTED,
    (AssignmentStatus.INTERRUPTED, AssignmentAction.RELAUNCH): AssignmentStatus.RUNNING,
    (AssignmentStatus.INTERRUPTED, AssignmentAction.CANCEL): AssignmentStatus.CANCELLED,
}

_EFFECT_TABLE: dict[tuple[EffectState, EffectAction], EffectState] = {
    (EffectState.PENDING, EffectAction.CLAIM): EffectState.RUNNING,
    (EffectState.PENDING, EffectAction.CANCEL): EffectState.CANCELLED,
    (EffectState.RUNNING, EffectAction.SUCCEED): EffectState.SUCCEEDED,
    (EffectState.RUNNING, EffectAction.RETRY): EffectState.RETRY_WAIT,
    (EffectState.RUNNING, EffectAction.FAIL): EffectState.FAILED,
    (EffectState.RUNNING, EffectAction.RECLAIM): EffectState.PENDING,
    (EffectState.RETRY_WAIT, EffectAction.CLAIM): EffectState.RUNNING,
    (EffectState.RETRY_WAIT, EffectAction.FAIL): EffectState.FAILED,
    (EffectState.RETRY_WAIT, EffectAction.CANCEL): EffectState.CANCELLED,
}

TERMINAL_TICKET_STATUSES = frozenset(
    {TicketStatus.COMPLETED, TicketStatus.CANCELLED, TicketStatus.FAILED}
)


def _step(table, machine, current, action):
    try:
        return table[(current, action)]
    except KeyError as exc:
        raise IllegalTransitionError(machine, current.value, action.value) from exc


def next_ticket_status(current: TicketStatus, action: TicketAction) -> TicketStatus:
    return _step(_TICKET_TABLE, "ticket", current, action)


def next_assignment_status(
    current: AssignmentStatus, action: AssignmentAction
) -> AssignmentStatus:
    return _step(_ASSIGNMENT_TABLE, "assignment", current, action)


def next_effect_status(current: EffectState, action: EffectAction) -> EffectState:
    return _step(_EFFECT_TABLE, "effect", current, action)


def assert_assignment_belongs_to(ticket: Ticket, assignment: Assignment) -> None:
    if assignment.ticket_id != ticket.id:
        raise IllegalTransitionError(
            "assignment", assignment.status.value, f"cross_ticket:{ticket.id}"
        )
```

A4 的"启动后必填"半边不在这里做模型不变量：`RUNNING` 早于 run_id 注册（ADR-04 决策 4.1），
硬校验会把 Task 7 的正常转换打成红。它由 Task 7 的行为断言闭合，见该任务 Step 1 的
`test_run_id_is_registered_once_at_the_first_step_boundary`。
```

注意：`TicketAction.START` 同时用于 `QUEUED→RUNNING` 与 `WAITING→RUNNING`（唤醒继续跑），
`BLOCKED→QUEUE` 是给"人工重试/输入唤醒"重新排队的；`FAILED`/`CANCELLED`/`COMPLETED` 是终态，
转换表里没有出边即为非法（A3/A6）。

- [ ] **Step 4: 跑到绿**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_model.py && .venv/bin/mypy`
Expected: PASS。删掉测试里 `or True` 那行的宽松断言，改为
`assert (TicketStatus.READY, TicketAction.COMPLETE) in {(TicketStatus.READY, TicketAction.COMPLETE)}`——
不，改为 `assert next_ticket_status(TicketStatus.READY, TicketAction.COMPLETE) is TicketStatus.COMPLETED`
并保留 `ready` 不等于 `completed` 的独立断言：
`assert TicketStatus.READY is not TicketStatus.COMPLETED`。

- [ ] **Step 5: 提交**

```bash
git add stableagent/orchestration/model.py tests/unit/test_orchestration_model.py
git commit -m "feat: put every P4 state machine behind one pure transition table"
```

**关闭 DoD：** A3、A6。
---

### Task 4: SQLite schema、事务守卫与迁移

**Files:**
- Create: `stableagent/orchestration/store.py`
- Test: `tests/unit/test_orchestration_store.py`

**Interfaces:**
- Produces: `Store(db_path: str | Path, *, clock: Clock)`；`store.in_transaction -> bool`；
  `store.transaction() -> ContextManager[None]`；`store.schema_version() -> str`；`store.close()`；
  DDL 常量 `_SCHEMA: dict[str, str]`（8 张表）。仓储方法在 Task 5 追加。
- Consumes: Task 1 errors（`ReentrantTransactionError`）、Task 2 模型。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/test_orchestration_store.py
"""B1/B2/B3/B4/B6 + L6：schema、事务原子性、重入守卫、唯一键、重开无损。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.orchestration.errors import ReentrantTransactionError
from stableagent.orchestration.store import ORCHESTRATION_TABLES, Store
from tests.support import FixedClock

TABLES = {
    "tickets",
    "mandates",
    "assignments",
    "inputs",
    "reports",
    "dependencies",
    "effects",
    "schema_migrations",
}

# 本文件允许直接用 store._conn 写裸 SQL：Task 4 的验收对象就是 schema 与事务，仓储方法要到
# Task 5 才存在。这种白盒写法只限 test_orchestration_store.py，其它测试一律走公开仓储方法。


@pytest.fixture
def store(tmp_path: Path):
    handle = Store(tmp_path / "orchestration.db", clock=FixedClock())
    yield handle
    handle.close()


def test_all_eight_tables_exist_with_schema_version(store: Store) -> None:
    names = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert TABLES <= names
    assert store.schema_version() == "0.1.0"


def test_transaction_is_atomic_for_both_ticket_and_effect(store: Store) -> None:
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with store.transaction():
            store._conn.execute(
                "INSERT INTO tickets (id,title,task,created_at,status,mandate_id) "
                "VALUES ('tk-1','t','x',1,'open','md-1')"
            )
            store._conn.execute(
                "INSERT INTO effects (id,kind,idempotency_key,payload,state,attempts,due_at) "
                "VALUES ('ef-1','launch_assignment','k','{}','pending',0,1)"
            )
            raise Boom()
    assert store._conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0


def test_nested_transaction_is_rejected_and_flag_clears(store: Store) -> None:
    assert store.in_transaction is False
    with store.transaction():
        assert store.in_transaction is True
        with pytest.raises(ReentrantTransactionError):
            with store.transaction():
                pass
    assert store.in_transaction is False


def test_reopen_loses_nothing_and_unique_keys_hold(store: Store, tmp_path: Path) -> None:
    with store.transaction():
        store._conn.execute(
            "INSERT INTO tickets (id,title,task,created_at,status,mandate_id) "
            "VALUES ('tk-1','t','x',1,'open','md-1')"
        )
    store.close()
    reopened = Store(tmp_path / "orchestration.db", clock=FixedClock())
    try:
        assert reopened._conn.execute("SELECT id FROM tickets").fetchone()[0] == "tk-1"
        with pytest.raises(Exception, match="UNIQUE"):
            with reopened.transaction():
                reopened._conn.execute(
                    "INSERT INTO tickets (id,title,task,created_at,status,mandate_id) "
                    "VALUES ('tk-1','t','x',1,'open','md-2')"
                )
    finally:
        reopened.close()


def test_ddl_is_exposed_for_documentation() -> None:
    assert set(ORCHESTRATION_TABLES) == TABLES
```

- [ ] **Step 2: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_store.py -v`
Expected: FAIL —`ModuleNotFoundError: ... orchestration.store`

- [ ] **Step 3: 实现 Store 的 schema 与事务**

```python
# stableagent/orchestration/store.py（Task 4 部分；仓储方法在 Task 5 追加到同一文件）
"""编排层持久化。本模块只做持久化与约束，不做调度或策略判断（B7）。

事务模型：sqlite3 以 isolation_level=None 运行，事务由 transaction() 显式 BEGIN IMMEDIATE /
COMMIT / ROLLBACK 管理。事务体内禁止 await——驱动循环是单线程，同步段对协程不可分割，
因此"状态与 effect 同事务"是可证明性质而非约定（ADR-04 决策 3）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from stableagent.orchestration import ORCHESTRATION_SCHEMA_VERSION
from stableagent.orchestration.errors import ReentrantTransactionError
from stableagent.orchestration.model import Clock

_SCHEMA: dict[str, str] = {
    "schema_migrations": (
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    ),
    "mandates": (
        "CREATE TABLE IF NOT EXISTS mandates ("
        " id TEXT PRIMARY KEY, origin_kind TEXT NOT NULL, requester TEXT NOT NULL,"
        " scope TEXT NOT NULL DEFAULT '', policy_version TEXT NOT NULL, granted_at INTEGER NOT NULL)"
    ),
    "tickets": (
        "CREATE TABLE IF NOT EXISTS tickets ("
        " id TEXT PRIMARY KEY, title TEXT NOT NULL, task TEXT NOT NULL,"
        " created_at INTEGER NOT NULL, status TEXT NOT NULL, mandate_id TEXT NOT NULL,"
        " parent_id TEXT, ended_at INTEGER, queued_at INTEGER, woken_at INTEGER)"
    ),
    "assignments": (
        "CREATE TABLE IF NOT EXISTS assignments ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, attempt INTEGER NOT NULL,"
        " status TEXT NOT NULL, worker_run_id TEXT, started_at INTEGER, ended_at INTEGER,"
        " checkpoint_run_id TEXT, failure TEXT, worker_config_version TEXT,"
        " worker_config TEXT NOT NULL DEFAULT '{}',"
        " UNIQUE (ticket_id, attempt), UNIQUE (worker_run_id))"
    ),
    "inputs": (
        "CREATE TABLE IF NOT EXISTS inputs ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, sequence INTEGER NOT NULL,"
        " kind TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,"
        " delivered_at INTEGER, consumed_at INTEGER, UNIQUE (ticket_id, sequence))"
    ),
    "reports": (
        "CREATE TABLE IF NOT EXISTS reports ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, assignment_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, summary TEXT NOT NULL, created_at INTEGER NOT NULL,"
        " event_step INTEGER, details TEXT NOT NULL DEFAULT '{}')"
    ),
    "dependencies": (
        "CREATE TABLE IF NOT EXISTS dependencies ("
        " ticket_id TEXT NOT NULL, depends_on_ticket_id TEXT NOT NULL,"
        " on_failure TEXT NOT NULL DEFAULT 'block',"
        " PRIMARY KEY (ticket_id, depends_on_ticket_id))"
    ),
    "effects": (
        "CREATE TABLE IF NOT EXISTS effects ("
        " id TEXT PRIMARY KEY, kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,"
        " payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL,"
        " due_at INTEGER NOT NULL, last_error TEXT,"
        " UNIQUE (kind, idempotency_key))"
    ),
}
ORCHESTRATION_TABLES = tuple(_SCHEMA)


class Store:
    def __init__(self, db_path: str | Path, *, clock: Clock) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._in_tx = False
        self._create_schema()

    def _create_schema(self) -> None:
        with self.transaction():
            for ddl in _SCHEMA.values():
                self._conn.execute(ddl)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (ORCHESTRATION_SCHEMA_VERSION, self._clock.now_ms()),
            )

    @property
    def in_transaction(self) -> bool:
        return self._in_tx

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._in_tx:
            raise ReentrantTransactionError()
        self._in_tx = True
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._in_tx = False

    def schema_version(self) -> str:
        row = self._conn.execute(
            "SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else "0"

    def close(self) -> None:
        self._conn.close()
```

`json` 与 `Clock` 在 Task 5 的编解码里用到，本步先按上面的最小集合实现即可，mypy 会拒绝未使用导入——
因此本步不要 import `json`，留到 Task 5。

- [ ] **Step 4: 跑到绿**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_store.py && .venv/bin/mypy && .venv/bin/ruff check stableagent`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add stableagent/orchestration/store.py tests/unit/test_orchestration_store.py
git commit -m "feat: hold ticket state and effects in one sqlite transaction"
```

**关闭 DoD：** B1、B2、B3、L6 前半（重入守卫 + 事务内异常回滚），以及 B4、B6 的前半
（tickets 主键、重开无损）。B4/B6/L6 的整条勾选在 Task 5、6、9 之后。

---

### Task 5: Store 仓储方法与优先级排序

**Files:**
- Modify: `stableagent/orchestration/store.py`（追加编解码与 CRUD/查询）
- Test: `tests/unit/test_orchestration_store.py`（追加 round-trip、排序、分页）

**Interfaces:**
- Produces: `insert_ticket(Ticket) -> None`、`get_ticket(str) -> Ticket | None`、
  `update_ticket(Ticket) -> None`、`list_tickets(*, status=None, limit=50, offset=0) -> list[Ticket]`、
  `list_runnable_tickets(now_ms) -> list[Ticket]`（优先级 SQL 排序）、
  `insert_assignment(Assignment) -> None` / `get_assignment(str)` / `update_assignment(Assignment)` /
  `list_assignments(str)`、`insert_report(Report)` / `list_reports(str)`、
  `insert_input(TicketInput)` / `list_inputs(str, *, pending_only=False)`、
  `insert_dependency(Dependency)` / `list_dependencies(str)`、
  `enqueue_effect(Effect) -> Effect`（幂等，见 Task 6）、`due_effects(now_ms, *, limit=10)`、
  `update_effect(Effect) -> Effect`、`count_effects(str, EffectState) -> int`。
- Consumes: Task 2/3 模型与枚举。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/test_orchestration_store.py 追加
from stableagent.orchestration.model import (
    Assignment, AssignmentStatus, Dependency, Effect, EffectKind, EffectState,
    Mandate, Report, ReportKind, Ticket, TicketInput, TicketStatus,
)


def _ticket(ticket_id: str, **kw) -> Ticket:
    base = dict(id=ticket_id, title="t", task="x", created_at=1, mandate_id=f"md-{ticket_id}")
    return Ticket(**{**base, **kw})


def _mandate(ticket_id: str) -> Mandate:
    return Mandate(id=f"md-{ticket_id}", granted_at=1)


def test_ticket_and_assignment_round_trip_through_sqlite(store: Store) -> None:
    ticket = _ticket("tk-1", status=TicketStatus.QUEUED, queued_at=5)
    store.insert_ticket(ticket)
    assert store.get_ticket("tk-1") == ticket
    assignment = Assignment(id="as-1", ticket_id="tk-1", attempt=1,
                            status=AssignmentStatus.PENDING, worker_config={"model": "gpt-4o"})
    store.insert_assignment(assignment)
    assert store.get_assignment("as-1") == assignment
    assert store.list_assignments("tk-1") == [assignment]
    with pytest.raises(Exception, match="UNIQUE"):
        store.insert_assignment(assignment.model_copy(update={"id": "as-2"}))


def test_worker_run_id_is_unique_across_assignments(store: Store) -> None:
    store.insert_ticket(_ticket("tk-1"))
    store.insert_ticket(_ticket("tk-2"))
    store.insert_assignment(Assignment(id="as-1", ticket_id="tk-1", worker_run_id="r1"))
    with pytest.raises(Exception, match="UNIQUE"):
        store.insert_assignment(Assignment(id="as-2", ticket_id="tk-2", worker_run_id="r1"))


def test_reports_inputs_and_dependencies_round_trip(store: Store) -> None:
    store.insert_ticket(_ticket("tk-1"))
    report = Report(id="rp-1", ticket_id="tk-1", assignment_id="as-1",
                    kind=ReportKind.PROGRESS, summary="第 2 步", created_at=2,
                    event_step=2, details={"tool": "echo"})
    store.insert_report(report)
    assert store.list_reports("tk-1") == [report]
    ticket_input = TicketInput(id="in-1", ticket_id="tk-1", sequence=1, payload="追加线索", created_at=2)
    store.insert_input(ticket_input)
    assert store.list_inputs("tk-1", pending_only=True) == [ticket_input]


def test_runnable_order_is_woken_then_queued_then_created(store: Store) -> None:
    store.insert_ticket(_ticket("tk-old", status=TicketStatus.QUEUED, queued_at=100, created_at=1))
    store.insert_ticket(_ticket("tk-new", status=TicketStatus.QUEUED, queued_at=200, created_at=2))
    store.insert_ticket(_ticket("tk-woken", status=TicketStatus.QUEUED, queued_at=300,
                                created_at=3, woken_at=900))
    assert [t.id for t in store.list_runnable_tickets(1000)] == ["tk-woken", "tk-old", "tk-new"]


def test_pagination_is_stable(store: Store) -> None:
    for index in range(5):
        store.insert_ticket(_ticket(f"tk-{index}", status=TicketStatus.QUEUED, queued_at=index))
    page = store.list_tickets(status=TicketStatus.QUEUED, limit=2, offset=1)
    assert [t.id for t in page] == ["tk-1", "tk-2"]


def test_mandate_is_its_own_row_and_dependencies_round_trip(store: Store) -> None:
    store.insert_mandate(_mandate("tk-1"))
    assert store.get_mandate("md-tk-1") == _mandate("tk-1")
    store.insert_mandate(_mandate("tk-1"))  # INSERT OR IGNORE：重复写不报错也不改行
    store.insert_ticket(_ticket("tk-1"))
    store.insert_ticket(_ticket("tk-2"))
    dep = Dependency(ticket_id="tk-2", depends_on_ticket_id="tk-1", on_failure="continue")
    store.insert_dependency(dep)
    assert store.list_dependencies("tk-2") == [dep]
    assert store.list_dependencies("tk-1") == []
```

- [ ] **Step 2: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_store.py -k "round_trip or unique or runnable or pagination or dependencies"`
Expected: FAIL —`AttributeError: 'Store' object has no attribute 'insert_ticket'`

- [ ] **Step 3: 实现编解码与查询**

```python
# stableagent/orchestration/store.py 追加（模块顶部补 import json 与模型名）
def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"], title=row["title"], task=row["task"], created_at=row["created_at"],
        status=TicketStatus(row["status"]), mandate_id=row["mandate_id"],
        parent_id=row["parent_id"], ended_at=row["ended_at"],
        queued_at=row["queued_at"], woken_at=row["woken_at"],
    )
```

`Mandate` 独立成行（表在 Task 4 已建），`Ticket.mandate_id` 只是外键列——Task 2 的模型就是这一形状，
本步只补编解码。`Store` 不开 `PRAGMA foreign_keys`（引用完整性由 `OrchestrationService` 在同一事务里
先 `insert_mandate()` 再 `insert_ticket()` 保证，见 Task 9），因此 Store 层单测可以先写 ticket 再写 mandate。

```python
    # Store 方法（全部同步、全部假定已在调用方事务内或自带短事务）
    def insert_mandate(self, mandate: Mandate) -> None: ...          # INSERT OR IGNORE
    def get_mandate(self, mandate_id: str) -> Mandate | None: ...
    def insert_ticket(self, ticket: Ticket) -> None: ...
    def update_ticket(self, ticket: Ticket) -> None: ...              # 按 id 全行 UPDATE
    def get_ticket(self, ticket_id: str) -> Ticket | None: ...
    def list_tickets(self, *, status=None, limit=50, offset=0) -> list[Ticket]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM tickets {where} ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_row_to_ticket(row) for row in rows]

    def list_runnable_tickets(self, now_ms: int) -> list[Ticket]:
        """优先级：被唤醒过的 > 已排队 > 新建（ADR-04 决策 7；E7 的 SQL 落点）。"""
        rows = self._conn.execute(
            "SELECT * FROM tickets WHERE status = 'queued'"
            " ORDER BY (woken_at IS NULL) ASC, queued_at ASC, created_at ASC",
        ).fetchall()
        return [_row_to_ticket(row) for row in rows]

    def insert_assignment(self, assignment: Assignment) -> None: ...   # worker_config 走 json.dumps(sort_keys=True)
    def update_assignment(self, assignment: Assignment) -> None: ...
    def get_assignment(self, assignment_id: str) -> Assignment | None: ...
    def list_assignments(self, ticket_id: str) -> list[Assignment]: ...  # ORDER BY attempt ASC
    def insert_report(self, report: Report) -> None: ...
    def list_reports(self, ticket_id: str) -> list[Report]: ...          # ORDER BY created_at ASC, id ASC
    def insert_input(self, ticket_input: TicketInput) -> None: ...
    def list_inputs(self, ticket_id: str, *, pending_only: bool = False) -> list[TicketInput]: ...
    def insert_dependency(self, dependency: Dependency) -> None: ...
    def list_dependencies(self, ticket_id: str) -> list[Dependency]: ...
    def update_effect(self, effect: Effect) -> Effect: ...
    def due_effects(self, now_ms: int, *, limit: int = 10) -> list[Effect]:
        rows = self._conn.execute(
            "SELECT * FROM effects WHERE state IN ('pending','retry_wait') AND due_at <= ?"
            " ORDER BY due_at ASC, id ASC LIMIT ?",
            (now_ms, limit),
        ).fetchall()
        return [_row_to_effect(row) for row in rows]

    def count_effects(self, ticket_id: str, state: EffectState) -> int: ...
```

上面每个 `...` 的方法体是一致的形状：把 pydantic 模型的字段按列顺序展开，
`payload` / `details` / `worker_config` 用 `json.dumps(..., sort_keys=True, ensure_ascii=False)` 存 TEXT，
读取时 `json.loads`；写操作全部 `self._conn.execute(...)`，不开事务（由调用方决定边界）。
枚举列写 `.value`，读时构造枚举。**每个方法都必须实现，不留 `...`。**

- [ ] **Step 4: 跑到绿**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_store.py && .venv/bin/mypy && .venv/bin/ruff check stableagent`
Expected: PASS（含 `mandate_id` 改造后的全部用例）

- [ ] **Step 5: 提交**

```bash
git add stableagent/orchestration/ tests/unit/test_orchestration_store.py tests/unit/test_orchestration_model.py
git commit -m "feat: read and write the control plane with the agreed ticket ordering"
```

**关闭 DoD：** B4、B6（三处唯一键、重开/排序/分页）。B5 随 Task 6 的 `enqueue_effect()` docstring 落地。
E7 的 SQL 落点在 `list_runnable_tickets()` 的 ORDER BY 里，但 E7 属 M2（第 1.4 节），本任务不勾；
L5 的"P4 库不含 P2 表名"断言到 Task 10 才整条勾。

---

### Task 6: Durable Outbox——入队、领取、幂等与 reclaim

**Files:**
- Create: `stableagent/orchestration/outbox.py`
- Modify: `stableagent/orchestration/store.py`（`enqueue_effect` / `reclaim_running_effects`）
- Test: `tests/unit/test_outbox.py`

**Interfaces:**
- Produces: `Store.enqueue_effect(effect: Effect) -> Effect`（同 `(kind, idempotency_key)` 处于
  pending/retry_wait 时只更新 payload 与 due_at；已终态则原样返回既有行）；
  `Store.reclaim_running_effects(now_ms: int) -> int`；
  `EffectHandler = Callable[[Effect], Awaitable[None]]`；
  `EffectDispatcher(store, handlers: Mapping[EffectKind, EffectHandler], *, clock, max_attempts=3)`
  带 `async def dispatch_due(self, *, limit: int = 10) -> int` 与 `def outcomes(self) -> list[tuple[str, EffectState]]`。
- Consumes: Task 3 `next_effect_status`、Task 4/5 事务与 `due_effects`。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/test_outbox.py
"""C1/C3 + L6：提交后才执行、重复 dispatch 不新增副作用、断言不持有事务（C5 后半与 C4 归 M2）。"""

from __future__ import annotations

import pytest

from stableagent.orchestration.errors import ReentrantTransactionError
from stableagent.orchestration.model import Effect, EffectAction, EffectKind, EffectState
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.orchestration.store import Store
from tests.support import FixedClock


def _launch(effect_id: str, key: str, *, due: int = 0, state: EffectState = EffectState.PENDING):
    return Effect(
        id=effect_id, kind=EffectKind.LAUNCH_ASSIGNMENT, idempotency_key=key,
        payload={"assignment_id": "as-1"}, state=state, due_at=due,
    )


@pytest.fixture
def store(tmp_path):
    handle = Store(tmp_path / "outbox.db", clock=FixedClock())
    yield handle
    handle.close()


def test_enqueue_is_idempotent_on_kind_and_key(store: Store) -> None:
    with store.transaction():
        first = store.enqueue_effect(_launch("ef-1", "launch:as-1:1"))
        duplicate = store.enqueue_effect(_launch("ef-2", "launch:as-1:1", due=9))
    assert first.id == duplicate.id == "ef-1"          # 不新增行
    assert duplicate.payload == {"assignment_id": "as-1"}
    assert store._conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1


def test_duplicate_enqueue_on_terminal_effect_does_not_revive_it(store: Store) -> None:
    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    store.update_effect(
        Effect.model_validate(
            {**store._effect_row("ef-1").model_dump(), "state": EffectState.SUCCEEDED}
        )
    )
    with store.transaction():
        again = store.enqueue_effect(_launch("ef-2", "k1"))
    assert again.state is EffectState.SUCCEEDED and again.id == "ef-1"


async def test_handler_never_sees_an_open_transaction(store: Store) -> None:
    seen: list[bool] = []

    async def handler(effect: Effect) -> None:
        seen.append(store.in_transaction)

    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    dispatcher = EffectDispatcher(store, {EffectKind.LAUNCH_ASSIGNMENT: handler}, clock=FixedClock())
    assert await dispatcher.dispatch_due() == 1
    assert seen == [False]
    assert dispatcher.outcomes() == [("ef-1", EffectState.SUCCEEDED)]


async def test_handler_exception_retries_then_fails_without_memory_flags(store: Store) -> None:
    clock = FixedClock()

    async def boom(effect: Effect) -> None:
        raise RuntimeError("provider down")

    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    dispatcher = EffectDispatcher(store, {EffectKind.LAUNCH_ASSIGNMENT: boom}, clock=clock, max_attempts=2)
    assert await dispatcher.dispatch_due() == 0
    state = store._effect_row("ef-1")
    assert state.state is EffectState.RETRY_WAIT and state.attempts == 1
    clock.advance(10_000)                                # 越过 backoff 的 due_at
    assert await dispatcher.dispatch_due() == 0
    assert store._effect_row("ef-1").state is EffectState.FAILED


async def test_orphan_running_effects_are_reclaimed(store: Store) -> None:
    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1", state=EffectState.RUNNING))
    assert store.reclaim_running_effects(5) == 1
    assert store._effect_row("ef-1").state is EffectState.PENDING


def test_transaction_wrapper_still_rejects_nesting(store: Store) -> None:
    with pytest.raises(ReentrantTransactionError):
        with store.transaction():
            with store.transaction():
                pass
```

- [ ] **Step 2: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_outbox.py -v`
Expected: FAIL —`ModuleNotFoundError: ... orchestration.outbox`

- [ ] **Step 3: 实现入队与派发**

```python
# stableagent/orchestration/store.py 追加
    def enqueue_effect(self, effect: Effect) -> Effect:
        """同 (kind, idempotency_key) 的 pending/retry_wait 行只更新 payload/due_at；
        已进终态的行原样返回，绝不复活——幂等靠唯一键，不靠内存标志（C3）。"""
        existing = self._effect_by_key(effect.kind, effect.idempotency_key)
        if existing is not None and existing.state in (
            EffectState.SUCCEEDED,
            EffectState.FAILED,
            EffectState.CANCELLED,
        ):
            return existing
        if existing is not None:
            self._conn.execute(
                "UPDATE effects SET payload = ?, due_at = ? WHERE id = ?",
                (
                    json.dumps(effect.payload, sort_keys=True, ensure_ascii=False),
                    effect.due_at,
                    existing.id,
                ),
            )
            return self._effect_row(existing.id)
        self._conn.execute(
            "INSERT INTO effects (id,kind,idempotency_key,payload,state,attempts,due_at,last_error)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                effect.id,
                effect.kind.value,
                effect.idempotency_key,
                json.dumps(effect.payload, sort_keys=True, ensure_ascii=False),
                effect.state.value,
                effect.attempts,
                effect.due_at,
                effect.last_error,
            ),
        )
        return effect

    def reclaim_running_effects(self, now_ms: int) -> int:
        """崩溃时正在跑的 effect 退回 pending：幂等键保证不会重复副作用（C5）。"""
        cursor = self._conn.execute(
            "UPDATE effects SET state = 'pending', due_at = ? WHERE state = 'running'", (now_ms,)
        )
        return cursor.rowcount
```

```python
# stableagent/orchestration/outbox.py
"""Durable outbox：领域状态与待执行副作用同事务落库，提交之后才做 I/O。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from stableagent.orchestration.model import Clock, Effect, EffectAction, EffectKind, next_effect_status
from stableagent.orchestration.store import Store

EffectHandler = Callable[[Effect], Awaitable[None]]


class EffectDispatcher:
    def __init__(
        self,
        store: Store,
        handlers: Mapping[EffectKind, EffectHandler],
        *,
        clock: Clock,
        max_attempts: int = 3,
        backoff_ms: int = 1000,
    ) -> None:
        self._store = store
        self._handlers = dict(handlers)
        self._clock = clock
        self._max_attempts = max_attempts
        self._backoff_ms = backoff_ms
        self._outcomes: list[tuple[str, EffectState]] = []

    def outcomes(self) -> list[tuple[str, EffectState]]:
        return list(self._outcomes)

    async def dispatch_due(self, *, limit: int = 10) -> int:
        """返回成功执行的 effect 数。I/O 必须在事务之外（L6）。"""
        assert not self._store.in_transaction, "handler 的 I/O 不得持有事务"
        handled = 0
        for effect in self._store.due_effects(self._clock.now_ms(), limit=limit):
            handler = self._handlers.get(effect.kind)
            if handler is None:
                continue
            claimed = self._claim(effect)
            try:
                await handler(claimed)
            except Exception as exc:  # 副作用失败：退避重试，达上限才失败
                self._fail_or_retry(claimed, repr(exc))
                continue
            self._settle(claimed, EffectAction.SUCCEED)
            handled += 1
        return handled

    def _claim(self, effect: Effect) -> Effect:
        with self._store.transaction():
            claimed = self._store.update_effect(
                effect.model_copy(
                    update={
                        "state": next_effect_status(effect.state, EffectAction.CLAIM),
                        "attempts": effect.attempts + 1,
                    }
                )
            )
        return claimed

    def _settle(self, effect: Effect, action: EffectAction) -> Effect:
        with self._store.transaction():
            return self._store.update_effect(
                effect.model_copy(update={"state": next_effect_status(effect.state, action)})
            )

    def _fail_or_retry(self, effect: Effect, detail: str) -> Effect:
        action = (
            EffectAction.RETRY if effect.attempts < self._max_attempts else EffectAction.FAIL
        )
        state = next_effect_status(effect.state, action)
        if state is EffectState.SUCCEEDED:  # pragma: no cover - 转换表保证不可达
            raise AssertionError("retry 不能得到 succeeded")
        due_at = (
            self._clock.now_ms() + self._backoff_ms * effect.attempts
            if state is EffectState.RETRY_WAIT
            else effect.due_at
        )
        with self._store.transaction():
            updated = self._store.update_effect(
                effect.model_copy(update={"state": state, "last_error": detail, "due_at": due_at})
            )
        self._outcomes.append((updated.id, updated.state))
        return updated
```

`_settle` 与 `_claim` 也要把终态记进 `self._outcomes`（实现时在 `_settle` 里 append
`(effect.id, EffectState.SUCCEEDED)`），测试 `test_handler_never_sees_an_open_transaction` 依赖它。
`Store._effect_row(effect_id)` / `_effect_by_key(kind, key)` 是本任务需要的两个私有读取方法，
返回 `Effect` 模型；`update_effect` 按 id 全行 UPDATE 并返回入参模型。

- [ ] **Step 4: 跑到绿**

Run: `.venv/bin/pytest -q tests/unit/test_outbox.py && .venv/bin/mypy && .venv/bin/ruff check stableagent`
Expected: PASS（6 条全绿，包括"两次失败即 FAILED"）

- [ ] **Step 5: 提交**

```bash
git add stableagent/orchestration/outbox.py stableagent/orchestration/store.py tests/unit/test_outbox.py
git commit -m "feat: make side effects durable through the outbox"
```

**关闭 DoD：** B5、C1、C3、L6 中段（提交前不执行 + handler 无事务）。C4/C5/C7 属 M2；
reclaim 只做"running 且过期的 effect 放回 pending"这一半，C5 的完整重启语义等 M2.10。
C2（Assignment 与 launch effect 同事务）要等 Task 9 的 `queue_ticket()` 才成立。

---

### Task 7: WorkerExecutor 协议与 Event→Report 映射

**Files:**
- Create: `stableagent/orchestration/executor.py`
- Modify: `tests/support.py`（追加 `RecordingSink`、`StubAgent`）
- Test: `tests/unit/test_agent_executor.py`

**Interfaces:**
- Consumes: `Agent.stream(task)`、`Agent.stream_recover(run_id)`、`agent.last_state`、
  `Event.data["run_id"]`（CHECKPOINT）、`Store.update_assignment`。
- Produces: `AssignmentSink` 协议（`add_report`、`finish_assignment`、`transition_ticket`、
  `register_run_id`）；`WorkerExecutor` 协议（`launch` / `deliver` / `cancel` / `recover`，
  M1 里后两者抛 `NotImplementedError`，M2 落地）；
  `AgentExecutor(store=..., sink=..., agent_factory=..., clock=..., *, emit_progress_on=frozenset({EventType.TOOL_RESULT}))`
  实现 `async def launch(self, assignment, ticket, inputs)` 与 `async def recover(self, assignment)`。

- [ ] **Step 1: 写测试资产与失败的测试**

```python
# tests/support.py 追加
class RecordingSink:
    """AssignmentSink 的测试替身：记录编排层写意图，不碰数据库。"""

    def __init__(self) -> None:
        self.reports: list[tuple[str, str, str, int | None]] = []
        self.finished: list[tuple[str, str, str | None]] = []
        self.ticket_actions: list[tuple[str, str]] = []
        self.run_ids: list[tuple[str, str]] = []

    def add_report(self, *, ticket_id, assignment_id, kind, summary, event_step=None, details=None):
        self.reports.append((ticket_id, assignment_id, kind.value, event_step))

    def register_run_id(self, assignment_id: str, worker_run_id: str) -> None:
        self.run_ids.append((assignment_id, worker_run_id))

    def finish_assignment(self, assignment_id: str, action, *, failure=None) -> None:
        self.finished.append((assignment_id, action.value, failure))

    def transition_ticket(self, ticket_id: str, action) -> None:
        self.ticket_actions.append((ticket_id, action.value))


class StubAgent:
    """只暴露 AgentExecutor 用到的面：stream / stream_recover / last_state。"""

    def __init__(self, events, *, run_id="run-1", config=None) -> None:
        from stableagent.core.agent import Config  # 局部导入避免测试耦合

        self._events = list(events)
        self.last_state = None
        self.config = config or Config()
        self.recovered_with: list[str | None] = []
        self._run_id = run_id

    async def stream(self, task: str):
        for event in self._events:
            yield event

    async def stream_recover(self, run_id: str | None = None):
        self.recovered_with.append(run_id)
        for event in self._events:
            yield event
```

```python
# tests/unit/test_agent_executor.py
"""D1/D2/D3/D5/D7 + 4.1 补充：run_id 只在第一个 step 边界注册；映射只读结构化字段（D4 全量归 M2）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stableagent.core.state import AgentState, AgentStatus, Config, Event, EventType
from stableagent.orchestration.errors import WorkerConfigError
from stableagent.orchestration.executor import AgentExecutor
from stableagent.orchestration.model import (
    Assignment, AssignmentAction, Mandate, ReportKind, Ticket, TicketAction,
)
from tests.support import RecordingSink, StubAgent, FixedClock


def _ticket() -> Ticket:
    return Ticket(id="tk-1", title="t", task="算一遍", created_at=1,
                  mandate_id="md-1", status="queued")


def _assignment() -> Assignment:
    return Assignment(id="as-1", ticket_id="tk-1", attempt=1, status="running")


def _checkpointed_config() -> Config:
    return Config(checkpoint={"backend": "sqlite", "auto_save_every": 1,
                              "recovery_strategy": "resume", "sqlite_path": ":memory:"})


def _executor(events, sink, *, config=None):
    agent = StubAgent(events, config=config or _checkpointed_config())
    executor = AgentExecutor(
        sink=sink, agent_factory=lambda ticket, assignment: agent, clock=FixedClock()
    )
    return executor, agent


async def test_checkpoint_required_worker_is_refused_before_any_io() -> None:
    sink = RecordingSink()
    executor, agent = _executor([], sink, config=Config())  # 默认 backend=memory
    with pytest.raises(WorkerConfigError):
        await executor.launch(_assignment(), _ticket(), [])
    assert agent._events == [] and sink.reports == []       # 没有启动，也没有报告


async def test_run_id_is_registered_once_at_the_first_step_boundary() -> None:
    sink = RecordingSink()
    events = [
        Event(type=EventType.THINKING, data={"text": "…"}, step=1),
        Event(type=EventType.CHECKPOINT, data={"run_id": "r-9", "step": 1,
              "contract_version": "1.1.0", "saved_at": 0}, step=1),
        Event(type=EventType.TOOL_RESULT, data={"tool": "echo"}, step=1),
        Event(type=EventType.DONE, data={"status": "succeeded"}, step=2),
    ]
    executor, _ = _executor(events, sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.run_ids == [("as-1", "r-9")]               # 只注册一次
    assert sink.reports == [("tk-1", "as-1", ReportKind.PROGRESS.value, 1)]
    assert sink.finished == [("as-1", AssignmentAction.END.value, None)]
    assert sink.ticket_actions == [("tk-1", TicketAction.DELIVER.value)]


async def test_paused_needs_human_maps_to_waiting_and_budget_to_blocked() -> None:
    sink = RecordingSink()
    paused = Event(type=EventType.PAUSED, data={"step": 2, "reason": "needs_human", "detail": None})
    executor, _ = _executor([paused], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.ticket_actions == [("tk-1", TicketAction.WAIT.value)]

    sink2 = RecordingSink()
    budget = Event(type=EventType.PAUSED, data={"step": 3, "reason": "budget_exceeded"})
    executor2, _ = _executor([budget], sink2)
    await executor2.launch(_assignment(), _ticket(), [])
    assert sink2.ticket_actions == [("tk-1", TicketAction.BLOCK.value)]


async def test_error_event_maps_to_failed_assignment_with_reason() -> None:
    sink = RecordingSink()
    error = Event(type=EventType.ERROR, data={"reason": "provider_unavailable", "detail": "503"})
    executor, _ = _executor([error], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.finished[0][1] == AssignmentAction.FAIL.value
    assert sink.finished[0][2] == "provider_unavailable"
    assert sink.ticket_actions == [("tk-1", TicketAction.FAIL.value)]


async def test_recover_uses_stream_recover_and_never_replays_events() -> None:
    sink = RecordingSink()
    events = [Event(type=EventType.DONE, data={"status": "succeeded"}, step=4)]
    executor, agent = _executor(events, sink)
    assignment = _assignment().model_copy(update={"worker_run_id": "r-7",
                                                  "status": AssignmentStatus.INTERRUPTED})
    await executor.recover(assignment, _ticket())
    assert agent.recovered_with == ["r-7"]
    assert sink.finished == [("as-1", AssignmentAction.END.value, None)]


async def test_thinking_and_tool_call_do_not_emit_reports_by_default() -> None:
    sink = RecordingSink()
    events = [
        Event(type=EventType.THINKING, data={"text": "x"}, step=1),
        Event(type=EventType.TOOL_CALL, data={"name": "echo"}, step=1),
        Event(type=EventType.DONE, data={"status": "succeeded"}, step=2),
    ]
    executor, _ = _executor(events, sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.reports == []                               # D3：默认零噪音


async def test_protocol_methods_not_in_scope_raise_clearly() -> None:
    sink = RecordingSink()
    executor, _ = _executor([], sink)
    with pytest.raises(NotImplementedError):
        await executor.deliver(_assignment(), [_ticket()], [])
    with pytest.raises(NotImplementedError):
        await executor.cancel(_assignment())
```

（测试里 `AssignmentStatus` 需要从 `orchestration.model` 一并导入；`ValidationError` 断言在
Task 8 的 scheduler 用例里用到时可删除本行的未使用导入。）

- [ ] **Step 2: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_agent_executor.py -v`
Expected: FAIL —`ModuleNotFoundError: ... orchestration.executor`

- [ ] **Step 3: 实现协议与适配器**

```python
# stableagent/orchestration/executor.py
"""Worker 侧适配：把一次 Agent run 的事件映射成长期 Report。

只认 Core 的公开入口（stream / stream_recover / last_state）；run_id 在第一个 step 边界注册，
因为 Core 在 _drive() 内部才生成它（ADR-04 决策 4.1）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Protocol, Sequence
from typing import Any

from stableagent.core.agent import Agent
from stableagent.core.state import Event, EventType
from stableagent.orchestration.errors import WorkerConfigError
from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    Clock,
    ReportKind,
    Ticket,
    TicketAction,
    TicketInput,
)

AgentFactory = Callable[[Ticket, Assignment], Agent]

_REPORT_BY_EVENT: dict[EventType, ReportKind] = {EventType.TOOL_RESULT: ReportKind.PROGRESS}
_TERMINAL_ACTION = {
    EventType.DONE: (AssignmentAction.END, TicketAction.DELIVER),
    EventType.ERROR: (AssignmentAction.FAIL, TicketAction.FAIL),
}


class AssignmentSink(Protocol):
    """编排层唯一写入口的窄面：executor 不认识 OrchestrationService，只认识这个协议。"""

    def add_report(
        self,
        *,
        ticket_id: str,
        assignment_id: str,
        kind: ReportKind,
        summary: str,
        event_step: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...

    def register_run_id(self, assignment_id: str, worker_run_id: str) -> None: ...

    def finish_assignment(
        self, assignment_id: str, action: AssignmentAction, *, failure: str | None = None
    ) -> None: ...

    def transition_ticket(self, ticket_id: str, action: TicketAction) -> None: ...


class WorkerExecutor(Protocol):
    async def launch(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None: ...

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None: ...

    async def cancel(self, assignment: Assignment) -> None: ...

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None: ...


class AgentExecutor:
    def __init__(
        self,
        *,
        sink: AssignmentSink,
        agent_factory: AgentFactory,
        clock: Clock,
        emit_progress_on: frozenset[EventType] = frozenset({EventType.TOOL_RESULT}),
    ) -> None:
        self._sink = sink
        self._agent_factory = agent_factory
        self._clock = clock
        self._emit_progress_on = frozenset(emit_progress_on)

    @staticmethod
    def _require_recoverable_worker(agent: Agent) -> None:
        checkpoint = agent.config.checkpoint
        if checkpoint.backend == "memory" or checkpoint.auto_save_every < 1:
            raise WorkerConfigError(
                "Worker 必须启用 step 边界 checkpoint（backend != memory 且 auto_save_every >= 1），"
                "否则 Assignment 没有可恢复的快照"
            )

    async def launch(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        agent = self._agent_factory(ticket, assignment)
        self._require_recoverable_worker(agent)
        await self._drive(agent, ticket, assignment, agent.stream(_task_of(ticket, inputs)))

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None:
        if assignment.worker_run_id is None:
            raise WorkerConfigError("没有 worker_run_id 的 Assignment 不能走恢复，应重新 launch")
        agent = self._agent_factory(ticket, assignment)
        self._require_recoverable_worker(agent)
        await self._drive(
            agent, ticket, assignment, agent.stream_recover(assignment.worker_run_id)
        )

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        raise NotImplementedError("deliver_input 属于里程碑 M2（依赖 extra_input Core 扩展）")

    async def cancel(self, assignment: Assignment) -> None:
        raise NotImplementedError("cancel_assignment 属于里程碑 M2")

    async def _drive(
        self, agent: Agent, ticket: Ticket, assignment: Assignment, events: Iterable[Event]
    ) -> None:
        run_id_registered = assignment.worker_run_id is not None
        async for event in events:
            if not run_id_registered:
                run_id = _run_id_of(agent, event)
                if run_id is not None:
                    self._sink.register_run_id(assignment.id, run_id)
                    run_id_registered = True
            self._report_event(ticket, assignment, event)
            if event.type in _TERMINAL_ACTION:
                assignment_action, ticket_action = _TERMINAL_ACTION[event.type]
                failure = None
                if event.type is EventType.ERROR:
                    failure = str(event.data.get("reason") or event.data.get("detail") or "worker_error")
                self._sink.finish_assignment(assignment.id, assignment_action, failure=failure)
                self._sink.transition_ticket(ticket.id, ticket_action)
                return
            if event.type is EventType.PAUSED:
                reason = str(event.data.get("reason", "needs_human"))
                self._sink.finish_assignment(assignment.id, AssignmentAction.END)
                self._sink.transition_ticket(
                    ticket.id,
                    TicketAction.WAIT if reason == "needs_human" else TicketAction.BLOCK,
                )
                return


def _task_of(ticket: Ticket, inputs: Sequence[TicketInput]) -> str:
    if not inputs:
        return ticket.task
    return "\n\n".join([ticket.task, *(item.payload for item in inputs)])


def _run_id_of(agent: Agent, event: Event) -> str | None:
    """CHECKPOINT 事件带 run_id；否则退回 agent.last_state（两者都只在 step 边界出现）。"""
    from_row = event.data.get("run_id") if event.type is EventType.CHECKPOINT else None
    if isinstance(from_row, str) and from_row:
        return from_row
    state = getattr(agent, "last_state", None)
    return state.run_id if state is not None else None
```

`_report_event` 的实现（同一文件，写全）：

```python
    def _report_event(self, ticket: Ticket, assignment: Assignment, event: Event) -> None:
        kind = _REPORT_BY_EVENT.get(event.type)
        if kind is None or event.type not in self._emit_progress_on:
            return
        summary = f"{event.type.value} @ step {event.step}"
        self._sink.add_report(
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=kind,
            summary=summary,
            event_step=event.step,
            details=dict(event.data),
        )
```

注意：PAUSED 也 `finish_assignment(END)`——Assignment 结束但 Ticket 进 `waiting`/`blocked`，
恢复由 `retry_ticket`/`submit_input`（M2）创建新 Assignment。这条语义要在 docstring 里写明。

- [ ] **Step 4: 跑到绿**

Run: `.venv/bin/pytest -q tests/unit/test_agent_executor.py && .venv/bin/mypy && .venv/bin/ruff check stableagent`
Expected: PASS（7 条全绿）

- [ ] **Step 5: 提交**

```bash
git add stableagent/orchestration/executor.py tests/support.py tests/unit/test_agent_executor.py
git commit -m "feat: map worker events into durable reports without touching the loop"
```

**关闭 DoD：** A4 收尾、D1、D2、D3、D5、D7。D4 与 D6 属 M2（见第 1.4 节），本任务只把
"映射只读结构化字段"的形状做出来，不勾。

---

### Task 8: Scheduler——单槽、依赖 Store 推导与单 run 单驱动

**Files:**
- Create: `stableagent/orchestration/scheduler.py`
- Test: `tests/unit/test_scheduler.py`（`StubExecutor` 留在测试文件内，只有这一个消费者）

**Interfaces:**
- Consumes: Task 6 `EffectDispatcher`、Task 7 `WorkerExecutor`、Task 5 `list_runnable_tickets`、
  `Store.due_effects/enqueue_effect`。
- Produces: `TickResult(enqueued: int, dispatched: int, running: int)`；
  `Scheduler(*, store, service, executor, clock, max_concurrency=1)`
  带 `async def tick(self) -> TickResult`、`async def drain(self, *, max_rounds: int = 50) -> list[TickResult]`、
  `async def drive(self, assignment, ticket) -> None`、`def running_assignments(self) -> set[str]`。
  注意 `drive()` 的第一参数是 Assignment 不是 run_id：Worker 的 `worker_run_id` 在启动前还不存在
  （ADR-04 决策 4.1），去重键只能是 `assignment.id`。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/test_scheduler.py
"""E1 + L7：可运行性只从 Store 推导；单 run 单驱动；M1 固定单槽（E2 的宽度归 M2.8）。"""

from __future__ import annotations

import asyncio

import pytest

from stableagent.orchestration.model import (
    Effect, EffectKind, EffectState, Mandate, Ticket, TicketStatus,
)
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds


class StubExecutor:
    def __init__(self) -> None:
        self.launched: list[str] = []
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)
        self.started.set()
        await self.gate.wait()

    async def deliver(self, assignment, ticket, inputs) -> None: ...
    async def cancel(self, assignment) -> None: ...
    async def recover(self, assignment, ticket) -> None: ...


@pytest.fixture
def env(tmp_path):
    clock, ids = FixedClock(), SequenceIds()
    store = Store(tmp_path / "sched.db", clock=clock)
    service = OrchestrationService(store, clock=clock, ids=ids)
    executor = StubExecutor()
    scheduler = Scheduler(
        store=store, service=service, executor=executor, clock=clock, max_concurrency=1
    )
    yield store, service, executor, scheduler, clock
    store.close()


def _queued(service, task: str) -> Ticket:
    return service.queue_ticket(service.create_ticket(task).id)


async def _wait_until_idle(scheduler: Scheduler, *, rounds: int = 20) -> None:
    """只用公开 API 等 Worker 结束：每轮让出一次事件循环，槽位释放当轮即可见。"""
    for _ in range(rounds):
        if not scheduler.running_assignments():
            return
        await asyncio.sleep(0)
    raise AssertionError("Worker 未在预期轮次内结束")


async def test_tick_starts_exactly_one_worker_per_concurrency_slot(env) -> None:
    store, service, executor, scheduler, _ = env
    for index in range(3):
        _queued(service, f"任务 {index}")
    result = await scheduler.tick()
    assert result.enqueued == 3 and result.dispatched == 1
    assert len(executor.launched) == 1 and scheduler.running_assignments()  # 槽位被占
    executor.gate.set()
    await _wait_until_idle(scheduler)  # 第一个 Worker 结束，槽位释放
    second = await scheduler.tick()
    await _wait_until_idle(scheduler)  # 新任务只是 create_task，让出循环后 launch 才记录
    assert second.dispatched == 1 and len(executor.launched) == 2


async def test_second_launch_of_the_same_assignment_is_refused(env) -> None:
    store, service, executor, scheduler, _ = env
    ticket = _queued(service, "只有一个")
    await scheduler.tick()
    assignment = store.list_assignments(ticket.id)[0]

    from stableagent.orchestration.errors import DuplicateLaunchError

    with pytest.raises(DuplicateLaunchError):
        await scheduler.drive(assignment, ticket)


async def test_nothing_runnable_means_no_effect_and_no_io(env) -> None:
    _, _, executor, scheduler, _ = env
    result = await scheduler.tick()
    assert result.enqueued == 0 and result.dispatched == 0 and executor.launched == []


async def test_queue_survives_reopen_without_memory_state(env) -> None:
    store, service, executor, scheduler, clock = env
    _queued(service, "重启也要跑")
    store.close()
    reopened = Store(str(env[0]._conn.execute("PRAGMA database_list").fetchone()[2]), clock=clock)
    try:
        assert [t.id for t in reopened.list_runnable_tickets(clock.now_ms())]  # 事实源在库里
    finally:
        reopened.close()
```

- [ ] **Step 2: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_scheduler.py -v`
Expected: FAIL —`ModuleNotFoundError: ... orchestration.service`（Task 9 前 service 不存在）

- [ ] **Step 3: 实现 Scheduler**

`service` 参数标注为 Task 9 的 `OrchestrationService`；本步先写实现，Step 4 才变绿（Task 8 与
Task 9 互为红绿，见本任务末尾）。

```python
# stableagent/orchestration/scheduler.py
"""从 Store 推导可运行工作，经 outbox 领取 effect 驱动 Worker；内存里没有不可恢复的队列。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from stableagent.orchestration.errors import DuplicateLaunchError, UnknownAssignmentError
from stableagent.orchestration.executor import WorkerExecutor
from stableagent.orchestration.model import Assignment, Clock, Effect, EffectKind, Ticket
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store


@dataclass(frozen=True)
class TickResult:
    enqueued: int
    dispatched: int
    running: int


class Scheduler:
    def __init__(
        self,
        *,
        store: Store,
        service: OrchestrationService,
        executor: WorkerExecutor,
        clock: Clock,
        max_concurrency: int = 1,
    ) -> None:
        self._store = store
        self._service = service
        self._executor = executor
        self._clock = clock
        self._max_concurrency = max_concurrency
        self._slots = asyncio.Semaphore(max_concurrency)
        # 键是 assignment.id：worker_run_id 在 Worker 启动前还不存在（ADR-04 决策 4.1）。
        # 本字典只作取消与去重句柄，可运行性的事实源永远是 Store。
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._dispatcher = EffectDispatcher(
            store, {EffectKind.LAUNCH_ASSIGNMENT: self._handle_launch}, clock=clock
        )

    def running_assignments(self) -> set[str]:
        return {key for key, task in self._tasks.items() if not task.done()}

    async def tick(self) -> TickResult:
        enqueued = self._service.enqueue_work_for_runnable_tickets(self._clock.now_ms())
        dispatched = await self._dispatcher.dispatch_due(limit=self._max_concurrency)
        return TickResult(
            enqueued=enqueued,
            dispatched=dispatched,
            running=len(self.running_assignments()),
        )

    async def drive(self, assignment: Assignment, ticket: Ticket) -> None:
        live = self.running_assignments()
        if assignment.id in live:
            raise DuplicateLaunchError(assignment.worker_run_id or assignment.id)
        self._tasks[assignment.id] = asyncio.create_task(self._guarded(assignment, ticket))

    async def _guarded(self, assignment: Assignment, ticket: Ticket) -> None:
        async with self._slots:  # 槽位由 Worker 任务自己持有，直到终态或异常
            await self._executor.launch(assignment, ticket, [])

    async def _handle_launch(self, effect: Effect) -> None:
        payload = effect.payload
        assignment = self._store.get_assignment(payload["assignment_id"])
        ticket = self._store.get_ticket(payload["ticket_id"])
        if assignment is None or ticket is None:
            raise UnknownAssignmentError(payload["assignment_id"])
        await self.drive(assignment, ticket)

    async def drain(self, *, max_rounds: int = 50) -> list[TickResult]:
        results: list[TickResult] = []
        for _ in range(max_rounds):
            result = await self.tick()
            results.append(result)
            if result.enqueued == 0 and result.dispatched == 0 and not self.running_assignments():
                break
            await asyncio.sleep(0)
        return results
```

三条要在实现时守住的性质，第 4/5 步各有一条测试锁住：

1. `_slots` 在 `_guarded()` 里 acquire，所以槽位跨越整个 Worker 运行，不是在 handler 返回时释放——
   这是 M2.8"槽位从领取起占到终态"的形状前提。
2. handler 里没有任何 `await store...` 事务：领取与标态由 `EffectDispatcher` 负责，
   `Scheduler` 不持有事务（L6）。
3. `drive()` 用 `assignment.id` 去重；`DuplicateLaunchError` 的参数取
   `worker_run_id or assignment.id`——M1 里 run_id 可能仍为 `None`，这不是错误。
   除 ADR-04 决策 4.1 记录的注册路径外，本任务不引入任何"占位 run_id"概念。

- [ ] **Step 4: 跑到绿**

Run: `.venv/bin/pytest -q tests/unit/test_scheduler.py tests/unit/test_outbox.py`
Expected: 本任务结束时 `test_scheduler.py` 里 4 条用例全绿（依赖 Task 9 的 service；
若 service 尚未落地，先做 Task 9 再回来跑，两个任务互为红绿）

- [ ] **Step 5: 提交**

```bash
git add stableagent/orchestration/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat: drive workers from stored queue with one slot per assignment"
```

**关闭 DoD：** E1、L7。`max_concurrency > 1` 的宽度（E2）与"槽位从领取起占到终态"的完整断言
留到 M2.8，本任务不勾。

---

### Task 9: OrchestrationService——唯一写入口与 TicketView

**Files:**
- Create: `stableagent/orchestration/service.py`
- Test: `tests/unit/test_orchestration_service.py`

**Interfaces:**
- Consumes: Task 2~8 全部。
- Produces: `OrchestrationService(store, *, clock, ids)`；
  `create_ticket(task, *, title=None, parent_id=None) -> Ticket`、
  `queue_ticket(ticket_id) -> Ticket`、`complete_ticket(ticket_id, summary=None) -> Ticket`、
  `get_ticket(ticket_id) -> TicketView`、`list_tickets(*, status=None, limit=50, offset=0) -> list[TicketView]`、
  `enqueue_work_for_runnable_tickets(now_ms) -> int`、`recover() -> RecoverySummary`，
  以及 `AssignmentSink` 的四个方法实现。`TicketView(ticket, assignments, reports, inputs)`、
  `RecoverySummary(interrupted_assignments, reclaimed_effects, relaunched)`。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/test_orchestration_service.py
"""A5 + L6：service 是唯一写入口；view 只由记录派生；queue_ticket 同事务落 Assignment + effect（G4 全量归 M2）。"""

from __future__ import annotations

import pytest

from stableagent.orchestration.errors import IllegalTransitionError, UnknownTicketError
from stableagent.orchestration.model import (
    AssignmentStatus, EffectKind, EffectState, ReportKind, TicketStatus,
)
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds


@pytest.fixture
def service(tmp_path):
    clock = FixedClock()
    store = Store(tmp_path / "service.db", clock=clock)
    yield OrchestrationService(store, clock=clock, ids=SequenceIds()), store, clock
    store.close()


def test_create_then_queue_writes_assignment_and_effect_atomically(service) -> None:
    svc, store, _ = service
    ticket = svc.create_ticket("写一篇收口报告")
    assert ticket.status is TicketStatus.OPEN
    queued = svc.queue_ticket(ticket.id)
    assert queued.status is TicketStatus.QUEUED and queued.queued_at is not None
    assignments = store.list_assignments(ticket.id)
    assert [a.attempt for a in assignments] == [1]
    effects = store.due_effects(10_000)
    assert [e.kind for e in effects] == [EffectKind.LAUNCH_ASSIGNMENT]
    assert effects[0].idempotency_key == f"launch:{assignments[0].id}:1"


def test_view_is_derived_only_from_records(service) -> None:
    svc, store, _ = service
    ticket = svc.create_ticket("任务")
    view = svc.get_ticket(ticket.id)
    assert view.ticket.id == ticket.id and view.assignments == [] and view.reports == []
    assert svc.list_tickets()[0].ticket.id == ticket.id


def test_unknown_ticket_and_illegal_transition_are_stable_errors(service) -> None:
    svc, _, _ = service
    with pytest.raises(UnknownTicketError):
        svc.queue_ticket("tk-missing")
    ticket = svc.create_ticket("任务")
    svc.queue_ticket(ticket.id)
    with pytest.raises(IllegalTransitionError):
        svc.queue_ticket(ticket.id)          # queued -> queue 非法
    svc.complete_ready(ticket)               # 测试辅助见 Step 3 说明
    with pytest.raises(IllegalTransitionError):
        svc.complete_ticket(ticket.id)


def test_sink_writes_reports_and_finishes_through_the_service(service) -> None:
    svc, store, _ = service
    ticket = svc.create_ticket("任务")
    svc.queue_ticket(ticket.id)
    assignment = store.list_assignments(ticket.id)[0]
    svc.register_run_id(assignment.id, "r-1")
    assert store.get_assignment(assignment.id).worker_run_id == "r-1"
    svc.add_report(
        ticket_id=ticket.id, assignment_id=assignment.id, kind=ReportKind.PROGRESS,
        summary="step 1", event_step=1, details={"tool": "echo"},
    )
    assert [r.kind for r in store.list_reports(ticket.id)] == [ReportKind.PROGRESS]
    svc.finish_assignment(assignment.id, AssignmentAction.END)
    svc.transition_ticket(ticket.id, TicketAction.DELIVER)
    assert store.get_ticket(ticket.id).status is TicketStatus.READY
    assert svc.complete_ticket(ticket.id, "完成").status is TicketStatus.COMPLETED


def test_recover_relaunches_interrupted_assignment(service) -> None:
    svc, store, _ = service
    ticket = svc.create_ticket("任务")
    svc.queue_ticket(ticket.id)
    assignment = store.list_assignments(ticket.id)[0]
    svc.register_run_id(assignment.id, "r-1")
    svc.finish_assignment(assignment.id, AssignmentAction.LAUNCH)   # -> running
    summary = svc.recover()
    assert summary.interrupted_assignments == 1
    assert store.get_assignment(assignment.id).status is AssignmentStatus.INTERRUPTED
    assert summary.relaunched == 1
    keys = [e.idempotency_key for e in store.due_effects(10_000)]
    assert f"launch:{assignment.id}:1:relaunch" in keys
```

（`from ...model import AssignmentAction` 需一并加入导入；`svc.complete_ready(ticket)` 只是把 Ticket
推到 READY 的测试辅助，实现里用 `svc.transition_ticket(ticket.id, TicketAction.DELIVER)`，
Step 1 里直接写这一行，不要新增公开方法。）

- [ ] **Step 2: 跑到红**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_service.py -v`
Expected: FAIL —`ModuleNotFoundError: ... orchestration.service`

- [ ] **Step 3: 实现 service**

```python
# stableagent/orchestration/service.py
"""P4 领域记录的唯一写入口。同步命令只做写入与入队，需要 I/O 的部分一律交给 outbox。"""

from __future__ import annotations

from dataclasses import dataclass

from stableagent.orchestration.errors import UnknownAssignmentError, UnknownTicketError
from stableagent.orchestration.model import (
    Assignment, AssignmentAction, AssignmentStatus, Clock, Dependency, Effect, EffectKind,
    EffectState, IdFactory, Mandate, Report, ReportKind, Ticket, TicketAction, TicketInput,
    TicketStatus, next_assignment_status, next_ticket_status,
)
from stableagent.orchestration.store import Store


@dataclass(frozen=True)
class TicketView:
    ticket: Ticket
    assignments: tuple[Assignment, ...]
    reports: tuple[Report, ...]
    inputs: tuple[TicketInput, ...]


@dataclass(frozen=True)
class RecoverySummary:
    interrupted_assignments: int
    reclaimed_effects: int
    relaunched: int


class OrchestrationService:
    def __init__(self, store: Store, *, clock: Clock, ids: IdFactory) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    # ------------------------------------------------------------------ 命令
    def create_ticket(self, task: str, *, title: str | None = None,
                      parent_id: str | None = None) -> Ticket:
        now = self._clock.now_ms()
        mandate = Mandate(id=self._ids.new_id("md"), granted_at=now, scope=title or task[:40])
        ticket = Ticket(
            id=self._ids.new_id("tk"), title=title or task[:40], task=task,
            created_at=now, mandate_id=mandate.id, parent_id=parent_id,
        )
        with self._store.transaction():
            self._store.insert_mandate(mandate)
            self._store.insert_ticket(ticket)
        return ticket

    def queue_ticket(self, ticket_id: str) -> Ticket:
        """OPEN/BLOCKED -> QUEUED，并在同一事务里建 Assignment + launch effect。"""
        with self._store.transaction():
            ticket = self._require(ticket_id)
            status = next_ticket_status(ticket.status, TicketAction.QUEUE)
            now = self._clock.now_ms()
            updated = ticket.model_copy(
                update={"status": status, "queued_at": now,
                        "woken_at": now if ticket.queued_at is not None else ticket.woken_at}
            )
            self._store.update_ticket(updated)
            attempt = len(self._store.list_assignments(ticket_id)) + 1
            assignment = Assignment(id=self._ids.new_id("as"), ticket_id=ticket_id,
                                    attempt=attempt)
            self._store.insert_assignment(assignment)
            self._store.enqueue_effect(
                Effect(id=self._ids.new_id("ef"), kind=EffectKind.LAUNCH_ASSIGNMENT,
                       idempotency_key=f"launch:{assignment.id}:{attempt}",
                       payload={"assignment_id": assignment.id, "ticket_id": ticket_id},
                       due_at=now)
            )
            return updated

    def complete_ticket(self, ticket_id: str, summary: str | None = None) -> Ticket:
        return self._apply(ticket_id, TicketAction.COMPLETE, summary=summary)

    def transition_ticket(self, ticket_id: str, action: TicketAction) -> Ticket:
        return self._apply(ticket_id, action)

    # ------------------------------------------------------------------ sink（executor 的回写面）
    def register_run_id(self, assignment_id: str, worker_run_id: str) -> None:
        with self._store.transaction():
            assignment = self._require_assignment(assignment_id)
            self._store.update_assignment(
                assignment.model_copy(update={"worker_run_id": worker_run_id})
            )

    def add_report(self, *, ticket_id, assignment_id, kind, summary,
                   event_step=None, details=None) -> None:
        with self._store.transaction():
            self._store.insert_report(
                Report(id=self._ids.new_id("rp"), ticket_id=ticket_id,
                       assignment_id=assignment_id, kind=kind, summary=summary,
                       created_at=self._clock.now_ms(), event_step=event_step,
                       details=dict(details or {}))
            )

    def finish_assignment(self, assignment_id: str, action: AssignmentAction,
                          *, failure: str | None = None) -> None:
        with self._store.transaction():
            assignment = self._require_assignment(assignment_id)
            status = next_assignment_status(assignment.status, action)
            self._store.update_assignment(
                assignment.model_copy(update={"status": status, "failure": failure,
                                              "ended_at": self._clock.now_ms()})
            )

    # ------------------------------------------------------------------ 调度支撑
    def enqueue_work_for_runnable_tickets(self, now_ms: int) -> int:
        """QUEUED 且无 pending Assignment 才算可运行；启动即 Ticket -> RUNNING。"""
        started = 0
        for ticket in self._store.list_runnable_tickets(now_ms):
            pending = [a for a in self._store.list_assignments(ticket.id)
                       if a.status in (AssignmentStatus.PENDING, AssignmentStatus.INTERRUPTED)]
            if not pending:
                continue
            with self._store.transaction():
                assignment = pending[0]
                action = (AssignmentAction.RELAUNCH
                          if assignment.status is AssignmentStatus.INTERRUPTED
                          else AssignmentAction.LAUNCH)
                self._store.update_assignment(
                    assignment.model_copy(update={
                        "status": next_assignment_status(assignment.status, action),
                        "started_at": now_ms})
                )
                self._store.update_ticket(
                    ticket.model_copy(update={"status": next_ticket_status(
                        ticket.status, TicketAction.START)})
                )
                self._store.enqueue_effect(
                    Effect(id=self._ids.new_id("ef"), kind=EffectKind.LAUNCH_ASSIGNMENT,
                           idempotency_key=(f"launch:{assignment.id}:{assignment.attempt}"
                                            f"{':relaunch' if action is AssignmentAction.RELAUNCH else ''}"),
                           payload={"assignment_id": assignment.id,
                                    "ticket_id": ticket.id},
                           due_at=now_ms)
                )
                started += 1
        return started

    def recover(self) -> RecoverySummary:
        now = self._clock.now_ms()
        interrupted = 0
        for ticket in self._store.list_tickets(status=TicketStatus.RUNNING):
            for assignment in self._store.list_assignments(ticket.id):
                if assignment.status is AssignmentStatus.RUNNING:
                    self.finish_assignment(assignment.id, AssignmentAction.INTERRUPT)
                    interrupted += 1
        reclaimed = self._store.reclaim_running_effects(now)
        relaunched = self.enqueue_work_for_runnable_tickets(now)
        return RecoverySummary(interrupted_assignments=interrupted,
                               reclaimed_effects=reclaimed, relaunched=relaunched)

    def get_ticket(self, ticket_id: str) -> TicketView:
        ticket = self._require(ticket_id)
        return TicketView(
            ticket=ticket,
            assignments=tuple(self._store.list_assignments(ticket_id)),
            reports=tuple(self._store.list_reports(ticket_id)),
            inputs=tuple(self._store.list_inputs(ticket_id)),
        )

    def list_tickets(self, *, status: TicketStatus | None = None,
                     limit: int = 50, offset: int = 0) -> list[TicketView]:
        return [self.get_ticket(ticket.id)
                for ticket in self._store.list_tickets(status=status, limit=limit, offset=offset)]

    # ------------------------------------------------------------------ 私有
    def _require(self, ticket_id: str) -> Ticket:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise UnknownTicketError(ticket_id)
        return ticket

    def _require_assignment(self, assignment_id: str) -> Assignment:
        assignment = self._store.get_assignment(assignment_id)
        if assignment is None:
            raise UnknownAssignmentError(assignment_id)
        return assignment

    def _apply(self, ticket_id: str, action: TicketAction,
               *, summary: str | None = None) -> Ticket:
        with self._store.transaction():
            ticket = self._require(ticket_id)
            status = next_ticket_status(ticket.status, action)
            ended = self._clock.now_ms() if status in TERMINAL_TICKET_STATUSES else ticket.ended_at
            updated = ticket.model_copy(update={"status": status, "ended_at": ended})
            self._store.update_ticket(updated)
            if summary is not None:
                self._store.insert_report(
                    Report(id=self._ids.new_id("rp"), ticket_id=ticket_id,
                           assignment_id=(self._store.list_assignments(ticket_id)[-1].id
                                          if self._store.list_assignments(ticket_id) else ""),
                           kind=ReportKind.READY, summary=summary,
                           created_at=self._clock.now_ms()))
            return updated
```

实现时注意三点，测试会立刻抓到：
`queue_ticket` 里 `woken_at` 的语义是"M2 的唤醒标记"，M1 恒为 `None`，直接删掉那行三元表达式；
`enqueue_work_for_runnable_tickets` 会为同一 Assignment 再次调用 `enqueue_effect`，
`relaunch` 后缀保证幂等键不同、原 effect 仍在库里（这正是 C3 要证的"不复活终态"）；
`_apply` 里 `assignment_id=""` 的兜底违反 B4 的意图，M1 改为 `summary=None` 时完全不写 Report，
有 summary 时必须存在至少一个 Assignment，否则抛 `IllegalTransitionError`。

- [ ] **Step 4: 跑到绿**

Run: `.venv/bin/pytest -q tests/unit/test_orchestration_service.py tests/unit/test_scheduler.py && .venv/bin/mypy && .venv/bin/ruff check stableagent`
Expected: PASS（Task 8 与本任务互为红绿，两个文件一起绿）

- [ ] **Step 5: 提交**

```bash
git add stableagent/orchestration/ tests/unit/test_orchestration_service.py tests/unit/test_scheduler.py
git commit -m "feat: make the service the only writer of control plane records"
```

**关闭 DoD：** A5、C2、L6 收尾（"service 不触达 executor"这条断言在本任务的测试里）。
G4 需要十个命令齐备，属 M2，本任务只落七个，不勾。

---

### Task 10: 进程级崩溃演练（SIGKILL）与 M1 出口

**Files:**
- Create: `tests/integration/ticket_recovery_child.py`、`tests/integration/test_ticket_recovery.py`
- Test: 上述两个文件

**Interfaces:**
- Consumes: 全部前序任务。
- Produces: 子进程入口脚本，逐行打印 JSON（与 `examples/03_checkpoint_resume.py` 同形状，
  便于测试读到第 2 个 Assignment 报告就击杀）；`test_ticket_recovery.py` 断言恢复后终态与
  "已完成 Assignment 不重跑"。

- [ ] **Step 1: 写子进程入口**

```python
# tests/integration/ticket_recovery_child.py
"""M1 崩溃演练的子进程入口：--phase run|recover，逐行打印 JSON 事件。

与 examples/03_checkpoint_resume.py 走同一套约定：stdout 每行一个 JSON，测试据此决定击杀时机。
Worker 用 tests/support.FakeProvider（离线、确定性），绝不碰网络。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stableagent.core.agent import Agent
from stableagent.core.state import Config
from stableagent.orchestration.model import Ticket
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store
from stableagent.orchestration.executor import AgentExecutor
from tests.support import FakeProvider, SequenceIds, final_response, tool_response


async def run(phase: str, db: Path) -> None:
    clock = _AdvanceClock()
    store = Store(db / "orchestration.db", clock=clock)
    ids = SequenceIds()
    service = OrchestrationService(store, clock=clock, ids=ids)

    def factory(ticket: Ticket, assignment) -> Agent:
        provider = FakeProvider([
            tool_response("call-1", "第 1 步中间结果"),
            final_response("完成：见报告"),
        ])
        return Agent(
            name=f"worker-{assignment.attempt}",
            provider=provider,
            config=Config(checkpoint={
                "backend": "sqlite", "sqlite_path": str(db / "checkpoint.db"),
                "auto_save_every": 1, "recovery_strategy": "resume",
            }),
        )

    executor = AgentExecutor(sink=service, agent_factory=factory, clock=clock)
    scheduler = Scheduler(store=store, service=service, executor=executor,
                          clock=clock, max_concurrency=1)

    if phase == "run":
        first = service.queue_ticket(service.create_ticket("任务 A").id)
        second = service.queue_ticket(service.create_ticket("任务 B").id)
        await scheduler.drain(max_rounds=4)
        for ticket in (first, second):
            print(json.dumps({"ticket": ticket.id,
                              "status": store.get_ticket(ticket.id).status.value}))
    else:
        summary = service.recover()
        print(json.dumps({"recover": summary.__dict__}))
        await scheduler.drain(max_rounds=4)
        for ticket in store.list_tickets():
            print(json.dumps({"ticket": ticket.id,
                              "status": store.get_ticket(ticket.id).status.value}))
    store.close()
```

`_AdvanceClock` 是本文件内的 8 行小类（`now_ms()` 每次调用 +1000ms），让 `due_at` 判定与
backoff 在单进程内确定性推进；不用 `time`。

- [ ] **Step 2: 写失败的测试**

```python
# tests/integration/test_ticket_recovery.py
"""G2：SIGKILL 后新进程 recover() 终态正确，且已完成 Assignment 不重跑。"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
from pathlib import Path

import pytest

CHILD = Path(__file__).resolve().parent / "ticket_recovery_child.py"


async def _spawn(phase: str, db: Path) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable, str(CHILD), "--phase", phase, "--dir", str(db),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )


async def _run_then_kill(db: Path) -> tuple[str, list[str]]:
    proc = await _spawn("run", db)
    assert proc.stdout is not None
    seen: list[str] = []
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode().strip()
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        if payload.get("report") and len(seen := seen + [payload["report"]]) == 2:
            os.kill(proc.pid, signal.SIGKILL)     # 第 2 个 Assignment 报告之后立即杀
            assert await proc.wait() != 0
            return seen[0], seen
    raise AssertionError("子进程没有产出两个 Assignment 报告就结束了")


def _assignment_rows(db: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(db / "orchestration.db")
    try:
        return [(row[0], row[1]) for row in
                conn.execute("SELECT id, status FROM assignments ORDER BY id").fetchall()]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_crash_mid_flight_then_recover_finishes(tmp_path: Path) -> None:
    db = tmp_path / "drill"
    db.mkdir()
    await _run_then_kill(db)
    before = dict(_assignment_rows(db))
    assert any(status == "running" for status in before.values())

    proc = await _spawn("recover", db)
    out = (await proc.stdout.read()).decode()
    assert await proc.wait() == 0
    after = dict(_assignment_rows(db))
    recovered = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert recovered[0]["recover"]["interrupted_assignments"] >= 1
    ended_before = {key for key, status in before.items() if status == "ended"}
    assert all(after[key] == "ended" for key in ended_before)          # 已完成不重跑
    assert all(status != "running" for status in after.values())       # 全部收敛
```

（测试文件顶部需要 `import sys`；击杀时机由"第 2 个报告"这一确定性事件触发，不用 `sleep` 赌时序——
这正是 ADR-02 后果 5 的教训。）

- [ ] **Step 3: 跑到红**

Run: `.venv/bin/pytest -q tests/integration/test_ticket_recovery.py -v`
Expected: FAIL（子进程入口的 report 行尚未按测试约定输出 JSON `{"report": ...}`）

- [ ] **Step 4: 补齐子进程的报告输出**

在 `ticket_recovery_child.py` 的 `run` 分支里，`service` 与 `store` 之外包一层打印：
每次 `add_report` 之后（用 `service` 的 `store.list_reports()` 轮询即可，或让 executor 的 sink
换成一个 8 行装饰器 `PrintingSink`）打印
`json.dumps({"report": report.id, "assignment": report.assignment_id, "kind": report.kind.value})`。
实现 `PrintingSink`（同文件，实现 `AssignmentSink` 的四个方法并委托 `service`），
把它传给 `AgentExecutor`——不要为了打印改动生产代码。

Run: `.venv/bin/pytest -q tests/integration/test_ticket_recovery.py -v`
Expected: PASS

- [ ] **Step 5: 跑 M1 出口命令并提交**

```bash
.venv/bin/pytest -q
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
git diff 46441f0..HEAD -- stableagent/core/loop.py            # 必须 0 行输出
git diff 46441f0..HEAD --stat -- stableagent/core/            # 必须无输出（M1 不动 Core）
.venv/bin/pytest --cov=stableagent --cov-report=term-missing  # orchestration 六文件 ≥90%
git add tests/integration/
git commit -m "test: prove the control plane survives a kill mid-flight"
```

然后把 M1 出口四条命令的实际输出贴进本账本第 1 节的 M1 出口条目（证据：待记录 → 实测），
并把 L1、L3~L8、A1~A6、B1~B7、C1~C3、D1~D3、D5、D7、E1、G2 勾为已完成。

**关闭 DoD：** G2、L1、L3、L4 收尾、L5、L8、B7（M1 出口复核）。H2 的本地门禁每次出口都跑，
但 H2 属 M3（第 1.4 节），本任务不勾。

---

### 计划自检（writing-plans 产出后执行）

- **规格覆盖**：M1 计划覆盖 spec WP-4.1 全部、WP-4.2 全部（A~H 中 M1 归属项）、WP-4.3 的
  `launch_assignment` 子集、WP-4.4 的 launch/映射/恢复、WP-4.5 的单槽与 Store 推导、WP-4.6 的
  崩溃演练第一版。WP-4.3 的 `deliver_input`/`cancel_assignment`、WP-4.5 的 DAG/环检测/真并发/优先级
  领取、`extra_input` Core 扩展、示例与 CI 扩容全部在 M2/M3，未在本计划伪装完成。
- **无占位符**：每个 Step 的代码块都是可直接落地的形状，没有"待补充/类似 Task N/适当处理"。
  Task 8 早期草稿里那段 `get and {...}` 与占位 run_id 已删除，实现只保留一份
  （`drive(assignment, ticket)` + `_guarded()` 内持有槽位）。
- **类型一致**：`Ticket.mandate_id: str`（`Mandate` 独立成行）在 Task 2 定义、Task 5 编解码、
  Task 9 的 `create_ticket()` 同事务写入，三处同名同型；`AssignmentSink` 四个方法的签名在 Task 7 定义、
  Task 9 实现、Task 8/10 以桩或真实 service 注入；`Scheduler` 不接 `ids` 参数（M1 内无用途，
  避免为将来预留未用参数）；`enqueue_work_for_runnable_tickets(now_ms) -> int` 在 Task 8 调用、
  Task 9 定义；幂等键格式统一为 `launch:{assignment_id}:{attempt}` 与
  `launch:{assignment_id}:{attempt}:relaunch`。
- **执行顺序**：Task 8 与 Task 9 互为红绿，先做 Task 9 也可，但两个任务必须在同一次推送前一起变绿。
- **本轮自审改掉的三处真问题**：(1) 四个 Task 的测试 docstring 曾声称覆盖 D4/E2/G4/C5 等
  M2 归属项，已改为只声称 M1 归属项并写明后半在哪个里程碑补；(2) A4 的"启动后 run_id 必填"
  曾被写成模型不变量，那会让 Task 7 的 `PENDING -> RUNNING` 转换直接失败（注册晚于转换），
  已改为 Task 7 的行为断言，并把"崩在注册之前"的空 run_id 行显式允许；
  (3) 规格第 4 节要求抛 `UserError` 子类而 Core 无此类，已按 spec 第 6 节第 8 条落为
  `OrchestrationError(ValueError)`。
