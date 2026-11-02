# P3 Context Engineering 基准记录（2026-11-02）

> **状态：Completed evidence。** 本文记录 P3 收口时的离线固定语料测量。它不是通用模型质量或真实 Provider token 精度声明。

## 运行环境与命令

- 日期：2026-11-02
- 环境：macOS，Python 3.11.15，项目 `.venv`
- 网络/密钥：未使用
- 固定变量：8 条工具结果事实、4 个压缩场景工具规格、启发式 `TokenCounter`、`keep_recent_messages=4`
- 工具爆炸变量：仅工具数量变化，固定为 200 个 specs

```bash
.venv/bin/python benchmarks/context_compression_benchmark.py --tools 200
.venv/bin/python benchmarks/recovery_time_benchmark.py --steps 12 --repeat 3 --dir .bench-p3-recovery
```

第二条命令的临时 SQLite 文件在记录结果后已删除。

## 压缩质量、估算与校准

`reported_prompt_tokens` 由 benchmark 的独立确定性 reference Provider 口径模拟，用于验证“估算值与 Provider usage 可比较”的测量链路。它不是在线模型 tokenizer，因此 MAE 只描述该固定离线语料下的估算偏差，不能外推到真实模型。

| 策略 | 估算 token | 节省 token | 节省率 | 事实保留率 | tool pair | reference usage | MAE | 估算偏差 |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| none | 1,288 | 0 | 0.00% | 100% | 完整 | 1,368 | 80 | -80 |
| truncation | 460 | 828 | 64.29% | 25% | 完整 | 446 | 14 | +14 |
| summary | 899 | 389 | 30.20% | 100% | 完整 | 1,023 | 124 | -124 |
| hybrid | 629 | 659 | 51.16% | 100% | 完整 | 663 | 34 | -34 |

固定语料中的结论：

- `truncation` 节省最多，但只保留 2/8 条必需事实，不能单独作为质量优先任务的默认策略。
- `summary` 保留全部事实，但仅节省 30.20%。
- `hybrid` 保留全部事实，节省 51.16%，比 `summary` 多节省 270 估算 token；这是 P3 默认优先对证据文本摘录再摘要的量化依据。
- 四种策略都保持完整 tool call/result 配对，未产生孤儿工具消息。

## 工具爆炸场景

固定任务文本中注册 200 个工具 specs，包含 1 个已在本 run 调用的无关 `resident_audit` 工具。

| 指标 | 结果 |
|---|---:|
| 全量工具 token | 8,693 |
| 动态视图保留工具数 | 3 / 200 |
| 裁掉 token | 8,570 |
| 工具 token 节省率 | 98.59% |
| resident 工具保留 | 是 |
| 空集 fallback | 否 |

结果表明相关性筛选没有为了节省 token 丢弃本 run 已使用的工具；召回护栏优先于 `max_visible_tools`。

## Checkpoint 影响

该测量用实际 `Agent`、SQLite checkpoint、4 次工具调用，并从第 3 步 checkpoint 重新恢复。`summary` 的 `ContextEngine.fold()` 会把 `context_summary` 标记消息写入恢复段的 `AgentState.messages`。

| 压缩 | checkpoint 条数 | 最新 payload 字节 | 读取 + 迁移恢复时间 |
|---|---:|---:|---:|
| none | 3 | 4,056 | 0.2372 ms |
| summary | 3 | 2,594 | 0.2724 ms |

摘要使最新 snapshot payload 减少 **1,462 字节（36.05%）**。单次读取测量相差 0.0352 ms，属于本机 SQLite 量级，不可据此声称稳定的恢复延迟变化；后续回归应重复运行并报告中位数与波动范围。

`recovery_time_benchmark.py --steps 12 --repeat 3` 的本次离线基线为：`auto_save_every=0/1/2/5` 的中位端到端时间分别为 0.0017/0.0079/0.0044/0.0033 秒，读取最新 snapshot 的平均恢复时间为 0.147 ms，最大值为 0.477 ms。该脚本未开启 P3 compression，故只作为 P2 存储基线，不与上表混为同一指标。

## 复现与限制

- `tests/unit/test_context_benchmark.py` 断言 benchmark 必须同时输出质量保留、校准、工具 resident 护栏与 checkpoint 影响。
- 任何更改 compression、token 估算、capability 策略、消息配对或 checkpoint 语义，都必须重新运行上述命令并新增日期明确的证据记录。
- 真实 Provider usage 的误差测量需要在不记录密钥或完整 prompt 的前提下，以显式 opt-in 集成测试另行采集。
