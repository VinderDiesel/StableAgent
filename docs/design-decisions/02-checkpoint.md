# ADR-02：Checkpoint 与失败自愈（P2 锁定，契约 v1.1.0 之上的兼容扩展）

- 状态：Accepted（P2）
- 日期：2026-10-30
- 关联：`00-contracts.md`（冻结规则、P2 扩展记录）、`01-loop-runtime.md`（F1/F3/F4）、
  `docs/p2-dod-checklist.md`（L1~L5、A~G 验收条目）
- 代码入口：`stableagent/core/checkpoint/{storage,manager,recovery}.py`、
  `stableagent/core/errors.py`、`stableagent/core/agent.py`（外层 `_pipeline`）

## 背景

Roadmap 的差异化卖点是"执行可靠性"，P2 的硬指标是招牌动作：**运行中 kill 进程 →
重启 → 从最近 checkpoint 续跑并跑完**。约束来自 P1：`core/loop.py` 已冻结（ADR-01 后果节
承诺 loop 不感知持久化），契约自 P2 起只允许兼容扩展。于是本阶段的全部设计问题都是
同一个问题的不同侧面——**如何在不动执行引擎的前提下，把"崩溃一致性"套在外层**。

## 决策

### 1. 接线位置：只长在 Agent 外层（L1/L3）

`Agent._pipeline()` 消费 `drive()` 产出的 `(事件, 快照)` 对，负责落盘、注入
`checkpoint`/`retry` 事件、失败自愈决策。`core/loop.py` 本阶段 diff 为空（DoD F1 的
机器证明），因此 ADR-01 的全部语义（F2/F3/F4/F5）在 P2 之后仍然逐条成立——P2 是叠加，
不是修改。

### 2. 一致性：步边界写 + "回看"式落盘

- **保存点 = 步边界**。与 ADR-01 F3 的暂停点同构：一个 `step` 内的
  `thinking → tool_call → tool_result` 是一个不可分的最小完成单元，快照里出现的
  `step=k` 状态必然是"k 步已完整结束"的状态，恢复时不会有半步残留。
- **回看（look-back）而不是前看**：第 k 步的快照在**第 k+1 步的第一个事件到达时**落盘，
  因为那一刻才证明第 k 步已经跑完。事件本身仍然即时透传——这一点是必需的而非优化的：
  若把事件缓冲到下一步再发，`pause()` 的步边界语义（F3）会被推迟一整步。
- **崩溃窗口**：进程被 SIGKILL 时，最后已落盘的快照是 step k，此刻正在跑的 step k+1
  全部作废 → 恢复后从 k+1 重跑。**最坏代价恰好是一步**，且这一步被刻意设计成幂等可重
  （快照里 `step` 与 `messages` 单调对齐，Provider 侧无副作用）。窗口大小可通过
  `auto_save_every` 调节：=1 时每步落盘（窗口 0 步，开销最大），=0 时只在暂停/终止点
  落盘（窗口最大，但 D1 保证暂停点不丢）。
- **终止态静默落盘**（保 F4）：`done`/`error` 的快照必须持久化（否则恢复时会把已完成的
  run 当成未完成重跑），但**不发 `checkpoint` 事件**——F4 要求终止事件位于流的末尾且
  恰好一个，若在 `done` 之后再注入一个事件就破坏了它。这是 ADR-01 与 ADR-02 之间唯一
  需要显式让路的地方，实现见 `agent.py` 的 `TERMINAL_EVENTS` 分支注释。

### 3. 并发：同 run 写锁 + 幂等覆盖写

- `CheckpointManager` 按 `run_id` 维护 `asyncio.Lock`：同一次运行的写入串行化，避免
  回看落盘与强制落盘（暂停/终止/needs_human 路径）交错写入产生交错记录。
- 存储主键为 `(run_id, step)`，**覆盖写幂等**：同一 step 重复保存不产生第二条记录
  （`manager` 侧另有 `_saved` 去重集合，`store` 侧靠 PK / dict key）。因此
  `INSERT OR REPLACE`、Redis 单 key 数组写都是安全的。
- **不提供跨进程锁**。多进程同时驱动同一个 run_id 视为使用错误：恢复语义要求"一个 run
  一个驱动者"，加分布式锁的收益（防误用）远小于代价（引入外部协调面）。SQLite 的
  busy 处理与 Redis 的实例内锁只保证单进程内一致。
- 历史裁剪：每 run 保留最近 `history_limit`（默认 20）条快照，三后端语义一致（DoD B4）。

### 4. 存储后端：统一四方法协议 + 客户端注入（L5）

