# P4（Ticket 编排内核）实施计划与验收账本

> 依据：`docs/Roadmap.md` Phase 4 + [P4 实施规格](../specs/p4-ticket-orchestration-kernel.md)
> + [ADR-04 编排与 Outbox](../design-decisions/04-ticket-orchestration.md)
> + [ADR-02 Checkpoint](../design-decisions/02-checkpoint.md)（`stream_recover` 语义与单快照源）
> + [ADR-03 Context](../design-decisions/03-context-engineering.md)（默认不增加事件噪音的结论）。
>
> **状态：Active（2026-11-02 计划经维护者确认，实现尚未开始；DoD 0/60，里程碑步骤 0/30）。**
>
> 使用方式：本文件既是 P4 的实施计划（第 1 节的里程碑步骤），也是 P4 的验收账本（第 0、2 节）。
> 每个可勾选事项都写明需求来源、可执行验证方法、对应测试或命令、证据占位；全部勾选且第 3 节命令
> 在 CI 复现后 P4 才算收口。计划推进时把实际输出追加到对应条目，不重写已记录的历史输出。
>
> **计数口径**：DoD = 第 0 节 L1~L8（8 项）+ 第 2 节 A~H（52 项）= 60 项；
> 里程碑步骤 = M1 的 10 项 + M2 的 12 项 + M3 的 8 项 = 30 项。里程碑与 DoD 的归属见第 1.4 节。

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
- [ ] **G4** `OrchestrationService` 十个命令全部可用且非法输入抛稳定 `UserError` 子类
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
