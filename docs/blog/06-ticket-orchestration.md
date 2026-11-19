# Ticket 编排：把长期目标拆成多次 Worker 执行，还能整体恢复

> **状态：Draft（发布前不可作为承诺）。** 本文讲的是一套**与具体项目无关的设计原理**：当一个目标要跨很多次 Agent 运行才能完成时，整体进度、依赖与并发由谁记录，崩溃之后又凭什么接着走。文中代码、字段与数字均为**示意**，用来把原理讲清楚，不绑定某一套实现。演示与测试都使用离线脚本 Provider、注入的时钟与 ID，**不是**真实模型端到端的长任务数据；发布前需补一张「Ticket / Assignment / Report 三层 → 调度门控 → 快照恢复」的分层图，以及真实 Provider 的崩溃演练数据。

前五篇把**一次 Worker 运行**做稳了：进程死了从快照续跑（01）、这一轮到底发什么给模型可测量（02）、对外部世界的动作不丢不重（03）、能力能叠在循环外而不改内核（04）、一次调用失败该重试还是该停下来等人（05）。它们合起来保证的是一句话：**只要这个 run 还在，它就能稳定地往前走一步**。

但真实的长期目标很少是一个 run。「把整个仓库的重构做完」要拆成调研、修改、验证、交付；父任务没验收，子任务不该发车；某个子任务在等人审批，并发槽位不该被它一直占着；编排进程半夜被打死，第二天重启时，谁记得哪些做完了、哪些做到一半、哪些压根没起？

直觉做法几乎总是同一句："起一个大循环，内存里放一张 todo 列表，跑完一项划掉一项。" 它在 demo 里永远有效，在生产里至少制造四类事故：

- **进度归零**：todo 列表在内存里，进程一重启，跨天的整体进度凭空消失，只剩一堆没人认领的 run；
- **依赖失守**：子任务在上游还没验收时就发车，拿着半成品继续往下做；
- **双驱动者**：崩溃恢复时同一个子任务被起了两遍，于是有两条 PR、两次通知——而 03 已经讲过，外部世界回滚不了；
- **状态不可判定**：等人、排队、失败被混成同一个 `status`，调度器不知道该重试、该让位还是该收口。

本篇的主张是一句可以被证伪的口号：

> **跨 run 的事实只落在领域记录里；每次执行是一条可单独恢复的 Assignment；调度只是把记录推进一格，而且这一格必须先记后做、可以重复判定。**

招牌动作是**在父 Ticket 的第 2 条进度报告落库时，把编排进程 `SIGKILL` 掉，再用同一个目录恢复**：

```text
[kill] 2 条 progress 已落库，SIGKILL pid=…
{"recover": {"interrupted_assignments": 1, "reclaimed_effects": 0, "relaunched": 1}}
SELFTEST ok=True killed=True completed=2
```

`completed=2` 与恢复进程那条恰好为 `[3]` 的 Provider 调用记录是全部重点：前者来自父子 Ticket 的击杀演示——重启后两票都从落定的事实继续，最终都完成；后者来自另一场崩溃演练——崩溃前已结束的 Assignment 一次都没重跑，被中断的那条从最近的步边界快照续跑，只补了 3 次模型调用。只要前者被重跑，这条记录里就会多出一个条目。第六节给出可以直接跑的命令。

---

## 一、三层记录：Ticket、Assignment、Report 各回答一个问题

先别急着写调度器。长期任务最先出错的地方，是**把三种不同生命周期的事实记在同一行里**。

| 记录 | 生命周期 | 回答的问题 | 典型字段（示意） |
|---|---|---|---|
| **Ticket** | 跨多次 run，直到终态 | 这件事整体到哪了 | `id / status / depends_on / queued_at / woken_at` |
| **Assignment** | 绑定一次 Worker 执行 | 这一轮由谁跑、跑到哪了 | `id / ticket_id / attempt / status / worker_run_id / deadline_at` |
| **Report** | 一条不可变的事实 | 下游可以据此做什么 | `ticket_id / assignment_id / kind / details / event_step` |

