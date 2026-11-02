# ADR-01：Loop 执行模型（P1 锁定，P2 起冻结范围的组成部分）

- 状态：Accepted（P1）
- 日期：2026-10-30
- 关联：`00-contracts.md`（契约 v1.1.0）、`docs/acceptance/p1-core-runtime.md`（F1~F5、C2、D1~D4）
- 代码入口：`stableagent/core/loop.py`、`stableagent/core/agent.py`、`stableagent/context/assembly.py`

## 背景

P2 的 checkpoint/恢复与 P3 的 Context Engineering 全部长在 loop 上。若 loop 携带
隐式运行状态或在内部内联拼装上下文，P2/P3 就被迫重写 `core/loop.py`——这是 Roadmap
在 P1 DoD 中明令禁止的。因此 P1 必须把"执行模型 + 接入面"钉死成决策文档。

## 决策

1. **状态外置（C1）**：`AgentState` 是一次运行的唯一事实源；loop 模块零直接 I/O。
   唯一的例外声明：`pause_requested` 是**控制信号**而非运行状态，不进快照、不参与
   重放等价断言。
2. **纯函数转移（F1）**：步转移接口为 `advance(state) -> (AgentState, list[Event])`；
   事件引擎为 `drive(state) -> AsyncIterator[(Event, AgentState)]`。
   drive 选择产出 **(事件, 事件后快照) 对** 而非裸事件，理由有二：
   P2 的 checkpoint 触发点天然就是这里给出的步边界；P1 的 C2 重放测试可直接挂钩。
3. **终止语义（F2/F4）**：`max_steps` 超限 -> `error` 事件 + `FAILED`（比"截断 done"
   更严格，判错面清晰）；`done | error` 每次运行恰好一个且为流末尾（不变式）。
4. **暂停/恢复（F3/F5）**：`pause()` 在步边界生效——刻意与 checkpoint 保存点同构
   （暂停点即可存点）；非法态调用返回 `False`（显式 no-op）；`resume()/aresume()`
   从 PAUSED 快照重新进入 `drive`；暂停期间拒绝新任务（RuntimeError）。
   暂停表达为新增枚举成员 `EventType.PAUSED`（兼容扩展）。
5. **组装位点（D1/D3）**：发给 Provider 的消息经 `ContextAssembler`（ASSEMBLERS
   注册表，默认 passthrough）；工具列表经注入的 `ToolSelector`（默认全量）。
   P3 的压缩/能力视图/预算以"注册新实现"接入，零修改 loop。
6. **usage 穿透（D4）**：`ProviderResponse.usage` 在每步 advance 中累计进
   `AgentState.usage`，作为 P3 预算治理的唯一数据源。
7. **工具异常不抛**：loop 只消费 `Evidence`（契约五），失败以 role=tool 消息回喂
   模型而非中断运行（B4）。

## 备选方案（均否决）

- **事件回调/hook 体系**（LangGraph 风格节点钩子）：hook 顺序与可变状态耦合，破坏
  "快照重建 -> 事件流逐条一致"的重放等价断言。
- **任意时刻 asyncio.cancel 打断**：产生非确定性的中间态，P2 无法定义一致保存点。
  步边界方案牺牲响应速度换确定性。
- **chunk 级流式事件**（provider delta 直通 Event）：会使 A3"三形态等价"无法成立、
  C2 重放失去确定性。token 级流式留给 P6 transport 层在事件流之外另开一条面。

## 后果

- 正面：C2 断点重放等价测试通过（每个步边界参数化）；P2 只需对 drive 对流的 state
  做持久化，不改调用面；P3 注入测试（D2/D3）证明零修改 loop。
- 代价：`Agent.run`（同步入口）内部 `asyncio.run`，不能在有运行中事件循环的环境
  调用（文档已标注，用 arun）；`max_steps -> FAILED` 的语义若 P3+ 要放宽，属于
  契约修订而非扩展，须走 CHANGELOG。
