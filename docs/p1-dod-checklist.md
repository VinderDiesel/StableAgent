# P1（MVP Core Runtime）DoD 与验收测试清单

> 依据：`Roadmap.md` Phase 1（含 P2/P3 预留两条硬性 DoD）+ `docs/design-decisions/00-contracts.md`。
> 使用方式：每条都有"怎么验"，全部勾选即 P1 收口；未达 DoD 不进 P2。
> P1 处于契约修订窗口内：允许修订契约，但必须记 CHANGELOG 并标注破坏性/非破坏性。

---

## 0. 前置约束（开工前先定，写进 loop 的实现）

这些语义 P2/P3 会直接消费，P1 不定清楚，P2 就被迫改 `core/loop.py`（违反 DoD）：

- [x] **F1 步转移接口形态钉死**：loop 主体为 `advance(state) -> (AgentState, list[Event])` 式纯函数
      （或等价形式），loop 内零 I/O、零可变成员状态。
- [x] **F2 `max_steps` 终止语义**：超限是产出 `error` 还是带截断标记的 `done`（二选一，写进 CHANGELOG）。
- [x] **F3 `pause()` 语义**：步边界生效（不是任意时刻打断）；非 RUNNING 态调用为显式 no-op 或报错（二选一）。
- [x] **F4 终止事件唯一性**：`done` 或 `error` 每次运行恰好一个，且为流末尾。
- [x] **F5 暂停事件表达**：`EventType` 现无 paused 成员——新增成员（兼容性扩展）还是复用 checkpoint 事件
      （v1.0.0 事件清单本就含 checkpoint）→ 选定后若属契约变更，记 CHANGELOG。

## A. 功能验收（真实端到端）

- [x] **A1** `python examples/01_basic_research_agent.py` 用真实 OpenAI Provider 跑完一个 **≥3 步**
      含工具调用的研究任务，退出码 0。（已验证 2026-10-30：3 步、2 文件读取、
      usage 2038 tokens、status=succeeded；离线等价：02_loop_offline_demo.py）
- [x] **A2** 事件流完整可打印：`tool_call` 与 `tool_result` 一一对应、`step` 单调不减、满足 F4。
- [x] **A3** 三形态等价：同一 FakeProvider 任务下，`run` 与 `arun` 结果一致；`stream` 事件序列
      与 `run` 内部产生序列逐条一致（golden 对比测试）。
- [x] **A4** OpenAI Provider 流式 function calling：碎片 chunk 重组出**完整合法**的 ToolCall
      （id/name/arguments 在流终止时无截断），含 arguments JSON 跨 chunk 拼接用例。

## B. Loop 分支单测（Roadmap DoD 第 2 条）

测试资产：`FakeProvider`（脚本化 ProviderResponse/Chunk 序列）+ 内存工具，全部离线可跑。

- [x] **B1 无工具**：纯文本回答，一步 `done`，不产生 `tool_call` 事件。
- [x] **B2 多轮工具**：N 轮工具往返，`tool_call`/`tool_result` 对数 == N，历史消息成对追加。
- [x] **B3 提前终止**：`max_steps` 触发（按 F2 语义）、provider 异常（→ `error` 事件）、
      用户取消（→ `error`/PAUSED 按 F3/F5 语义）。
- [x] **B4 失败归一化**：工具抛异常时 loop 收到 `ok=False` Evidence 并回喂模型，loop 不崩溃
      （契约五不变式在 loop 层的消费证明）。
- [x] **B5 状态机合法**：`PENDING→RUNNING→SUCCEEDED|FAILED` 全路径覆盖，无非法跳跃；
      PAUSED 仅经 F3 语义进入。
- [x] **B6 覆盖率门禁**：全库 ≥70%（Roadmap 基线）；`core/loop.py` 单独 ≥90%（心脏代码从严）。

## C. P2 预留验收（Roadmap DoD 第 3 条：可快照、可重入）

- [x] **C1 状态外置**：`AgentState` 为唯一事实源——审查 + 静态检查证明 loop 无隐式内存态
      （loop 模块内不定义 dict/list 型可变全局、不缓存跨步数据）。
- [x] **C2 断点重放等价（核心测试）**：FakeProvider 跑 N 步任务，在第 k 步边界
      `AgentState.loads(s.snapshot())` 重建后继续跑，事件流与不中断跑完**逐条一致**。
      对每个 k ∈ [1, N-1] 参数化执行。
- [x] **C3 pause/resume 接入点存在**：`agent.pause()` / `agent.resume()` 在 P1 为最小实现
      （语义按 F3/F5），P2 只替换持久化层，不改 `core/loop.py` 的调用面。
- [x] **C4 接口冻结检查**：对照 00-contracts.md"P2 起冻结"清单跑一遍不变式测试，全绿。

## D. P3 预留验收（Roadmap DoD 第 4 条：可插拔组装位点）

- [x] **D1 组装位点走配置**：上下文构建经 `ContextConfig.assembly` 解析到可插拔 assembler，
      默认 `passthrough`；loop 不内联拼装 history。
- [x] **D2 注入测试**：注册一个"只保留最近 2 条消息"的自定义 assembler，零修改 `core/loop.py`
      即可改变实际发给 Provider 的 messages（FakeProvider 捕获断言）。
- [x] **D3 能力视图预留**：每轮传给 `Provider.complete/stream` 的 `tools` 列表经由一个可替换的
      选择函数产出（默认返回 `registry.specs()` 全量）——P3 capability filter 的接入点。
- [x] **D4 usage 穿透**：`ProviderResponse.usage` 逐步累计进 `AgentState`（新增带默认值字段，
      属兼容扩展），为 P3 预算治理提供数据源；FakeProvider 验证累计正确。

## E. 工程门禁（质量基线不降）

- [x] **E1** `ruff check` / `ruff format --check` 0 issue；`mypy` 通过（strict 留 P6，逐阶段爬坡）。
- [ ] **E2** CI（3.10/3.12 矩阵）全绿。（状态：四道门禁本地全绿，推送后在远端 CI 验证）
- [x] **E3** 真实 OpenAI 集成测试用 `VCR`/mocked transport 进 CI（离线、确定性）；
      真实调用只跑在 examples，凭据走环境变量，不落库。
- [x] **E4** CHANGELOG 更新：F 节所有语义决定 + 契约修订/扩展逐条标注破坏性/非破坏性。
- [x] **E5** `examples/01_basic_research_agent.py` 可直接对外演示：README 级注释、一条命令跑通
      （Roadmap：本阶段产物 = 最早对外演示）。

---

## 验收顺序建议

0（定语义）→ B（离线测试先行，FakeProvider）→ A（真实 Provider）→ C → D → E。
C2 断点重放等价是 P1→P2 交接的关键证据，建议最先写、最后跑全参数化。