示意结构：

```python
class Ticket(BaseModel):
    id: str
    status: TicketStatus                 # queued / running / waiting / ready / blocked / …
    depends_on: list[str] = []
    woken_at: int | None = None          # 被人工唤醒过，优先级高于纯排队

class Assignment(BaseModel):
    id: str
    ticket_id: str
    attempt: int = 1                     # 每次重试新开一条，历史不覆盖
    status: AssignmentStatus             # pending / running / ended / interrupted / …
    worker_run_id: str | None = None     # 与 Worker 快照唯一的接缝

class Report(BaseModel):
    ticket_id: str
    assignment_id: str
    kind: ReportKind                     # progress / ready / waiting_for_human / blocked / failed
    details: dict = {}                   # 原样承载诊断载荷，不给状态机读
```

为什么不能只用一张「任务表」？因为一个 Ticket 可能被重试多次，每次尝试都有独立的 Worker、独立的失败原因、独立的 `worker_run_id`。把 `attempt` 覆盖在原行上，等于把「上次为什么失败」和「这次跑到哪」揉成一团；恢复时既无法判断该从哪条快照续，也无法回答「这一轮是否已经做过一次」。反过来，Worker 内部吐出的几十条诊断事件也不配当领域状态：它们高频、可读、随版本变化，而领域记录需要少、稳、可驱动下游。

- **换来什么**：跨 run 的进度是磁盘上的结构化事实；重试历史可回溯；编排层不必去读懂 Worker 的每一条事件。
- **付出什么**：一套命令要同时维护三处记录，写库次数变多，而且必须靠**同一事务**保证它们不撕裂——这正是 03 的 outbox 机制在这里的用处。
- **不这么做会怎样**：最诱人的省法是「把整体计划塞进 `messages`，让模型自己记进度」。代价是上下文越跑越长（02）、模型随时可能"写得像已完成"（结构化事实为零）、进程重启后没有任何可判定的整体状态。计划可以放在 prompt 里给模型看，但**不能放在 prompt 里给系统当事实源**。

顺带把两篇的分工说清：**03 讲 effect 机制**（一次副作用为什么不丢不重），**本篇讲领域状态机**（哪些状态存在、谁能推进、崩溃后整体怎么收敛）。前者是手段，后者是账本。

## 二、状态机集中：非法转移必须当场报错

有了三层记录，下一个问题是「谁能改状态」。答案是**只有一张表能改**：

```python
def next_ticket_status(current, action):
    try:
        return TABLE[(current, action)]      # (当前状态, 动作) -> 下一个状态
    except KeyError as exc:
        raise IllegalTransitionError(current, action) from exc
```

Ticket、Assignment、Effect 各有一张这样的转移表，都是纯函数；存储层不提供"任意字段更新"的通用方法，非法转移、未知 ID、环状依赖、跨 Ticket 报告一律抛**带稳定错误码**的异常。Ticket 的主干大致是：

```text
open → queued → running → waiting / ready / blocked / failed / cancelled → completed
```

三个口径细节最容易被写错：

1. **`ready` 不是 `completed`**。前者表示 Worker 交付了结果、等人验收；后者要一次显式的完成动作才落。把两者合并，依赖闸门就会在验收前放行上游半成品。
2. **`interrupted` 属于 Assignment，不属于 Ticket**。Ticket 仍是 `running`，所以它还能被恢复流程挑出来；如果连 Ticket 一起标成失败，恢复入口就不会再看它。
3. **终态是 `{completed, cancelled, failed}`**。给终态再写报告、再起 Worker，都必须被拒绝，而不是"安静地成功"。

依赖与优先级也属于这张状态机，而不是散落的 `if`：

