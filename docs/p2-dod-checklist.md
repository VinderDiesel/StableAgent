# P2（执行可靠性 Checkpoint & Recovery）DoD 与验收测试清单

> 依据：`docs/Roadmap.md` Phase 2 + `docs/design-decisions/00-contracts.md`（P2 起冻结规则）
> + `docs/design-decisions/01-loop-runtime.md`（ADR-01 已冻结的 loop 契约）。
> 使用方式：每条都有"怎么验"，全部勾选即 P2 收口；未达 DoD 不进 P3。

---

## 0. 前置约束（本阶段的硬边界，违反即不算完成）

- [x] **L1 `core/loop.py` 零修改**：checkpoint/恢复全部落在 `core/checkpoint/` + `core/agent.py`
      外层。验收方式：`git diff HEAD~N -- stableagent/core/loop.py` 为空，结果记进本文件。
- [x] **L2 契约只扩展不推翻**：新增内容仅限「带默认值的可选字段 / 新枚举成员 / 新配置节」。
      逐项列进 CHANGELOG 并标注非破坏性。
- [x] **L3 checkpoint 事件在 Agent 外层注入**：消费 `drive()` 产出的 `(事件, 快照)` 对，
      loop 不知道持久化存在（ADR-01 后果节的承诺）。
- [x] **L4 终止事件唯一性在扩展后仍成立**：注入 `checkpoint` / `retry` 事件后，
      `done|error` 依然恰好一个且位于流末尾（F4 不得被 P2 破坏）。
- [x] **L5 存储层统一异步协议**：`CheckpointStore` 只有 `save/load/list/delete`；
      Redis 客户端为注入项，未安装 redis 也能 import 本包。

## A. 端到端演示：运行中 kill → 重启 → 从最近 checkpoint 恢复跑完

- [x] **A1** `examples/03_checkpoint_resume.py --phase run`（SQLite 后端 + 慢速脚本 Provider）
      跑到第 2 步落盘后被外部 `SIGKILL`，退出码非 0，事件流无 `done`。
- [x] **A2** 同脚本 `--phase resume` 读回最近快照，续跑到 `done`，且 `step` 从断点单调递增、
      历史消息数 ≥ 断点时（不重跑已完成步）。
- [x] **A3** 上述两步做成**自动化集成测试** `tests/integration/test_crash_recovery.py`
      （subprocess + `os.kill(pid, SIGKILL)` + 二次 subprocess），CI 离线可跑、确定性。

## B. 三种存储后端（Roadmap DoD：各有集成测试）

- [x] **B1 Memory**：进程内；断言"进程重启即丢失"（新实例读不到），用于单测基线与开发路径。
- [x] **B2 SQLite**：`tmp_path` 文件持久化；关闭后重开同一文件能读回；并发写不报错。
- [x] **B3 Redis**：注入 fake async client（`get/set/del` 协议）跑通；真实 Redis 用例
      由环境变量开关，不可用时 skip 而非 fail。
- [x] **B4 后端一致性**：同一组共享断言（save→load 无损、`list` 按 run_id 隔离、
      按 step 覆盖写、取最新）参数化跑三套后端，结果一致。
- [x] **B5 写入失败不影响运行**：store 抛异常时任务继续跑完，错误只降级为一次告警
      （`error` 不因此产生）。

## C. 恢复策略与失败自愈

- [x] **C1 `resume`**：从最近快照继续，已完成步不重跑；`retry_with_fallback` 之外的路径零重试。
- [x] **C2 `retry_with_fallback`**：可重试错误按指数退避重试至多 `max_attempts` 次；
      配置了 fallback Provider 时按序切换；成功则从断点续跑，耗尽则 `error` 收尾（满足 F4）。
- [x] **C3 错误分类**（`core/errors.py`）：可重试（超时/连接/429/5xx）、
      不可重试（4xx 参数类、代码异常）、需人工（权限/审批类）——每类 ≥1 单测，
      未知错误默认判为不可重试（理由写进 ADR-02）。
- [x] **C4 指数退避确定性**：`RetryPolicy.delays()` 产出 `base * factor^n` 且封顶 `max_delay`；
      `sleep` 函数可注入，断言重试实际等待序列与预算次数完全一致（不真等）。
- [x] **C5 超时控制**：`ProviderConfig.request_timeout_seconds` 贯通到 OpenAI Provider 的
      httpx 超时配置（有断言）；超时错误被分类为可重试并进退避重试。
- [x] **C6 需人工的路径**：分类为 `needs_human` 时运行停在 `PAUSED`（不是 `FAILED`），
      人处理后可 `resume()`——复用 ADR-01 的 F3 语义，不新增状态。

## D. `pause()` / `resume()` 语义完备（持久化层接入）

- [x] **D1** 暂停点必落盘：`auto_save_every=0` 时也必须保存 PAUSED / 终止态快照。
- [x] **D2 跨实例恢复**：另一个 Agent 实例（同 run_id、同配置）从 store 载入快照并 `aresume()` 跑完。
- [x] **D3 兼容 P1 语义**：无 PAUSED 态时 `resume()` 抛 ValueError、暂停期间拒绝新任务抛
      RuntimeError——P1 已有测试保持绿色，不改动调用面（C3 预留兑现）。
- [x] **D4** 恢复后事件流与"不中断跑完"逐条一致（仅多出 `checkpoint`/`retry` 注入事件），
      与 P1 的 C2 重放等价测试互证。

## E. 状态序列化版本化（兼容旧快照）

- [x] **E1 迁移注册表**：`MIGRATIONS` 按 `contract_version` 逐版本升级；缺字段视为
      最早已知版本；**高于当前版本或未知版本 → 显式报错**，绝不静默猜测。
