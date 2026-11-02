# P4 详细实施规格：Ticket 编排内核

> **阶段定位**：在不接入 GitHub、飞书、MCP、Docker 或长期外部 Source 的前提下，将一次性 Worker run 组织为可持久化、可恢复、可并发调度的长期工作系统。P4 的工作单位是 `Ticket`，执行单位是 `Assignment`，执行内核仍是既有 `stableagent.Agent`。
>
> **开始条件**：P3 已满足其全部退出条件，并记录最终验证和 benchmark 基线。
>
> **完成条件**：本阶段全部状态机、仓储、调度和 durable outbox 用例可以在离线 SQLite 环境中恢复和验证，且没有将控制平面逻辑加入 `core/loop.py`。

## 1. 目标、非目标与架构边界

### 目标

P4 必须使调用方能够：

1. 创建一个持久化 Ticket，并以不可变的初始任务范围和本地显式 Mandate 记录其来源。
2. 为 Ticket 创建一个或多个 Assignment；每个 Assignment 映射到一个独立的 Worker `run_id`。
3. 以有限并发调度 Assignment，支持队列、依赖、取消、重试、崩溃恢复与失败隔离。
4. 从 Worker Event / AgentState 映射出结构化 Report，而不修改 Worker 的 Event 协议。
5. 使用 durable outbox 可靠执行“启动 Worker、投递输入、记录报告”等副作用，避免重启后的丢失或重复。
6. 提供基础 DAG，但不实现基于 LLM 的 Planner。

### 明确非目标

- 不接收 GitHub、飞书、Slack、定时器或 webhook 事件；这些是 P6 Source 的范围。
- 不创建实际 PR、不调用 GitHub API、不建立 worktree；这些是 P5 的范围。
- 不实现组织级 Policy、身份同步或复杂审批；P4 只提供 P5 可扩展的最小 Mandate 模型。
- 不实现 MCP、Docker、长期 Memory、Web UI、SSE/WebSocket 或多租户。
- 不让 Scheduler 直接读写 `AgentLoop` 内部状态，也不为编排修改 `core/loop.py`。

### 必须保持的边界

```text
P4 control plane
  Ticket / Assignment / Input / Report / Dependency / Effect
                    │ WorkerExecutor adapter
                    ▼
StableAgent Core
  Agent.run_id / AgentState / Event / CheckpointStore
```

- `AgentState` 仍只描述一次 Worker run，不记录 Ticket、DAG、队列、审批或外部 effect。
- Ticket ID 与 Worker `run_id` 必须不同，且 Assignment 永远显式保存二者的关系。
- `CheckpointStore` 不等于 P4 Store；前者恢复 Worker step，后者恢复 Ticket 和副作用。
- Ticket 状态和任何需执行的 effect 必须在同一 SQLite 事务中写入。事务提交前不得启动 Worker。
- 每个 `worker_run_id` 同时最多一个调度驱动者。Scheduler 重启不得并发恢复同一 Assignment。

## 2. 目录、模块与稳定接口

新增包建议为 `stableagent/orchestration/`，避免与 P3 `planning/` 占位目录混淆。P7 可将公开重导出迁至顶层，但 P4 先保持内部 API 明确。

```text
stableagent/orchestration/
  __init__.py
  model.py          # Ticket、Assignment、Input、Report、Effect、Dependency
  store.py          # SQLite schema、repositories、transaction、migration
  scheduler.py      # queue、并发槽、恢复、依赖判定
  executor.py       # WorkerExecutor 协议和 AgentExecutor 适配器
  outbox.py         # durable effect dispatcher、idempotency、retry
  service.py        # 唯一应用服务 / 命令入口
  errors.py         # 用户可读、可分类的编排错误
```

### 2.1 领域模型

所有模型使用 Pydantic v2，并遵循“新字段必须有默认值”的兼容规则。