- 依赖只有在上游**全部 completed** 时才放行；上游落到终态失败，按 `on_failure = block | continue` 决定是把本票置为 `blocked`，还是带着一条结构化提示继续。加依赖时先做环检测，发现环直接拒绝——"跑起来再看会不会死循环"不是可接受的验收方式。
- 优先级不写成判断链，写成一条**可测的排序键**：被人工唤醒过的先跑，其次按入队时间，再按创建时间。示意：

```sql
SELECT * FROM tickets WHERE status = 'queued'
ORDER BY (woken_at IS NULL) ASC, queued_at ASC, created_at ASC;
```

这样"谁先发车"有唯一答案，测试可以逐行断言，而不是靠调度循环的遍历顺序碰运气。

- **换来什么**：任何一个状态都能回答"是谁、在哪个动作下写进去的"；恢复时看到一个 `running`，能确定它对应哪条 Assignment、哪次尝试。
- **付出什么**：加一个状态就要改表、改命令、改测试；排序键一旦被测试钉死，也视同契约。表达力是刻意收窄的。
- **不这么做会怎样**：状态散落在各个处理分支里，恢复时无法区分 `running` 是"刚写完还没发车"还是"Worker 已经跑了一半"。这其实就是 01 那句"任意时刻切开的状态没有定义"，只不过从单 run 内部搬到了跨 run 这一层。

## 三、门控是状态推进的守卫，不是重试器

一轮调度做三件事：**从存储推导可运行 Ticket → 在同一事务里入队启动 effect → 提交后领取执行**。后两步是 03 的机制；本篇要补的是领取之前那道门控——它决定**这一格能不能推**。

门控必须在**领取之前**：关闭时不领取、不消耗 `attempts`、不改写 `due_at`。于是"现在不行"被分成清清楚楚的几类：

| 情形 | 判定来源 | 账本后果 |
|---|---|---|
| 同一 Assignment 已有活任务（在途去重） | 内存中的取消 / 去重句柄 | effect 保持 `pending`，拒绝第二次驱动 |
| 依赖未满足、并发槽位满 | 存储里的 Ticket 状态 + 活任务数 | effect 保持 `pending`，**不吃重试预算** |
| 要投递输入但 Worker 正忙 / 尚未驻留 | 存储 + 活任务集合 | 可延迟类错误：退还刚 claim 的那次预算，固定退避 |
| 任务级 deadline 到期 | `deadline_at` 与注入时钟 | Assignment `cancelled`、Ticket `blocked`，写一条带结构化 `reason` 的 `BLOCKED` Report |
| 真失败 | 其它异常 | 吃 `max_attempts` 预算，超限才 `failed` |

第一列里最容易被忽略的是"驻留不占槽位"：任务停在 `waiting` 等人处理时（05 的 `PAUSED`），协程让位，**等待由 Ticket 状态表达，不占并发槽位**。否则一个等人审批两小时的任务，会把整台机器的并发额度锁死。

内存里那张 `_tasks` 表只作取消与去重句柄；**可运行性的事实源永远是存储**。这与 03「内存队列不是事实源」是同一条纪律，但这里关心的不是"丢没丢"，而是**所有权**：同一时刻，谁有权驱动同一个 run。判定只从存储推导，两个调度实例才不会因为各自的内存视图不同而同时发车。

- **换来什么**：等待不会被自己的队列位置杀死；崩溃重启后门控结论可重算，不需要恢复内存状态。
- **付出什么**：单事件循环 + 同步存储的写操作会短暂阻塞驱动循环。03 已给过固定规模下 `tick` / `recover` / 重放的量级与口径；那组数字只用来验证"该规模下同步存储不构成瓶颈"，**不外推为性能承诺**，规模变大就得换存储方案。
- **不这么做会怎样**：把门控挪到 handler 里（领取后才发现不行），排队久的任务会被自己的重试预算判死；把门控下放给每个 Worker 自己判断，则等于每个执行单元各维护一份规则，与 05「分类只有一份」踩的是同一个坑。

## 四、恢复的接缝：谁有权继续驱动同一个 run