- [x] **E2 v1.0.0 样本迁移**：固定 `tests/fixtures/state_v1_0_0.json` 经 `migrate_state()`
      升到 v1.1.0（`usage={}`、`Message.tool_calls=[]` 等默认补齐），断言可继续驱动。
- [x] **E3 往返无损**：`AgentState.loads(s.snapshot()) == s`（契约二不变式在 P2 仍成立）；
      迁移后的快照再存再读等价。
- [x] **E4 API 不变**：迁移发生在 checkpoint 读取侧（`recovery.migrate_state`），
      `AgentState.loads` 语义保持严格，不改签名与不变式。

## F. 工程门禁（质量基线只升不降）

- [x] **F1 L1 证明**：`core/loop.py` 本阶段 diff 为空，命令与输出记入本文件末尾。
- [x] **F2 CHANGELOG**：P2 全部契约扩展逐条列出并标注非破坏性；
      同步 `00-contracts.md` 增补"P2 扩展记录"小节。
- [x] **F3 门禁**：`ruff check` / `ruff format --check` 0 issue；`mypy` 通过；
      全库覆盖率 ≥70%，`core/checkpoint/` 单独 ≥90%（心脏代码从严）。
- [x] **F4 ADR-02** `docs/design-decisions/02-checkpoint.md`：一致性（步边界写、崩溃窗口）、
      并发（同 run 写锁）、版本迁移策略、三后端取舍、错误分类与重试的边界。
- [ ] **F5 CI**：3.10/3.12 矩阵全绿；SQLite 用例走 `tmp_path` 无残留；
      Redis 用例离线 skip 不 fail；崩溃恢复用例在 CI 里确定性通过。

## G. 配套二并行轨道（Roadmap：P2 期间启动）

- [x] **G1** `docs/blog/01-checkpoint-deep-dive.md` 初稿：《Agent Checkpoint 机制的深度设计与实现》，
      素材直接取 ADR-02 + A3 崩溃恢复实测数据。
- [x] **G2** `benchmarks/recovery_time_benchmark.py`：不同 `auto_save_every` 下的
      快照开销与恢复耗时基线（Roadmap 指标：恢复时间，只对标自家基线）。

---

## 验收顺序建议

0（定边界）→ B（后端与共享一致性）→ E（版本化）→ C/D（恢复与自愈，Agent 外层接线）→
A（崩溃恢复 e2e）→ F（门禁与 ADR）→ G（配套产出）。

A3 崩溃恢复自动化测试是 P2 的核心证据（"生产级"的硬指标），建议接线一完成就先写。

---

## 验收记录（2026-10-30，本机 darwin / Python 3.11.15）

### L1 / F1：`core/loop.py` 零修改的机器证明

```
$ git diff HEAD --stat -- stableagent/core/loop.py | wc -l
0
$ grep -c checkpoint stableagent/core/loop.py
0
```

HEAD = `cd72647`（P1 收口提交）。P2 改动清单（`git diff HEAD --name-only` + untracked）
不含 `core/loop.py`；loop 也不 import `core/checkpoint`（上面第二条 grep 为 0）。

### L5：未安装 redis 也能 import

```
$ python -c "import stableagent.core.checkpoint.storage as s; print(s.RedisCheckpointStore.__name__)"
RedisCheckpointStore        # venv 内没有 redis 包；客户端是注入项（RedisLike 协议）
```

### A1/A2/A3：崩溃恢复

```
$ python examples/03_checkpoint_resume.py --phase selftest --dir .demo-checkpoint
[run] ... {"type": "checkpoint", "data": {"run_id": "0390...", "step": 2, "contract_version": "1.1.0"}}
（外部 SIGKILL，第 3 步未完成）
{"type": "done", "data": {"result": "研究完成：共 2 条发现", "steps": 3}, "step": 3}
RESUME_RESULT status=succeeded steps=3 provider_calls=1
SELFTEST ok=True killed=True final=succeeded
```

`provider_calls=1` = 恢复后只跑了断点之后的第 3 步，已完成步零重跑（A2/C1 的直接证据）。
自动化用例 `tests/integration/test_crash_recovery.py`：2 passed。

### F3：门禁

```
$ ruff check stableagent tests examples benchmarks   → All checks passed!
$ ruff format --check stableagent tests examples     → 48 files already formatted
$ mypy stableagent examples                          → Success: no issues found in 26 source files
$ pytest                                             → 126 passed, 1 skipped
```

覆盖率（心脏代码从严）：全库 **98.2%**（门槛 70%，已写进 `pyproject [tool.coverage.report]
fail_under`）；`checkpoint/manager.py` 100%、`checkpoint/storage.py` 99.3%、
`checkpoint/recovery.py` 99.0%、`errors.py` 98.3%、`core/loop.py` 96.8%（门槛 90%，
新增 CI 步骤逐文件把关）。唯一 skip 是真实 Redis 用例（未设 `STABLEAGENT_REDIS_URL`）。

### G2：开销与恢复基线（12 步 / 3 轮 / SQLite）

| `auto_save_every` | 端到端耗时中位 | 落盘次数 | 相对 =0 |
|---|---|---|---|
| 0 | 1.5 ms | 1 | 1.0× |
| 1 | 6.6 ms | 12 | 4.4× |
| 2 | 3.9 ms | 6 | 2.6× |
| 5 | 2.3 ms | 3 | 1.5× |

单次写入 mean 0.56 ms / p95 0.77 ms；纯序列化 0.003 ms（**开销在落盘 fsync，不在序列化**）；
恢复（读最近快照 + 版本迁移 + 反序列化）mean 0.114 ms / max 0.348 ms，且与历史长度无关。
