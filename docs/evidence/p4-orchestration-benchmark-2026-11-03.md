# P4 编排基准记录（2026-11-03）

> **状态：Completed evidence（M3.7 / 账本 H7）。** 本文只记录本机固定规模下的量级，
> 不外推为性能承诺；生产超时与并发参数未做任何调整。

## 运行环境与命令

- 日期：2026-11-03
- 环境：macOS 26.5（arm64），Python 3.11.15，项目 `.venv`，本地 SQLite
- 网络/密钥：未使用
- 固定规模：50 Ticket / 100 Assignment（每 Ticket：attempt 1 已 `ended`、attempt 2 可运行）
- 时钟/ID：`BenchClock`（每次 `now_ms()` +1ms）、`BenchIds`（进程内序号）
- Worker：`InstantExecutor`（launch/recover 即 `finish_assignment(END)` + `ticket DELIVER`，零延时）
- 并发：`Scheduler(max_concurrency=50)`；dispatcher 仍按生产默认每轮最多领取 10 条 effect

```bash
.venv/bin/python benchmarks/orchestration_benchmark.py \
  --tickets 50 --assignments 100 --repeat 3 --dir .bench-p4-orchestration
```

临时 SQLite 目录已删除；脚本默认写入 `.bench-p4-orchestration/`（已在 `.gitignore` 中忽略）。

## 原始输出

```text
{"event": "meta", "tickets": 50, "assignments": 100, "repeat": 3}
{"event": "run", "repeat": 0, "tick_ms": 27.623001486063004, "recover_ms": 45.373544096946716, "drain_ms": 124.28891658782959}
{"event": "run", "repeat": 1, "tick_ms": 37.06945665180683, "recover_ms": 38.308752700686455, "drain_ms": 120.63820660114288}
{"event": "run", "repeat": 2, "tick_ms": 34.90333445370197, "recover_ms": 37.88554109632969, "drain_ms": 122.34537489712238}
{"summary": {"event": "summary", "tickets": 50, "assignments": 100, "repeat": 3, "tick_ms": 34.903, "tick_ms_min": 27.623, "tick_ms_max": 37.069, "recover_ms": 38.309, "recover_ms_min": 37.886, "recover_ms_max": 45.374, "drain_ms": 122.345, "drain_ms_min": 120.638, "drain_ms_max": 124.289, "note": "本机量级，不外推为性能承诺"}}
```

## 指标（毫秒）

| 指标 | min | median | max | 口径 |
|---|---:|---:|---:|---|
| `tick_ms` | 27.623 | 34.903 | 37.069 | 单轮 `tick()`：50 个 Ticket 的 enqueue + 最多 10 条 launch effect 的 dispatch 窗口 |
| `recover_ms` | 37.886 | 38.309 | 45.374 | `recover()`：50 个 RUNNING Assignment 转 INTERRUPTED 并各入队一条 relaunch effect |
| `drain_ms` | 120.638 | 122.345 | 124.289 | 重放剩余 effect（原 pending + relaunch 去重后）到队列静默 |

## 读法与边界

- 三个指标都是**本机单进程同步 `sqlite3`** 的量级；`tick_ms` 含 50 次状态更新与至多 10 次
  effect 领取/提交，`drain_ms` 随 dispatcher 每轮 10 条的领取上限分批推进（约 9~10 轮）。
- 第 0 轮偏快（首次进程内 SQLite 页缓存仍冷/热差异），因此以 3 轮的中位数为主、min/max 显示波动；
  波动约 ±10ms 属于本机噪声，不解释为回归。
- 该结果只用于验证 ADR-04「后果与代价」里"同步 SQLite 在 P4 离线规模下不构成问题"的判断，
  不构成 SLA、容量规划或跨机器比较的依据。

## 复跑方式

```bash
.venv/bin/pytest -q tests/unit/test_orchestration_benchmark.py --no-cov
.venv/bin/python benchmarks/orchestration_benchmark.py --tickets 50 --assignments 100 \
  --repeat 3 --dir .bench-p4-orchestration
```