01 回答了"一个 Worker 怎么从快照续跑"，本篇要回答的是更靠前一步的问题：**编排进程重启后，凭什么认定哪条记录该由自己接着驱动**。

这里有三层容易错位的事实：编排层还留着一条 `running` 的 Assignment，内存句柄已经随进程消失；Worker 的快照存在另一个存储里；两者之间只有一个 `worker_run_id`。恢复流程因此是：

```python
def recover():
    for ticket in store.list_tickets(status=RUNNING):
        a = first(a for a in store.list_assignments(ticket.id) if a.status is RUNNING)
        finish_assignment(a.id, INTERRUPT)                       # running -> interrupted
        enqueue_effect(kind=LAUNCH_ASSIGNMENT,
                       idempotency_key=f"launch:{a.id}:{a.attempt}:relaunch")
    reclaim_running_effects()                                    # running effect 退回 pending
```

四个细节决定这条接缝是否干净：

1. **只挑 `running` 的记录，且只重启一个在途 Assignment**。已经 `ended` 的不碰——否则"先完成的那个子任务"会在恢复时被重跑，外部动作随之做第二遍。
2. **重新入队用不同的幂等键**（`:relaunch` 后缀）。03 讲过"已终态的行绝不复活"：如果恢复沿用原键，新入队会命中那条成功的旧行被安静吞掉，故障表现是**静默卡死**，不是报错。
3. **`worker_run_id` 在第一个步边界才注册**。04 讲过，循环对外吐的是「事件 + 事件后快照」对，run_id 只在第一个步边界之后可读，启动前根本拿不到。若崩溃发生在这之前，字段仍是 `NULL`，恢复就以同一 Assignment、同一 attempt 重新发车；其重复风险已被 01 的"一步崩溃窗口 → 工具必须可重入"覆盖，**不新增不变式**。没启用 checkpoint 的 Worker 则直接拒绝启动——没有可恢复的快照，就不该假装能恢复。
4. **续跑读快照，不重放事件**：`stream_recover(worker_run_id)` 载入最近快照后继续，不重放历史事件、不复制一份 `AgentState`。编排层只读 Worker 的事件流，把它映射成少量 Report。

最后一条是两层系统的边界纪律：**`AgentState` 里没有 ticket 字段，Ticket ID 与 run_id 是两个不同的标识**；编排记录自带版本，既不并入内核的公共契约版本，也不从顶层重导出。内核的默认路径仍由 04 那套逐字节金标准锁定。于是"编排层能不能改 Worker 的快照"这个问题只有一个答案：不能。

- **换来什么**：两层各自只有一个崩溃窗口定义，不会互相假装"对方兜住了"；恢复只依赖已经落定的事实，而不是重放推导出来的第二套状态。
- **付出什么**：两个存储、两套版本，还要显式处理"run_id 尚未注册"这个分支；编排层想给暂停中的 Worker 送新输入，也只能走一个默认 `None` 的可选参数（04 的最小口子），不能抄近路改写快照。
- **不这么做会怎样**：把新输入直接写进 Worker 的最新快照，或把整份 `AgentState` 复制进 Ticket 表，短期省一张表，长期就得到两个"唯一事实源"——恢复时该信谁，没人能证明。

## 五、Event → Report：领域接口不等于诊断流

Worker 一次运行会吐出大量事件：思考、工具调用、工具结果、暂停、终止。它们适合给人看、给 UI 用，**不适合当长期状态**。领域接口只需要少量结构化结论，映射必须固定：

| Worker 事件 / 终态 | 领域结论 | 后续动作 |
|---|---|---|
| `DONE` | `READY` | Assignment `ended`，Ticket 等验收 |
| `ERROR` | `FAILED` | 取结构化 `reason`，不解析错误文本 |
| `PAUSED(reason=needs_human)` | `WAITING_FOR_HUMAN` | 驻留等人，释放槽位 |
| `PAUSED(reason=budget_exceeded)` | `BLOCKED` | 留结构化原因，等人工或外部条件 |
| `TOOL_RESULT` | `PROGRESS`（默认唯一） | 事件 payload 原样作为诊断载荷 |