| 模型 | 必填字段与语义 |
|---|---|
| `Ticket` | `id`、`title`、`task`、`created_at`、`status`、`mandate`、`parent_id?`、`ended_at?`。它是长期任务事实源。 |
| `Mandate` | P4 最小字段：`id`、`origin_kind="local"`、`requester="local"`、`scope`、`policy_version="p4-local-v1"`、`granted_at`。P5 只能兼容扩展。 |
| `Assignment` | `id`、`ticket_id`、`attempt`、`worker_run_id?`、`status`、`started_at?`、`ended_at?`、`checkpoint_run_id?`、`failure?`。一次重试创建新 Assignment，不覆盖历史。 |
| `TicketInput` | `id`、`ticket_id`、`sequence`、`kind`、`payload`、`created_at`、`delivered_at?`、`consumed_at?`。P4 可只支持初始任务和本地文本输入。 |
| `Report` | `id`、`ticket_id`、`assignment_id`、`kind`、`summary`、`event_step?`、`created_at`、`details`。kind 固定为 `progress`、`waiting_for_human`、`ready`、`blocked`、`failed`。 |
| `Dependency` | `ticket_id`、`depends_on_ticket_id`、`on_failure`。P4 支持 `block` 和 `continue`，默认 `block`。 |
| `Effect` | `id`、`kind`、`idempotency_key`、`payload`、`state`、`attempts`、`due_at`、`last_error?`。外部或异步动作的唯一可靠队列。 |

### 2.2 状态机

状态必须由集中函数或 service 命令转换，Repository 不得暴露任意字段更新。

```text
Ticket: open → queued → running → waiting | ready | blocked | failed | cancelled → completed
Assignment: pending → running → ended | failed | cancelled
Effect: pending → running → succeeded | retry_wait | failed | cancelled
```

规则：

- Ticket 只在所有依赖满足且有 pending Assignment 时进入 `queued`。
- Assignment 运行时 Ticket 为 `running`；一个 Ticket 同时最多一个 `running` Assignment。
- `ready` 是 Worker 已交付可审查结果，P4 中由调用方使用 `complete_ticket()` 转为 `completed`；不自动等同 completed。
- `waiting_for_human` 的 Report 使 Ticket 进入 `waiting` 并释放并发槽。
- `blocked`、`failed` 保留失败原因，不自动无限重试。
- `cancel_ticket()` 只能取消未结束 Ticket，并为 running Assignment 写 `cancel_assignment` effect；不得删除历史。
- Assignment 的最终 Report 只能结束该 Assignment；Ticket 是否结束由 service 根据状态机判定。

### 2.3 公共应用服务

`OrchestrationService` 是 P4 领域记录的唯一写入口。第一版提供以下同步命令；其中需要 I/O 的部分只入队 effect：

```python
create_ticket(task: str, *, title: str | None = None, parent_id: str | None = None) -> Ticket
add_dependency(ticket_id: str, depends_on: str, *, on_failure: FailurePolicy = "block") -> None
queue_ticket(ticket_id: str) -> Ticket
submit_input(ticket_id: str, text: str) -> TicketInput
retry_ticket(ticket_id: str) -> Assignment
cancel_ticket(ticket_id: str, reason: str | None = None) -> Ticket
complete_ticket(ticket_id: str, summary: str | None = None) -> Ticket
recover() -> RecoverySummary
get_ticket(ticket_id: str) -> TicketView
list_tickets(...) -> list[TicketView]
```

非法状态、未知 ID、环状依赖、已结束 Ticket 写入、跨 Ticket Assignment 报告等必须抛出稳定的 `UserError` 子类，不能静默忽略。

## 3. 实施工作包

### WP-4.1：模型、ID、时间与状态机

**文件**：`orchestration/model.py`、`errors.py`、`tests/unit/test_orchestration_model.py`。

1. 使用 UUID 或 ULID 生成 Ticket、Assignment、Input、Report、Effect ID；测试注入 ID/clock，不读取墙钟。
2. 实现上述模型与枚举，模型序列化必须稳定、可 round-trip。
3. 实现唯一的状态转换函数，返回新模型或在 service transaction 内修改记录。
4. 添加 model 验证：Ticket 不得依赖自己；Assignment 必须属于 Ticket；`attempt >= 1`；`worker_run_id` 在启动前可空，启动后必填。
5. 定义 `TicketView`，仅由持久化记录派生当前状态，不引入缓存事实源。

**验收**：覆盖所有合法/非法转换；时间、ID、状态派生测试均为确定性。

### WP-4.2：SQLite Store 与迁移

**文件**：`orchestration/store.py`、`tests/unit/test_orchestration_store.py`、`tests/fixtures/`。