`CheckpointStore` 只有 `save / load / records / delete` 四个异步方法；`load` = step 最大
的一条，`records` = 按 step 升序。协议面刻意小：后端差异不许泄漏到 manager 之上。

| 后端 | 取舍 | 适用 |
|------|------|------|
| `memory` | 零成本、进程重启即丢失（DoD B1 把它当成"反证"来断言） | 单测基线、开发路径 |
| `sqlite` | 单文件、零服务、`PK(run_id, step)` 天然幂等；同步驱动经 `asyncio.to_thread` 下沉以维持异步调用面，并自带实例内 `asyncio.Lock` | **P2 默认交付路径**，也是 A3 崩溃恢复用例的后端 |
| `redis` | 一个 run 一个 key、值为记录数组 JSON（读-改-写）；零 schema、跨进程可见，但牺牲了同 key 的并发写入最优性 | 多实例共享快照的部署形态 |

- **Redis 客户端是注入项**（`RedisLike` 协议只需 `get/set/delete` 协程，与
  `redis.asyncio` 兼容）。本包**不硬依赖 redis 库**：未安装 redis 也能 import 并通过全部
  用例（CI 用 fake client 跑通，真实 Redis 由环境变量开关、不可用时 skip 而非 fail）。
- `build_store()` 在 `auto_save_every <= 0 且 recovery_strategy == "none"` 时返回 `None`
  ——即"P1 默认行为不变"：不配 checkpoint 就不该多一次 I/O。

### 5. 版本迁移：注册表 + 读侧唯一入口 + 绝不猜测（DoD E1~E4）

- `MIGRATIONS: dict[源版本, (目标版本, 升级函数)]` 构成逐级升级链
  （`1.0.0 → 1.1.0`）；兼容扩展只抬 minor，故 `_next_version` 按 minor+1 推导。
- **缺 `contract_version` 字段 = 最早已知版本**（`EARLIEST_KNOWN_VERSION = "1.0.0"`），
  这是 P0 快照的真实形态。
- **高于当前版本 / 未知版本 / 版本串不可解析 → 抛 `SnapshotVersionError`**。理由：静默
  猜测会把语义漂移写进生产数据；宁可让运维者升级框架。
- 迁移发生在 **checkpoint 读取侧**（`recovery.migrate_state`），`AgentState.loads`
  保持严格、签名与不变式（契约二/三）不动——这是 DoD E4"API 不变"的实现方式。
- v1.0.0 → v1.1.0 的升级函数只做一件事：补默认值（`usage={}`、`Message.tool_calls=[]`），
  因此与 Pydantic 的字段默认值语义等价、可双向验证（`tests/fixtures/state_v1_0_0.json`）。

### 6. 错误分类与重试边界（DoD C2~C6）

- 三分类：`retryable` / `non_retryable` / `needs_human`。判据优先级固定为
  **HTTP 状态码 > 异常类型名 > 消息文本特征 > 默认**；未知错误**默认不可重试**——
  无界重试在生产的破坏力远大于一次快速失败，且分类表可随时按新增条目放宽（兼容扩展）。
- 分类实现只有一份（`errors.classify`），异常对象与 P1 归一化字符串
  （`"{类型名}: {消息}"`）两个入口都汇聚到它，避免两套规则漂移。
- **重试边界**：`max_attempts` **含首次调用**，故可重试次数为 `max_attempts - 1`；
  退避为 `min(base * factor**(n-1), max_delay)`，`sleep` 由外部注入（DoD C4 要求
  断言等待序列而不真等）。
- **fallback Provider 轮转**：配置了 fallback 时按 `retry_no % provider_count` 取索引，
  因此**第一次重试就切到备用 Provider**（瞬时故障最常见的成因是主通道本身），
  而不是耗尽主通道的重试次数后才切换。非 Provider 实例的槽位（字符串寻址，P6 之前
  未实现）不计入轮转池。
- **需人工 → 停在 `PAUSED`，不是 `FAILED`**（DoD C6）：鉴权/配额/审批类错误不是代码能
  解决的，人处理完调用 `resume()` 即可续跑——复用 ADR-01 F3 语义，**不新增状态机成员**。
  该路径只在 `recovery_strategy != "none"` 时生效：没开自愈就没资格改变终止方式。
- 重试的"续跑"由 `repair_for_retry()` 实现：剥掉 `error` 与 `FAILED`，`step`/`messages`
  原样保留，因此重试**不会重跑已完成步**（DoD C1 的"零重试路径"与 C2 的"重试路径"共用
  同一条不重跑保证）。