三条纪律：

- **只读结构化字段**：`Event.type`、状态枚举、`reason` / `error` 记录。错误与暂停原因都是自由文本，用正则去"猜准"一个状态，等于在代码里偷偷建立一条不可靠的保证——05 的分类优先级已经把这条讲透；拿不到结构化原因时应当**显式降级**并把降级写进文档，而不是假装判断成功。
- **默认只落一条 progress**：`THINKING` / `TOOL_CALL` 默认不进长期存储。一旦进度事件默认高频，长期列表查询的信噪比立刻垮掉，真正重要的 `ready` / `blocked` 被淹掉。要不要放开到思考级，是配置项，不是默认值。
- **归属要校验**：Report 必须绑定到同一 Ticket 下的 Assignment，跨 Ticket 报告直接拒绝；事件 payload 原样进 `details`，供人排查，不参与状态判定。

- **换来什么**：长期状态少、稳、可驱动下游；依赖闸门、人工看板、超时策略都只需读 Report 与 Ticket 状态。
- **付出什么**：Worker 新增一类事件不会自动出现在领域记录里，必须显式加映射；这是"默认不增加噪音"刻意付出的表达力代价。
- **不这么做会怎样**：让模型在最终回答里写一句"任务已完成"，调度器用文本匹配判断依赖是否满足——不可验收，且与 02「省 token 与保事实必须成对」、05「不从文本猜类型」自相矛盾。

## 六、招牌动作：杀死编排进程，两个 Ticket 仍然都完成

把上面五节变成能跑的命令。在项目根目录、激活虚拟环境后执行（`--dir` 指到临时目录可避免污染工作区，输出与默认目录一致）：

```bash
python examples/04_ticket_orchestration.py --phase selftest --dir .demo-orchestration
# [kill] 2 条 progress 已落库，SIGKILL pid=…
# {"recover": {"interrupted_assignments": 1, "reclaimed_effects": 0, "relaunched": 1}}
# SELFTEST ok=True killed=True completed=2

pytest -q tests/integration/test_ticket_recovery.py --no-cov
# 1 passed

pytest -q tests/integration/test_ticket_recovery.py tests/integration/test_ticket_example.py --no-cov
# 3 passed

pytest -q tests/unit/test_scheduler.py tests/unit/test_orchestration_model.py \
  tests/unit/test_agent_executor.py tests/unit/test_orchestration_service.py --no-cov
# 103 passed
```

四条命令钉住的不是"看起来恢复了"，而是逐格可证伪的断言（**口径**：离线脚本 Provider、注入的时钟与 ID、本地 SQLite、macOS + 项目虚拟环境 Python 3.11.15；本次本机单次实测为 `1 passed in 3.39s`、`3 passed in 4.58s`、`103 passed in 0.54s`，**耗时不是结论，断言数字才是**）：

| 场景 | 精确断言 |
|---|---|
| 示例：父 Ticket 第 2 条 progress 落库时 `SIGKILL`（子 Ticket 尚未发车） | 恢复摘要 `interrupted_assignments=1`；`SELFTEST ok=True killed=True completed=2`；两张票都 `completed` |
| 集成演练：上游已结束、下游跑到一半时 `SIGKILL` | 崩溃前已 `ended` 的 Assignment 恢复后**仍为 `ended`**；最终无任何 `running` 残留；恢复进程只建 1 个 Worker，其 Provider 调用序列恰为 `[3]` |
| 父子依赖与示例 | `RUN_RESULT status=completed tickets=2`；子 Ticket 的第一个 Assignment 晚于父 Ticket 的 `READY`；报告数不少于 3 条 |
| 状态机 / 门控 / 映射 / 领域命令 | 依赖未满足不发车、两槽位上限、被唤醒的票先跑、超时置 `blocked`、`ready` 不是 `completed`、跨 Ticket 报告被拒、默认不落 `THINKING` 报告 |