1. 使用独立 SQLite 数据库或 P4 schema 版本表；不要复用 P2 checkpoint 表。
2. 建表：tickets、mandates、assignments、inputs、reports、dependencies、effects、schema_migrations。
3. `Store.transaction()` 使用 SQLite 事务；所有 domain mutation 和 `enqueue_effect()` 必须在同一 transaction 中完成。
4. 对 `(ticket_id, attempt)`、`Assignment.worker_run_id`、`Effect.idempotency_key` 设唯一约束；`Effect.kind + idempotency_key` 的语义必须记录清楚。
5. 提供 restart/reopen、schema migration、查询排序、分页、事务回滚、并发读取的离线测试。
6. Store 层只做持久化和约束，不做 Worker 调度或策略判断。

**验收**：模拟在 transaction 中异常，断言 Ticket 修改和 effect 均不落库；重开数据库后所有记录与顺序无损。

### WP-4.3：Durable Outbox / Effects

**文件**：`orchestration/outbox.py`、`tests/unit/test_outbox.py`。

1. 定义 effect kind：`launch_assignment`、`deliver_input`、`cancel_assignment`；P4 不添加外部消息、PR 或 Source effect。
2. Service 每次创建/重试 Assignment 时，在同一事务创建 `launch_assignment` effect。输入写入后创建 `deliver_input` effect。
3. Dispatcher 在 commit 后拉取 due effects；同一 idempotency key 的 pending effect 重复入队只能更新 payload，不新增副作用。
4. Handler 成功才将 effect 标记 succeeded；失败采用注入式 backoff，达到 `max_attempts` 后标记 failed 并派生 Ticket/Assignment `blocked` 或 `failed` Report。
5. effect handler 的 I/O 不能持有 SQLite transaction。程序重启后 pending/retry_wait effect 必须继续执行。
6. 按 effect kind 保序：同一 Assignment 的输入必须按 sequence 投递；不同 Ticket 可并发。

**验收**：测试“commit 后 crash”“handler 成功前 crash”“重复 dispatch”“失败重试”“超限失败”“同 Ticket 保序”“不同 Ticket 并发”。不得通过内存标志实现幂等。

### WP-4.4：WorkerExecutor 与 AgentExecutor 适配器

**文件**：`orchestration/executor.py`、`tests/unit/test_agent_executor.py`。

定义窄协议，便于测试桩和 P5 执行环境替换：

```python
class WorkerExecutor(Protocol):
    async def launch(self, assignment: Assignment, ticket: Ticket, inputs: list[TicketInput]) -> None: ...
    async def deliver(self, assignment: Assignment, inputs: list[TicketInput]) -> None: ...
    async def cancel(self, assignment: Assignment) -> None: ...
    async def recover(self, assignment: Assignment) -> None: ...
```

`AgentExecutor`：

1. 为 Assignment 构造或获取 Agent 实例；由 Assignment 保存 Agent 配置版本和 `worker_run_id`。
2. 首次启动调用 `Agent.stream()`；恢复调用 `stream_recover(worker_run_id)`。绝不手动重放 Event 或复制 state。
3. 将 `THINKING`、`TOOL_CALL`、`TOOL_RESULT` 映射为可选 progress Report；`PAUSED` 映射 waiting/blocked；`DONE` 映射 ready；`ERROR` 映射 failed。
4. Worker Event 原样可留作诊断载荷，但 Report 是 P4 的稳定领域接口。不要让 Ticket 状态依赖自然语言文本。
5. 每次启动前注册 Assignment 的 `worker_run_id`；不允许同 run 两个 executor 并行驱动。
6. 初始输入作为 task 或明确封装进 Worker prompt；后续输入 P4 只能在 Worker 空闲/暂停边界投递，不能抢占一个正在执行的 `AgentLoop.advance()`。

**验收**：FakeExecutor 与 FakeProvider 验证映射、恢复、取消和重复 launch 防护；核心测试不得需要真实 LLM。

### WP-4.5：Scheduler、并发与 DAG

**文件**：`orchestration/scheduler.py`、`service.py`、`tests/unit/test_scheduler.py`。