### 7. 恢复入口：`recover/arecover/stream_recover`

从存储读最近快照：终止态**原样返回不续跑**（否则会把成功的 run 又跑一遍）；
`RUNNING`（崩溃残留）与 `PAUSED`（人工暂停/需人工）都洗成 `RUNNING` 续跑。
外部快照续跑时清空实例内 `_last_state`，以绕开 F3 的"暂停中拒绝新任务"守卫——
那个守卫保护的是**同一实例内**的误用，跨进程恢复不属于它的情形。

## 备选方案（均否决）

- **在 `loop.py` 内落盘**：最直接，但违反 ADR-01 已冻结的承诺（"loop 不知道持久化存在"），
  且会让 P3 的上下文注入测试（D2/D3）失去"零修改 loop"的证据力。
- **前看式落盘（事件发完即存）**：看似等价，实际会在第 k 步的 `tool_result` 与
  `step` 自增之间留下窗口——崩溃后恢复会重跑第 k 步的工具调用。对昂贵/有副作用的工具
  不可接受。回看式把这个窗口推到"下一步的第一个事件"，代价只是 `checkpoint` 事件延迟
  一步到达（消费方按数据一致性优先于展示实时性）。
- **`done` 之后再发 `checkpoint` 事件**：能让"最后一次保存"可见，但直接破坏 F4。
  改为静默落盘 + `agent.last_checkpoint` 只读属性供演示/调试取用。
- **事件溯源（存事件流而非状态快照）**：恢复时需要重放全部事件才能重建 `AgentState`，
  恢复时间与历史长度成正比，与 Roadmap 的"恢复时间"指标相反。契约三更适合作为
  唯一持久化单元；事件流留给 P6 transport 层另做。
- **Redis 用 hash/多 key 存 step 索引**：读侧省一次全量反序列化，但要引入
  `HSET/SCAN` 协议面（`RedisLike` 从 3 方法涨到 6+），且 fake client 与真实客户端的
  差异面扩大。当前快照体量下（每 run ≤20 条）不值得。

## 后果

- 正面：招牌动作有自动化证据——`tests/integration/test_crash_recovery.py`（subprocess +
  `SIGKILL` + 二次 subprocess）离线确定性通过，恢复阶段 `provider_calls=2`（4 步任务、
  崩溃在第 2 步之后）直接证明 "已完成步不重跑"；三种后端共享同一组一致性断言（DoD B4
  参数化）；`core/checkpoint/` 三模块覆盖率 99–100%，全库 98%。
- 代价与遗留：
  1. 一步的崩溃窗口意味着**工具调用必须可重入**。框架只能约定，不能强制——
     幂等性检查留给 P4（审计/追溯）与 `Evidence` 增强。
  2. `auto_save_every > 1` 时 `checkpoint` 事件是**补发**的，其 `step` 可能小于当前
     流位置。消费方（P6 transport）不能假设 checkpoint 事件的 step 单调跟随事件流。
  3. 无跨进程锁：同 run 多驱动者属误用，不提供保护。
  4. 超时控制的"贯通"目前只到 `ProviderConfig.request_timeout_seconds → httpx 超时`
     这一层；Provider 调用整体的硬超时（`asyncio.timeout` 包裹）留待 P4 的预算治理
     一并设计，因为二者共享同一个 deadline 模型。
  5. 回看式落盘的时序对**外部观察者**有一个反直觉后果：第 k 步快照与第 k+1 步首个事件
     同时出现，若 k+1 就是终止步，快照到达和进程退出只差调度级几毫秒。演示脚本
     `examples/03_checkpoint_resume.py` 首轮在 CI 矩阵 3.10 上因此偶发失败（父进程
     `SIGKILL` 时子进程已正常退出）。框架行为正确，改法是让击杀点之后仍留一整轮
     provider 往返（`TOTAL_STEPS` 3 → 4），把可击杀窗口从十几毫秒拉到 `delay` 的线性
     量级——见 `docs/p2-dod-checklist.md` 的复核记录。**任何外部编排（P6 的超时看门狗、
     运维脚本）想用 checkpoint 事件判断"现在杀是安全的"，都必须按这条时序设计。**
- 契约影响：全部为兼容扩展（新枚举成员 `retry`、`CheckpointConfig` 新增 7 个带默认值
  字段），逐条记录见 `CHANGELOG.md` 与 `00-contracts.md` 的"P2 扩展记录"。
  `CONTRACT_VERSION` 保持 `1.1.0`：扩展未触及任何既有字段语义与不变式，不抬版本。