两个细节决定这个动作能不能稳定复现：

1. **击杀点读的是"第 2 条 progress 报告"，不是 `sleep(2)`**。报告是落了库的结构化事实，而墙钟会随机器负载漂移——这与 05 把 sleeper 做成注入项、02 把 token 变成可测量对象是同一条纪律。断言同样用「**恰好**」：`completed=2` 而不是 ≥2，调用序列是 `[3]` 而不是"不多于 3 次"；只要先结束的 Ticket 被重跑，恢复进程就会多出一条调用记录，动作立刻变红。
2. **单跑测试文件要加 `--no-cov`**：仓库配置了全局覆盖率门槛，只跑单文件会因总覆盖率不足返回非零，测试其实已经全绿。这是 05 已经记过的真实复现坑。

这套动作证明的是"**跨 run 的事实确实在磁盘上**"：内存句柄随 `SIGKILL` 消失，重启后调度器仍能从存储推导出哪些该续、哪些不该重跑、依赖是否已经放开。若哪天有人把门控挪到领取之后，或让恢复重放事件，上面几条命令会立刻变红。

## 七、有没有更好 / 更省的方法

"领域状态机 + outbox + 单循环门控"不是唯一解。把替代方案摊开，才知道它的适用边界：

**1. 一个长 run + 把计划塞进 messages**
最省，一次性任务完全够用：不拆票、不建表、不写调度。缺点：上下文线性增长，恢复粒度是整条 run，无法并行、无法表达依赖，模型还可能"报告"一个并未完成的进度。**只有当目标确实能在一次运行里做完时，它才是更优解**。

**2. 通用工作流引擎 / DAG 调度器**
成熟、可视化、重试与依赖开箱即用，适合确定性作业。缺点：它不知道 Agent 的步边界与快照语义，把 Worker 当普通任务；暂停等人、追加输入、从步边界恢复这些语义仍要自己接，副作用幂等也仍要自己做。当节点是脚本、编译、定时批处理时，它比本文这套更划算。

**3. 消息队列 + 每个子任务一个消费者**
天然跨进程、可水平扩展，背压与重试由队列承担。缺点：整体进度、依赖、优先级、人工验收仍需要一份领域状态，队列只解决"投递"；至少一次投递还要求消费端幂等。它其实是 03 那条 outbox 思路的下游形态：先保证发送端不丢不重，再谈水平扩展。

**4. 事件溯源推导 Ticket 状态**
只存事件、状态靠重放，审计能力最强。缺点：Agent 的事件绑定模型调用与可能的外部副作用，重放既不确定也不安全——04 已经给出"为什么恢复读快照而不是重放事件"的完整理由。适合计算确定的审计领域，不适合以模型调用为步的系统。

**5. 让模型自己维护计划、判断依赖是否完成**
看似最智能，实则最不可验收：状态来自自然语言，等于把调度正确性押在一段文本上。分类必须来自结构化字段（05），依赖闸门同理。

**6. 把编排状态塞进 `AgentState` / checkpoint 快照**
省一张表、少一套版本。缺点：单 run 的快照被跨 run 的字段污染，版本迁移绑死两层，多个 Worker 还会抢同一份快照；"任意时刻切开的状态完整"这条不变式也随之失去定义。**这是最诱人也最难回头的一条近路**。

**7. 全内存调度 + 定期 dump**
实现最快。缺点：崩溃即丢，门控与在途去重无法重算；dump 之间的窗口里发生的事没有任何事实源。只在"进程死了就整体重来"可接受的场景里成立。

**8. 每个 Assignment 一个独立进程 / 容器**
隔离最强，也便于做资源限制。缺点：需要 IPC、进程生命周期管理与跨进程所有权仲裁，复杂度在"编排"之外又长一层。当执行环境本身需要安全隔离时，这是正确方向——那是下一篇《受控执行》的话题，与本文的"该不该起、起几次"正交。