1. Scheduler 轮询或显式 `tick()`，只从 Store 推导可运行 Ticket；不要维护不可恢复的内存队列。
2. `max_concurrency` 限制 running Assignment 数；槽位从 launch effect 被领取起占用，直到 Assignment ended、waiting、blocked、failed 或 cancelled。
3. 父 Ticket 可创建子 Ticket；P4 不要求模型 Planner，子 Ticket 只能由 API/测试显式创建。
4. DAG 添加前检测直接和间接环；Ticket 只有所有依赖 completed 才可运行。
5. 依赖失败时：`block` 使下游 blocked 并写结构化原因；`continue` 允许下游运行但把失败依赖作为 Input/metadata。
6. 优先级顺序固定并记录：人工 `retry_ticket`/`submit_input` 唤醒的 Ticket > 已排队 Ticket > 新建 Ticket。若不实现优先级，必须明确 FIFO 并测试。
7. `recover()` 将 crash 时 running Assignment 标为 interrupted，并通过 effect 重启或从 Agent checkpoint 恢复；旧 effect 和新 effect 共享幂等规则。

**验收**：覆盖并发上限、慢任务不阻塞无关 Ticket、环拒绝、依赖成功/失败两策略、取消、重试、重启恢复和单 run 单驱动。

### WP-4.6：最小本地接口、示例与故障演练

**文件**：`examples/04_ticket_orchestration.py`、`tests/integration/test_ticket_recovery.py`、`docs/design-decisions/04-ticket-orchestration.md`。

1. 提供离线示例：创建父/子 Ticket，设依赖，运行两个脚本 Worker，输出 Ticket/Assignment/Report 时间线。
2. 集成测试在子进程中创建 SQLite Store、执行到至少两个 Assignment 后 SIGKILL；新进程 `recover()` 后最终状态正确、已完成 Assignment 不重复启动。
3. ADR-04 记录：Ticket 与 AgentState 的边界、状态机、outbox 事务、idempotency key、输入投递语义、Scheduler 单驱动与被拒绝的方案。

## 4. 测试、质量与完成门槛

### 必测场景

| 类别 | 必须证明 |
|---|---|
| 模型 | 所有状态转换、默认兼容字段、序列化、非法转换和环检测 |
| Store | 原子 transaction、迁移、重开、唯一键、排序、回滚 |
| Outbox | commit 后执行、重启重试、幂等、保序、失败耗尽 |
| Executor | Event→Report 映射、checkpoint recover、取消、单 run 单驱动 |
| Scheduler | 并发上限、依赖、优先级、隔离、重试、取消、恢复 |
| 集成 | SQLite + 多 Ticket + crash recovery，无网络、无真实模型 |
| 回归 | P0-P3 现有命令全绿，`core/loop.py` 无 P4 diff |

### 阶段验证命令

```bash
.venv/bin/pytest -q tests/unit/test_orchestration_model.py tests/unit/test_orchestration_store.py
.venv/bin/pytest -q tests/unit/test_outbox.py tests/unit/test_agent_executor.py tests/unit/test_scheduler.py
.venv/bin/pytest -q tests/integration/test_ticket_recovery.py
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
.venv/bin/pytest --cov=stableagent --cov-report=xml --cov-report=term-missing
```

### P4 退出清单

- [ ] Ticket、Assignment、Input、Report、Dependency、Effect 均持久化且有明确状态机。
- [ ] 每个 Assignment 唯一映射 Worker `run_id`，不污染 `AgentState`。
- [ ] domain mutation 与 effect enqueue 原子提交；effect 只在提交后执行且可重试、幂等、保序。
- [ ] Scheduler 实现有限并发、DAG、取消、重试、恢复和失败隔离。
- [ ] 任意崩溃恢复不会重复已确认 Assignment 或 effect 的外部语义。
- [ ] 全部测试离线确定性；P0-P3 无回归；新增 orchestration 心脏模块 CI 覆盖率 ≥90%。
- [ ] ADR-04、CHANGELOG、README 和示例已完成。

## 5. 阶段交接：P5 的输入

P5 复用 P4 的 Ticket、Assignment、Report、Effect、Scheduler 和 Store，但不得替换其 ID、状态机或 outbox 语义。P5 只扩展：

- `Mandate` 的授权字段与 Policy 版本；
- `WorkerExecutor` 的实现，使其在 worktree / sandbox 中启动；
- 新的 effect handler，用于安全的 GitHub 评论和 PR 创建；
- 输入来源元数据，仍通过 TicketInput 而不是直接写入 AgentState。

P5 开始前必须保存 P4 的 SQLite recovery 集成基线和 Worker Event→Report 映射契约。 