**结论**：当硬约束是「**跨 run 必须可恢复 + 依赖与槽位必须可判定 + 内核默认路径不能被污染**」时，"三层记录 + 集中状态机 + 领取前门控 + 快照恢复接缝"是性价比的甜点：它把复杂度从"每个调度分支的临场发挥"转移到"一次性把状态与所有权定对"。去掉"可恢复"，一个长 run 或全内存调度更省；去掉"依赖与人工交接"，工作流引擎更成熟；规模大到需要水平扩展，就在 outbox 之上叠加队列与进程隔离，但不要因此丢掉"事实源只有一份、断言用恰好"这两条底线。

## 八、写在最后的检查清单

要让长期任务"拆得开、也收得回"，这八条缺一条，半年内就会被悄悄磨掉：

1. **三层记录各司其职**：Ticket 管跨 run 进度、Assignment 管一次执行与 `attempt` 历史、Report 管下游结论；计划可以给模型看，但不能当事实源。
2. **状态只能由一张转移表推进**，非法转移、未知 ID、依赖环、跨 Ticket 报告当场报错；`ready` ≠ `completed`，`interrupted` 属于 Assignment。
3. **依赖与优先级写成可测规则**：上游全 `completed` 才放行，排序键可逐行断言，别靠遍历顺序碰运气。
4. **门控在领取之前**，在途去重、依赖未满足、槽位满都不吃重试预算；驻留等人不占并发槽位；deadline 到期写结构化 `reason`。
5. **内存只留取消与去重句柄**，可运行性与所有权一律从存储推导；同一 run 同一时刻只能有一个驱动者。
6. **恢复只挑 `running`、已 `ended` 不重跑**，重新入队换幂等键；`worker_run_id` 在第一个步边界注册，没启用 checkpoint 的 Worker 拒绝启动。
7. **续跑读快照、不重放事件**，编排层不写 `AgentState`，不复制 Core 快照；两层各自只有一个崩溃窗口定义。
8. **Event → Report 只读结构化字段**，默认只落一条 `progress`，崩溃演练断言「恰好」而非「不超过」。

## 写在最后

本篇回答的是"**一个长期目标怎么拆成多次 Worker 执行，且整体仍可恢复**"：用 Ticket / Assignment / Report 把跨 run 的事实落到磁盘上，用一张状态机决定谁能推进，用领取前的门控守住依赖、槽位与所有权，再把恢复接缝收敛到"读一条快照、不重放事件"。于是 01 管单 run 的进程死亡、05 管单 run 的调用失败、本篇管**多次执行之间的整体进度**；三者共用同一条底线——已完成的工作不重跑。

但编排层回答的始终是"该不该起、起几次、崩了怎么接"。它并不回答"发出来的那条命令本身是否安全可控"：工作目录怎么隔离、命令边界怎么收成 argv 数组 + 精确白名单、授权如何可审计。那是下一篇《受控执行》的主题，与 03 的"不丢不重"正交——**outbox 管动作发出去几次，受控执行管不该发的动作连一次都发不出去**。

**证据与复现**（正文不依赖这些材料即可读懂）：领域状态机与恢复接缝的设计说明见 [编排层设计说明](../design-decisions/04-ticket-orchestration.md)，命令与实测输出见 [编排验收记录](../acceptance/p4-ticket-orchestration.md)，固定规模下的调度耗时口径见 [编排基准记录](../evidence/p4-orchestration-benchmark-2026-11-03.md)；可跑的演示在 `examples/04_ticket_orchestration.py`，断言本体在 `tests/integration/test_ticket_recovery.py`、`tests/integration/test_ticket_example.py` 与 `tests/unit/test_scheduler.py`、`tests/unit/test_orchestration_model.py`、`tests/unit/test_agent_executor.py`、`tests/unit/test_orchestration_service.py`。
