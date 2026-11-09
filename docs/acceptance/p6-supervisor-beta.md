# P6 单仓库 Super-Agent Beta 验收账本与实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单仓库内建立常驻 Router 与多 Worker 控制平面：事件去重、Thread 归属、
显式授权建票、上下文订阅、最小监管面，并以 `observe` 模式完成 Beta 演练。

**Architecture:** 新增 `stableagent/supervisor/`，依赖方向为
`supervisor → integrations → execution → control → orchestration → core`。
持久化用 `SupervisorStore(Store)` 子类共享同一 SQLite 连接与 `BEGIN IMMEDIATE` 事务；
Trigger/Decision/Subscription 与 Ticket/Assignment/Effect 可同事务落库。旧包禁止 import
supervisor。

**Tech Stack:** Python 3.10+、Pydantic v2、SQLite（JSON1）、pytest / mypy / ruff。

**Spec:** [P6 实施规格](../specs/p6-single-repository-super-agent-beta.md)

## Global Constraints

- Python 下限 `>=3.10`；行宽 100；`ruff check` 与 `ruff format --check` 全绿。
- `stableagent/core/` 对 P4 收口提交 `234f620` 零 diff（L1）；`orchestration/service.py` 一字不动。
- 依赖方向只有 `supervisor → integrations → execution → control → orchestration → core`；
  旧包 import supervisor 即失败。
- Beta 锁定 `observe`；`auto` 未启用；merge / auto-merge 继续结构性缺席（L6）。
- 外部文本（Issue/PR 正文、评论、CI 日志、Worker 输出）只能是 digest、上下文或 Report，
  不能成为授权来源（L4）。
- 事务体内禁止 `await`（L3）；外部 I/O 在事务外。
- 全量测试与 CI 不依赖网络、真实 GitHub 或 Docker（L8）。
- 门禁命令统一带 `env -u NO_PROXY -u no_proxy`；读数先落 `/tmp/p6-*.txt` 再读回。

---

## 0. P6 出口不变量（违反任意一条即不收口）

| 编号 | 不变量 |
|---|---|
| I1 | Trigger 以 `UNIQUE(source, external_id)` 在数据库去重，payload 不可变 |
| I2 | Thread/Channel 显式映射；Channel 只是目的地，不携带授权 |
| I3 | Subscription 的 `context` 模式不扩大 Mandate，后续输入只提供上下文 |
| I4 | Decision 六种 outcome / 三种 strength；Beta 只记录 observe，不执行推断任务 |
| I5 | Trigger 去重、Thread 更新、cursor 推进、扇出与建票在同一事务内 |
| I6 | 视图只读且不返回 payload / 原始日志正文 |

## 1. 里程碑

### M1：Supervisor 基础层（规格 WP-6.1；ADR-06 决策 1/2/3/4/5/6/7/10）

> **状态：M1 收口（Task 1~8 全部完成，2026-11-08）。** 证据提交：`1281566`（文档）、
> `e7de113`（模型）、`ef03c60`（Trigger/Thread）、`43f15d0`（Channel/Subscription）、
> `cf6af4a`（Decision/SourceCursor）、`c3f9801`（视图）、`2e04a1f`（CI FOCUS）、本收口提交。

- [x] **M1-1** ADR-06 落盘并 Accepted，账本与本计划建立。
      `docs/design-decisions/06-super-agent-router.md` 共 10 项决策、5 个被否决方案；
      `docs/guides/supervisor-migration.md` 写明备份、marker 校验与降级步骤；提交 `1281566`。
- [x] **M1-2** `supervisor/` 包骨架与 `model.py`：六个记录、枚举、canonical digest。
      `tests/unit/test_supervisor_model.py` 5 passed；提交 `e7de113`。
- [x] **M1-3** `SupervisorStore`：七张表、索引、`p6-supervisor-v1` marker、P5 库无损迁移、
      Trigger/Thread CRUD。`tests/unit/test_supervisor_store.py` 首段 8 passed（含
      "supervisor 与 Ticket 同事务回滚"）；提交 `ef03c60`。
- [x] **M1-4** Channel 与 Subscription CRUD：目的地唯一、live 订阅部分唯一、结束可重订。
      累计 11 passed；提交 `43f15d0`。
- [x] **M1-5** Decision（含 trigger 联结与 attempt 重评估）与 SourceCursor CRUD。
      累计 15 passed；提交 `cf6af4a`。
- [x] **M1-6** `views.py`：`needs_attention`、`open_tickets`、`failed_effects`、
      `source_health`、`thread_timeline`、`ticket_timeline`。
      `tests/unit/test_supervisor_views.py` 6 passed；提交 `c3f9801`。
- [x] **M1-7** CI FOCUS 由 25 扩到 28（新增 supervisor model/store/views），契约测试同步。
      `tests/unit/test_ci_focus_gate.py` 1 passed；coverage.xml 实测三文件
      `98.25% / 98.23% / 98.59%`（均 ≥90%）；提交 `2e04a1f`。
- [x] **M1-8** M1 出口：全量门禁、三解释器、L1/约束 1 复核、账本与状态面回填。
      本地读数（落盘 `/tmp/p6-m1-*.txt` 后读回）：`715 passed, 6 skipped, 0 failed`
      （19.10s，覆盖 98.99%）；`ruff check` `All checks passed!`；`ruff format --check`
      `143 files already formatted`；`mypy` `Success: no issues found in 64 source files`；
      3.11.16 与 3.12.14 同为 `715 passed, 6 skipped`；
      `git diff 234f620..HEAD --stat -- stableagent/core/` 与
      `... -- stableagent/orchestration/service.py` 均为 0 行；`git diff --check` 无输出。
      本提交完成账本与状态面回填。

### M2：GitHubSource 与 Trigger 接入（规格 WP-6.2）

> **状态：M2 收口（Task 1~7 全部完成）。** 主路径为 polling；读客户端沿用 P5 的
> "协议 + 确定性 fake" 形态，读权限与写权限分离。任务级计划见 §7。

- [x] **M2-1** `Source` / `ChannelWriter` 协议、Source 错误四分类与 `SourceEvent`/`PollBatch` 记录。
      `tests/unit/test_github_source.py` 起步用例通过；提交 `0c8b9a4`。
- [x] **M2-2** Issue / PR 标准化：稳定 Thread key、`#number` external_ref、bot 身份过滤。
      新增 Issue/PR 与 bot 过滤用例；提交 `082c7d4`。
- [x] **M2-3** comment / review 映射：issue comment、PR comment、review 会话与 reply Channel。
      新增评论/评审与 Channel 用例；提交 `88c2faa`。
- [x] **M2-4** CI status：有 PR 挂 PR Thread，无 PR 用 repository Thread。
      新增 CI 映射用例；提交 `86f8dcb`。
- [x] **M2-5** `poll`：cursor 分页、乱序排序、批内去重、删除事件、临时/403/404/schema 错误。
      `tests/unit/test_github_source.py` 累计 32 passed；提交 `8f5a6b9`。
- [x] **M2-6** 部署文档：polling 路径、只读权限、cursor 契约与 webhook 后续接法。
      `docs/guides/supervisor-github-source.md` 7 节全量落盘。
- [x] **M2-7** CI FOCUS 由 28 扩到 29（新增 `supervisor/source.py`），契约测试同步。
      `tests/unit/test_ci_focus_gate.py` 1 passed；coverage.xml 实测 `supervisor/source.py`
      行覆盖率 `96.27%`（≥90%）；提交 `04d6317`。
- [x] **M2-8** M2 出口：全量门禁、三解释器、L1/约束 1 复核、账本与状态面回填。
      本地读数（落盘 `/tmp/p6-m2-*.txt` 后读回）：`749 passed, 6 skipped, 0 failed`
      （19.73s，覆盖 98.89%）；`ruff check` `All checks passed!`；`ruff format --check`
      `145 files already formatted`；`mypy` `Success: no issues found in 65 source files`；
      3.10 / 3.11.16 / 3.12.14 均为 `749 passed, 6 skipped`；
      `git diff 234f620..HEAD --stat -- stableagent/core/` 与
      `... -- stableagent/orchestration/service.py` 均为 0 行；
      下层包真实 import `stableagent.supervisor` 的 grep 命中 0 行；`git diff --check` 无输出。
      本提交完成账本与状态面回填。

### M3：Router Intake、Subscription 与 Decision（规格 WP-6.3）

> **状态：M3 收口（Task 1~7 全部完成）。** `receive(trigger)`
> 是外部事件进入领域的唯一命令；`observe` 锁定、Channel 不授予权限、context 订阅不扩大
> Mandate。任务级计划见 §9，出口读数见 §9 末尾。

- [x] **M3-1** `AutonomyMode`（`off`/`observe`/`auto`，`auto` 构造期拒绝）与
      `CandidateAssessor` 窄协议；`ObserveOnlyAssessor` 的确定性规则。
      `tests/unit/test_supervisor_assessor.py` 6 passed（六条规则各一例；`auto` 在
      `RouterService.__init__` 抛 `RouterModeError`）；提交 `bbb7342`。
- [x] **M3-2** GitHubSource 为带正文事件补 `payload["request"]`（`text`/`mentioned`/`url`）；
      `SupervisorStore` 增加 `next_decision_attempt` 与 `find_accepted_decision`。
      `tests/unit/test_github_source.py` 新增 2 例（mention 大小写不敏感、CI 事件无 request
      键）；`tests/unit/test_supervisor_store.py` 新增 2 例（按 digest 递增 attempt、跳过
      OBSERVE 只认 ACCEPT）；提交 `6d98962`。
- [x] **M3-3** `receive()`：Trigger 去重、Thread 解析、live Subscription 扇出（每 Ticket
      每事件一条 Input + deliver effect）、`off` 忽略与 `observe` 只记录。
      `tests/unit/test_supervisor_router.py` 前 20 例（duplicate 不新增 Decision/Ticket、
      扇出与 sequence 递增、`off` 不调评估器）；提交 `7fa4f79`。
- [x] **M3-4** explicit 准入：未准入 IGNORE；准入后 Mandate/Ticket/Input/Assignment/
      launch effect/own Subscription 与 Decision 同事务；malformed 与 repository 不匹配稳定拒绝。
      同文件覆盖五件原子写与两类稳定拒绝；提交 `7fa4f79`。
- [x] **M3-5** 归属语义：own Thread 后续消息 ATTACH、重复 `request_digest` ATTACH、
      已 stop 的 STOP、已结束的 OBSERVE 且保留 Input、多个 owner 的 `ambiguous_owner`。
      同文件覆盖五种归属分支（含终态订阅不扇出）；提交 `7fa4f79`。
- [x] **M3-6** `unavailable` 与 `reevaluate`：评估器异常只记 `unavailable`；重评估不重复扇出。
      `test_supervisor_router.py` 后 3 例（异常转 `unavailable`/`assessor_error`、
      `reevaluate` 追加 attempt 且不新增 Trigger/Input、未知 trigger 抛
      `RouterNotFoundError`）；提交 `9e2f1c3`。
- [x] **M3-7** 命令层：attach/subscribe/unsubscribe/stop（Task 5）与 handback/resume/
      `record_pull_request`（Task 6）；全部检查 operator、Ticket 状态与 Mandate 复检。
      `tests/unit/test_supervisor_service.py` 22 passed（stop 保留 own 护栏、handback 降级
      可开新 Ticket、resume 仅 COMPLETED/FAILED 且 `revalidate(when="launch")` + 审计、
      PR 报告升 own）；提交 `d5093f9`、`d9aabc3`。
- [x] **M3-8** CI FOCUS 29→32、`docs/guides/supervisor-router.md`、全量门禁与账本回填。
      `tests/unit/test_ci_focus_gate.py` 1 passed（32 文件、无通配兜底）；出口读数见 §9。

### M4：常驻调度、恢复与运行控制（规格 WP-6.4；待 M3 收口后展开）

### M5：最小监管面与注意力协议（规格 WP-6.5；待 M4 收口后展开）

### M6：Beta 演练与发布门禁（规格 WP-6.6；待 M5 收口后展开）

## 2. P6 退出清单（规格 §4）

- [ ] GitHub Source 将所有支持事件确定性映射为 Trigger/Thread/Channel，并持久化
      cursor/去重状态。
- [ ] Router 是 Trigger、Decision、Subscription 与 Ticket 创建的唯一写入口。
- [ ] 所有显式任务经过 P5 Policy/Mandate；推断候选在 `observe` 只记录、不执行。
- [ ] Ticket 后续评论、PR 和 CI 输入正确归属，且不会因此扩大授权。
- [ ] Router、Source、Outbox、Scheduler 和 Worker 的崩溃恢复不丢失或重复语义副作用。
- [ ] 监管入口以待人工事项为首要视图，所有外部消息符合注意力预算。
- [ ] 单仓库 Beta 有 dry-run 记录、明确风险边界、回滚步骤和可审计数据。
- [ ] 所有离线门禁通过；新增 supervisor 核心模块的 CI 覆盖率 ≥90%。

## 3. 阶段验证命令（收口前须在 CI 复现）

```bash
.venv/bin/pytest -q tests/unit/test_supervisor_model.py tests/unit/test_supervisor_store.py
.venv/bin/pytest -q tests/unit/test_supervisor_views.py tests/unit/test_ci_focus_gate.py
.venv/bin/pytest -q tests/unit/test_supervisor_assessor.py tests/unit/test_supervisor_router.py \
  tests/unit/test_supervisor_service.py
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
git diff 234f620..HEAD --stat -- stableagent/core/   # 必须 0 行（L1）
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py   # 必须 0 行
```

## 4. 任务级实施计划说明

M1 展开为 **8 个 Task / 47 个 Step**，M2 展开为 **7 个 Task / 43 个 Step**，M3 展开为
**7 个 Task / 38 个 Step**。每个 Task 独立可验收、独立提交；Task 之间的接口在各自
`Interfaces` 块写清。任务级 Step 不计入里程碑步骤。

## 5. M1 任务级实施计划（writing-plans 产出，2026-11-08）

### Task 1：ADR-06 与 P6 账本落盘（M1-1）

**Files:**
- Create: `docs/design-decisions/06-super-agent-router.md`
- Create: `docs/acceptance/p6-supervisor-beta.md`
- Create: `docs/guides/supervisor-migration.md`

**Interfaces:**
- Consumes: P6 spec §2/§3、ADR-04/05 的决策风格。
- Produces: 后续 Task 引用的决策编号（ADR-06 决策 1~10）、验收条目（M1-1~M1-8）
  与运维迁移口径（备份、marker 检查、降级）。

- [ ] **Step 1: 落 ADR-06**

内容以本仓库 ADR-05 的章节顺序组织：背景、约束、决策、被否决方案、后果与代价、
测试与证据、阶段交接。决策必须覆盖持久化分层、Trigger 去重、Thread/Channel、
Subscription、Decision、事务边界、Source/Channel 协议、注意力预算、迁移降级。

- [ ] **Step 2: 落本账本与计划**

写入第 0~5 节；本文件就是 P6 的验收账本与 M1 计划，不再另建计划文件。

- [ ] **Step 3: 落迁移运维文档（规格 WP-6.1 第 5 条）**

`docs/guides/supervisor-migration.md` 必须写明：

```markdown
# Supervisor 迁移与降级

## 升级（P5 → P6）

1. 停止进程，备份 SQLite 文件：`cp state.db state.db.bak-$(date +%Y%m%d%H%M%S)`。
2. 用 P6 二进制启动一次；`SupervisorStore` 只新增表、索引与 `p6-supervisor-v1` marker。
3. 校验 marker：`SELECT version FROM schema_migrations ORDER BY applied_at, rowid;`
   应同时包含 `p5-control-v1` 与 `p6-supervisor-v1`。

## 降级（P6 → P5）

1. 停止进程并再次备份。
2. P5 二进制直接打开同一文件即可读取原有表；新增的 supervisor 表被忽略。
3. 不得删除 `schema_migrations` 中的 `p6-supervisor-v1`；再次升级必须幂等。
4. 若需回到升级前状态，用第 1 步的备份整库恢复，不做逐表删除。
```

- [ ] **Step 4: 校验链接与占位符**

Run: `grep -n 'TBD\|TODO\|待补' docs/design-decisions/06-super-agent-router.md docs/acceptance/p6-supervisor-beta.md docs/guides/supervisor-migration.md`
Expected: 只命中本命令行自身那一行；其余三份文档无占位符。

- [ ] **Step 5: 提交**

```bash
git add docs/design-decisions/06-super-agent-router.md docs/acceptance/p6-supervisor-beta.md docs/guides/supervisor-migration.md
git commit -m "docs: accept adr-06 and open the p6 m1 plan"
```

---

### Task 2：supervisor 包骨架与领域模型（M1-2）

**Files:**
- Create: `stableagent/supervisor/__init__.py`
- Create: `stableagent/supervisor/model.py`
- Test: `tests/unit/test_supervisor_model.py`

**Interfaces:**
- Consumes: Pydantic v2（仓库已依赖 `pydantic>=2.7`）。
- Produces:
  - `SUPERVISOR_SCHEMA_VERSION = "p6-supervisor-v1"`
  - `canonical_json(value: Any) -> str`
  - `payload_digest(payload: dict[str, Any]) -> str`
  - `TriggerKind`、`ThreadKind`、`ThreadStatus`、`ChannelKind`、`SubscriptionMode`、
    `DecisionOutcome`、`DecisionStrength`、`SourceHealth`
  - `Trigger`、`Thread`、`Channel`、`Subscription`、`Decision`、`SourceCursor`

- [ ] **Step 1: 写失败测试**

```python
"""P6 领域记录：digest 规范化、Trigger 不可变、枚举值钉死。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
    canonical_json,
    payload_digest,
)


def _trigger(**overrides: object) -> Trigger:
    data: dict[str, object] = {
        "id": "tg-0001",
        "external_id": "issue:1",
        "kind": TriggerKind.ISSUE,
        "actor": "alice",
        "thread_keys": ["issue:acme/widgets#1"],
        "occurred_at": 1_700_000_000_000,
        "received_at": 1_700_000_000_001,
        "payload": {"number": 1, "title": "add rate limiter"},
    }
    data.update(overrides)
    return Trigger(**data)


def test_canonical_json_and_digest_are_order_independent() -> None:
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert payload_digest({"b": 2, "a": 1}) == payload_digest({"a": 1, "b": 2})
    assert payload_digest({"a": 1}) != payload_digest({"a": 2})


def test_trigger_fills_digest_and_rejects_mismatch() -> None:
    trigger = _trigger()
    assert trigger.digest == payload_digest({"number": 1, "title": "add rate limiter"})
    with pytest.raises(ValidationError):
        _trigger(digest="0" * 64)


def test_trigger_is_frozen_and_forbids_extra_fields() -> None:
    trigger = _trigger()
    with pytest.raises(ValidationError):
        trigger.actor = "mallory"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _trigger(unexpected=True)


def test_enum_values_are_pinned() -> None:
    assert {k.value for k in TriggerKind} == {
        "issue",
        "pull_request",
        "issue_comment",
        "review_comment",
        "review",
        "ci_status",
    }
    assert {k.value for k in DecisionOutcome} == {
        "accept",
        "observe",
        "ignore",
        "attach",
        "stop",
        "unavailable",
    }
    assert {k.value for k in DecisionStrength} == {"explicit", "inferred", "not_admitted"}
    assert {k.value for k in SubscriptionMode} == {"own", "context"}
    assert {k.value for k in SourceHealth} == {"ok", "degraded", "failed"}


def test_records_have_safe_defaults() -> None:
    thread = Thread(
        key="issue:acme/widgets#1",
        repository="acme/widgets",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=1,
    )
    assert thread.status is ThreadStatus.OPEN
    channel = Channel(
        id="ch-1", thread_key=thread.key, kind=ChannelKind.ISSUE_COMMENT, external_ref="#1"
    )
    assert channel.kind is ChannelKind.ISSUE_COMMENT
    subscription = Subscription(
        id="sb-1",
        ticket_id="tk-1",
        thread_key=thread.key,
        mode=SubscriptionMode.OWN,
        created_at=1,
    )
    assert subscription.ended_at is None
    decision = Decision(
        id="dc-1",
        trigger_ids=["tg-0001"],
        outcome=DecisionOutcome.OBSERVE,
        strength=DecisionStrength.INFERRED,
        policy_version="p6-v1",
        request_digest="d",
        created_at=1,
    )
    assert decision.reason_codes == [] and decision.mandate_id is None and decision.attempt == 1
    cursor = SourceCursor(source="github")
    assert cursor.health is SourceHealth.OK and cursor.consecutive_failures == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_model.py --no-cov`
Expected: FAIL，`ModuleNotFoundError: No module named 'stableagent.supervisor'`。

- [ ] **Step 3: 实现包与模型**

`stableagent/supervisor/__init__.py`：

```python
"""P6 控制平面：常驻 Router、Trigger/Thread/Subscription 与只读监管视图。

方向锁：supervisor → integrations → execution → control → orchestration → core；
旧包禁止 import 本包。记录结构在 P6 Beta 前不承诺稳定（ADR-06 决策 1）。
"""

from __future__ import annotations

SUPERVISOR_SCHEMA_VERSION = "p6-supervisor-v1"

__all__ = ["SUPERVISOR_SCHEMA_VERSION"]
```

`stableagent/supervisor/model.py`：

```python
"""P6 领域记录。Trigger 不可变；时间用毫秒整数；ID 由调用方注入。"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def canonical_json(value: Any) -> str:
    """跨进程稳定的 JSON 写法：键有序、无多余空白、非 ASCII 原样保留。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class TriggerKind(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    ISSUE_COMMENT = "issue_comment"
    REVIEW_COMMENT = "review_comment"
    REVIEW = "review"
    CI_STATUS = "ci_status"


class ThreadKind(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    REVIEW = "review"


class ThreadStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    STOPPED = "stopped"


class ChannelKind(str, Enum):
    ISSUE_COMMENT = "issue_comment"
    PULL_REQUEST_COMMENT = "pull_request_comment"
    REVIEW_REPLY = "review_reply"


class SubscriptionMode(str, Enum):
    OWN = "own"
    CONTEXT = "context"


class DecisionOutcome(str, Enum):
    ACCEPT = "accept"
    OBSERVE = "observe"
    IGNORE = "ignore"
    ATTACH = "attach"
    STOP = "stop"
    UNAVAILABLE = "unavailable"


class DecisionStrength(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    NOT_ADMITTED = "not_admitted"


class SourceHealth(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class Trigger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source: Literal["github"] = "github"
    external_id: str
    kind: TriggerKind
    actor: str
    thread_keys: list[str] = Field(default_factory=list)
    occurred_at: int
    received_at: int
    payload: dict[str, Any]
    digest: str = ""

    @model_validator(mode="before")
    @classmethod
    def _seal_digest(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return data
        expected = payload_digest(payload)
        supplied = data.get("digest") or expected
        if supplied != expected:
            raise ValueError("trigger digest does not match payload")
        return {**data, "digest": supplied}


class Thread(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    source: Literal["github"] = "github"
    repository: str
    kind: ThreadKind
    external_ref: str
    last_trigger_at: int
    status: ThreadStatus = ThreadStatus.OPEN


class Channel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    thread_key: str
    kind: ChannelKind
    external_ref: str


class Subscription(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    ticket_id: str
    thread_key: str
    mode: SubscriptionMode
    created_at: int
    ended_at: int | None = None


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    trigger_ids: list[str]
    outcome: DecisionOutcome
    strength: DecisionStrength
    reason_codes: list[str] = Field(default_factory=list)
    policy_version: str
    mandate_id: str | None = None
    request_digest: str
    attempt: int = Field(default=1, ge=1)
    created_at: int


class SourceCursor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    cursor: str | None = None
    last_success_at: int | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    health: SourceHealth = SourceHealth.OK
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_model.py --no-cov`
Expected: PASS（5 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_model.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_model.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor tests/unit/test_supervisor_model.py
git commit -m "feat: add supervisor records with canonical trigger digests"
```

---

### Task 3：SupervisorStore、迁移与 Trigger/Thread 仓储（M1-3）

**Files:**
- Create: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_supervisor_store.py`

**Interfaces:**
- Consumes: `Store`（`stableagent.orchestration.store`）、Task 2 的全部记录类型。
- Produces:
  - `SupervisorStore(Store)`；marker `p6-supervisor-v1`
  - `insert_trigger(trigger: Trigger) -> bool`
  - `get_trigger(trigger_id: str) -> Trigger | None`
  - `get_trigger_by_external(source: str, external_id: str) -> Trigger | None`
  - `list_triggers(*, thread_key: str | None = None, limit: int = 100) -> list[Trigger]`
  - `upsert_thread(thread: Thread) -> None`
  - `get_thread(key: str) -> Thread | None`
  - `list_threads(*, limit: int = 100) -> list[Thread]`

- [ ] **Step 1: 写失败测试**

```python
"""SupervisorStore：P5 库无损扩展、Trigger 去重、Thread 单调状态。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stableagent.orchestration.model import Mandate, Ticket
from stableagent.orchestration.store import Store
from stableagent.supervisor import SUPERVISOR_SCHEMA_VERSION
from stableagent.supervisor.model import (
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock


def _trigger(external_id: str = "issue:1", *, id: str = "tg-0001") -> Trigger:
    return Trigger(
        id=id,
        external_id=external_id,
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"number": 1, "title": "add rate limiter"},
    )


def _thread(*, at: int = 10, status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    return Thread(
        key="issue:acme/widgets#1",
        repository="acme/widgets",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=at,
        status=status,
    )


@pytest.fixture
def store(tmp_path: Path) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=FixedClock())


def test_supervisor_schema_extends_a_p5_store_without_touching_it(tmp_path: Path) -> None:
    path = tmp_path / "p6.db"
    clock = FixedClock()
    p5 = Store(path, clock=clock)
    p5.insert_mandate(Mandate(id="md-1", granted_at=clock.now_ms()))
    p5.insert_ticket(
        Ticket(id="tk-1", title="t", task="t", created_at=clock.now_ms(), mandate_id="md-1")
    )
    p5.close()

    store = SupervisorStore(path, clock=clock)
    try:
        assert store.schema_version() == SUPERVISOR_SCHEMA_VERSION
        assert store.get_ticket("tk-1") is not None
        versions = {
            row[0]
            for row in store._conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert {"p5-control-v1", SUPERVISOR_SCHEMA_VERSION} <= versions
    finally:
        store.close()


def test_supervisor_schema_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "p6.db"
    first = SupervisorStore(path, clock=FixedClock())
    first.close()
    second = SupervisorStore(path, clock=FixedClock())
    try:
        assert second.schema_version() == SUPERVISOR_SCHEMA_VERSION
        assert second.list_triggers() == []
    finally:
        second.close()


def test_trigger_insert_is_idempotent_by_external_id(store: SupervisorStore) -> None:
    assert store.insert_trigger(_trigger()) is True
    assert store.insert_trigger(_trigger(id="tg-9999")) is False
    assert [t.id for t in store.list_triggers()] == ["tg-0001"]


def test_trigger_round_trip_preserves_payload_and_digest(store: SupervisorStore) -> None:
    trigger = _trigger()
    store.insert_trigger(trigger)
    loaded = store.get_trigger("tg-0001")
    assert loaded == trigger
    assert store.get_trigger_by_external("github", "issue:1") == trigger
    assert store.get_trigger_by_external("github", "issue:404") is None


def test_list_triggers_filters_by_thread_key(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    other = _trigger(external_id="issue:2", id="tg-0002").model_copy(
        update={"thread_keys": ["issue:acme/widgets#2"]}
    )
    store.insert_trigger(other)
    assert [t.id for t in store.list_triggers(thread_key="issue:acme/widgets#1")] == ["tg-0001"]


def test_thread_upsert_is_monotonic_for_time_and_status(store: SupervisorStore) -> None:
    store.upsert_thread(_thread(at=10))
    store.upsert_thread(_thread(at=20, status=ThreadStatus.CLOSED))
    store.upsert_thread(_thread(at=15, status=ThreadStatus.OPEN))
    loaded = store.get_thread("issue:acme/widgets#1")
    assert loaded is not None
    assert loaded.last_trigger_at == 20
    assert loaded.status is ThreadStatus.CLOSED


def test_thread_external_ref_is_unique_per_source_and_repository(store: SupervisorStore) -> None:
    store.upsert_thread(_thread())
    duplicate = _thread().model_copy(update={"key": "issue:acme/widgets#1-dup"})
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_thread(duplicate)


def test_supervisor_and_ticket_writes_share_one_transaction(store: SupervisorStore) -> None:
    """ADR-06 决策 7：Trigger 去重与 Ticket 创建必须同生共死。"""
    with pytest.raises(RuntimeError), store.transaction():
        store.insert_trigger(_trigger())
        store.insert_ticket(
            Ticket(id="tk-tx", title="t", task="t", created_at=1, mandate_id="md-none")
        )
        raise RuntimeError("rollback")
    assert store.get_trigger("tg-0001") is None
    assert store.get_ticket("tk-tx") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py --no-cov`
Expected: FAIL，`ModuleNotFoundError: No module named 'stableagent.supervisor.store'`。

- [ ] **Step 3: 实现 Store 子类、建表与 Trigger/Thread CRUD**

`stableagent/supervisor/store.py` 的首段（本 Task 只实现 Trigger/Thread；Channel 等在
Task 4/5 追加到同一文件）：

```python
"""Supervisor 持久化。表、索引与 CRUD 全部在本包，orchestration 不知道 supervisor。

与 P4 Store 共用连接与 BEGIN IMMEDIATE，因此 Trigger/Decision/Subscription 和
Ticket/Assignment/Effect 可以在同一事务内落库（规格 2.1、ADR-06 决策 2/7）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from stableagent.orchestration.model import Clock
from stableagent.orchestration.store import Store
from stableagent.supervisor import SUPERVISOR_SCHEMA_VERSION
from stableagent.supervisor.model import (
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)

_SUPERVISOR_SCHEMA: dict[str, str] = {
    "triggers": (
        "CREATE TABLE IF NOT EXISTS triggers ("
        " id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, actor TEXT NOT NULL,"
        " thread_keys TEXT NOT NULL DEFAULT '[]',"
        " occurred_at INTEGER NOT NULL, received_at INTEGER NOT NULL,"
        " payload TEXT NOT NULL, digest TEXT NOT NULL,"
        " UNIQUE (source, external_id))"
    ),
    "threads": (
        "CREATE TABLE IF NOT EXISTS threads ("
        " key TEXT PRIMARY KEY, source TEXT NOT NULL, repository TEXT NOT NULL,"
        " kind TEXT NOT NULL, external_ref TEXT NOT NULL,"
        " last_trigger_at INTEGER NOT NULL, status TEXT NOT NULL,"
        " UNIQUE (source, repository, kind, external_ref))"
    ),
}

_SUPERVISOR_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS triggers_received ON triggers (received_at, id)",
    "CREATE INDEX IF NOT EXISTS threads_last_trigger ON threads (last_trigger_at, key)",
)


def _row_to_trigger(row: sqlite3.Row) -> Trigger:
    return Trigger(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        kind=TriggerKind(row["kind"]),
        actor=row["actor"],
        thread_keys=json.loads(row["thread_keys"]),
        occurred_at=row["occurred_at"],
        received_at=row["received_at"],
        payload=json.loads(row["payload"]),
        digest=row["digest"],
    )


def _row_to_thread(row: sqlite3.Row) -> Thread:
    return Thread(
        key=row["key"],
        source=row["source"],
        repository=row["repository"],
        kind=ThreadKind(row["kind"]),
        external_ref=row["external_ref"],
        last_trigger_at=row["last_trigger_at"],
        status=ThreadStatus(row["status"]),
    )


class SupervisorStore(Store):
    """P4 Store 的增量扩展：同一连接、同一事务、只加表。"""

    def __init__(self, db_path: str | Path, *, clock: Clock) -> None:
        super().__init__(db_path, clock=clock)
        with self.transaction():
            for ddl in _SUPERVISOR_SCHEMA.values():
                self._conn.execute(ddl)
            for ddl in _SUPERVISOR_INDEXES:
                self._conn.execute(ddl)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (SUPERVISOR_SCHEMA_VERSION, self._clock.now_ms()),
            )

    def insert_trigger(self, trigger: Trigger) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO triggers"
            " (id, source, external_id, kind, actor, thread_keys,"
            " occurred_at, received_at, payload, digest)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trigger.id,
                trigger.source,
                trigger.external_id,
                trigger.kind.value,
                trigger.actor,
                json.dumps(trigger.thread_keys, sort_keys=True, ensure_ascii=False),
                trigger.occurred_at,
                trigger.received_at,
                json.dumps(trigger.payload, sort_keys=True, ensure_ascii=False),
                trigger.digest,
            ),
        )
        return int(cursor.rowcount) == 1

    def get_trigger(self, trigger_id: str) -> Trigger | None:
        row = self._conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        return _row_to_trigger(row) if row else None

    def get_trigger_by_external(self, source: str, external_id: str) -> Trigger | None:
        row = self._conn.execute(
            "SELECT * FROM triggers WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        return _row_to_trigger(row) if row else None

    def list_triggers(self, *, thread_key: str | None = None, limit: int = 100) -> list[Trigger]:
        if thread_key is None:
            rows = self._conn.execute(
                "SELECT * FROM triggers ORDER BY received_at ASC, id ASC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT t.* FROM triggers t"
                " WHERE EXISTS (SELECT 1 FROM json_each(t.thread_keys) WHERE value = ?)"
                " ORDER BY t.received_at ASC, t.id ASC LIMIT ?",
                (thread_key, limit),
            ).fetchall()
        return [_row_to_trigger(row) for row in rows]

    def upsert_thread(self, thread: Thread) -> None:
        self._conn.execute(
            "INSERT INTO threads (key, source, repository, kind, external_ref,"
            " last_trigger_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET"
            " last_trigger_at = MAX(threads.last_trigger_at, excluded.last_trigger_at),"
            " status = CASE"
            "   WHEN threads.status = 'stopped' THEN 'stopped'"
            "   WHEN excluded.status = 'stopped' THEN 'stopped'"
            "   WHEN threads.status = 'closed' AND excluded.status = 'open' THEN 'closed'"
            "   ELSE excluded.status END",
            (
                thread.key,
                thread.source,
                thread.repository,
                thread.kind.value,
                thread.external_ref,
                thread.last_trigger_at,
                thread.status.value,
            ),
        )

    def get_thread(self, key: str) -> Thread | None:
        row = self._conn.execute("SELECT * FROM threads WHERE key = ?", (key,)).fetchone()
        return _row_to_thread(row) if row else None

    def list_threads(self, *, limit: int = 100) -> list[Thread]:
        rows = self._conn.execute(
            "SELECT * FROM threads ORDER BY last_trigger_at DESC, key ASC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_thread(row) for row in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py --no-cov`
Expected: PASS（8 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/store.py tests/unit/test_supervisor_store.py
git commit -m "feat: persist supervisor triggers and threads on the p4 store"
```

---

### Task 4：Channel 与 Subscription 仓储（M1-4）

**Files:**
- Modify: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_supervisor_store.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `SupervisorStore`。
- Produces:
  - `insert_channel(channel: Channel) -> bool`
  - `list_channels(thread_key: str) -> list[Channel]`
  - `insert_subscription(subscription: Subscription) -> bool`
  - `end_subscription(subscription_id: str, *, ended_at: int) -> int`
  - `list_subscriptions(*, ticket_id: str | None = None, thread_key: str | None = None, live_only: bool = False) -> list[Subscription]`

- [ ] **Step 1: 追加失败测试**

```python
def test_channel_is_unique_per_destination(store: SupervisorStore) -> None:
    channel = Channel(
        id="ch-1",
        thread_key="issue:acme/widgets#1",
        kind=ChannelKind.ISSUE_COMMENT,
        external_ref="#1",
    )
    assert store.insert_channel(channel) is True
    duplicate = channel.model_copy(update={"id": "ch-2"})
    assert store.insert_channel(duplicate) is False
    assert [c.id for c in store.list_channels(channel.thread_key)] == ["ch-1"]


def test_live_subscription_is_idempotent_and_can_be_resubscribed(
    store: SupervisorStore,
) -> None:
    live = Subscription(
        id="sb-1",
        ticket_id="tk-1",
        thread_key="issue:acme/widgets#1",
        mode=SubscriptionMode.OWN,
        created_at=10,
    )
    assert store.insert_subscription(live) is True
    assert store.insert_subscription(live.model_copy(update={"id": "sb-2"})) is False
    assert store.end_subscription("sb-1", ended_at=20) == 1
    assert store.end_subscription("sb-1", ended_at=21) == 0
    assert store.insert_subscription(live.model_copy(update={"id": "sb-3"})) is True
    live_rows = store.list_subscriptions(ticket_id="tk-1", live_only=True)
    assert [s.id for s in live_rows] == ["sb-3"]


def test_subscription_list_filters_by_thread_and_mode(store: SupervisorStore) -> None:
    for index, mode in enumerate((SubscriptionMode.OWN, SubscriptionMode.CONTEXT), start=1):
        store.insert_subscription(
            Subscription(
                id=f"sb-{index}",
                ticket_id="tk-1",
                thread_key="issue:acme/widgets#1",
                mode=mode,
                created_at=index,
            )
        )
    assert len(store.list_subscriptions(thread_key="issue:acme/widgets#1")) == 2
    assert [s.mode for s in store.list_subscriptions(ticket_id="tk-1")] == [
        SubscriptionMode.OWN,
        SubscriptionMode.CONTEXT,
    ]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py -k 'channel or subscription' --no-cov`
Expected: FAIL，`AttributeError: 'SupervisorStore' object has no attribute 'insert_channel'`。

- [ ] **Step 3: 追加表、索引与 CRUD**

在 `_SUPERVISOR_SCHEMA` 中追加：

```python
    "channels": (
        "CREATE TABLE IF NOT EXISTS channels ("
        " id TEXT PRIMARY KEY, thread_key TEXT NOT NULL, kind TEXT NOT NULL,"
        " external_ref TEXT NOT NULL,"
        " UNIQUE (thread_key, kind, external_ref))"
    ),
    "subscriptions": (
        "CREATE TABLE IF NOT EXISTS subscriptions ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, thread_key TEXT NOT NULL,"
        " mode TEXT NOT NULL, created_at INTEGER NOT NULL, ended_at INTEGER)"
    ),
```

在 `_SUPERVISOR_INDEXES` 中追加：

```python
    "CREATE INDEX IF NOT EXISTS subscriptions_thread ON subscriptions (thread_key, ended_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_live"
    " ON subscriptions (ticket_id, thread_key, mode) WHERE ended_at IS NULL",
```

在模块级追加行转换器：

```python
def _row_to_channel(row: sqlite3.Row) -> Channel:
    return Channel(
        id=row["id"],
        thread_key=row["thread_key"],
        kind=ChannelKind(row["kind"]),
        external_ref=row["external_ref"],
    )


def _row_to_subscription(row: sqlite3.Row) -> Subscription:
    return Subscription(
        id=row["id"],
        ticket_id=row["ticket_id"],
        thread_key=row["thread_key"],
        mode=SubscriptionMode(row["mode"]),
        created_at=row["created_at"],
        ended_at=row["ended_at"],
    )
```

在 `SupervisorStore` 末尾追加：

```python
    def insert_channel(self, channel: Channel) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO channels (id, thread_key, kind, external_ref)"
            " VALUES (?, ?, ?, ?)",
            (channel.id, channel.thread_key, channel.kind.value, channel.external_ref),
        )
        return int(cursor.rowcount) == 1

    def list_channels(self, thread_key: str) -> list[Channel]:
        rows = self._conn.execute(
            "SELECT * FROM channels WHERE thread_key = ? ORDER BY id ASC", (thread_key,)
        ).fetchall()
        return [_row_to_channel(row) for row in rows]

    def insert_subscription(self, subscription: Subscription) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO subscriptions"
            " (id, ticket_id, thread_key, mode, created_at, ended_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                subscription.id,
                subscription.ticket_id,
                subscription.thread_key,
                subscription.mode.value,
                subscription.created_at,
                subscription.ended_at,
            ),
        )
        return int(cursor.rowcount) == 1

    def end_subscription(self, subscription_id: str, *, ended_at: int) -> int:
        cursor = self._conn.execute(
            "UPDATE subscriptions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (ended_at, subscription_id),
        )
        return int(cursor.rowcount)

    def list_subscriptions(
        self,
        *,
        ticket_id: str | None = None,
        thread_key: str | None = None,
        live_only: bool = False,
    ) -> list[Subscription]:
        clauses, params = [], []
        if ticket_id is not None:
            clauses.append("ticket_id = ?")
            params.append(ticket_id)
        if thread_key is not None:
            clauses.append("thread_key = ?")
            params.append(thread_key)
        if live_only:
            clauses.append("ended_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM subscriptions {where} ORDER BY created_at ASC, id ASC", params
        ).fetchall()
        return [_row_to_subscription(row) for row in rows]
```

同时在文件顶部 import 中补 `Channel`、`ChannelKind`、`Subscription`、`SubscriptionMode`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py --no-cov`
Expected: PASS（11 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/store.py tests/unit/test_supervisor_store.py
git commit -m "feat: persist supervisor channels and subscriptions"
```

---

### Task 5：Decision 与 SourceCursor 仓储（M1-5）

**Files:**
- Modify: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_supervisor_store.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `SupervisorStore`。
- Produces:
  - `insert_decision(decision: Decision) -> None`
  - `get_decision(decision_id: str) -> Decision | None`
  - `list_decisions(*, trigger_id: str | None = None, mandate_id: str | None = None, limit: int = 100) -> list[Decision]`
  - `get_source_cursor(source: str) -> SourceCursor | None`
  - `put_source_cursor(cursor: SourceCursor) -> None`
  - `list_source_cursors() -> list[SourceCursor]`
  - `list_pending_approvals() -> list[dict[str, Any]]`
  - `list_failed_effects() -> list[dict[str, Any]]`

- [ ] **Step 1: 追加失败测试**

```python
def _decision(*, attempt: int = 1, trigger_id: str = "tg-0001") -> Decision:
    return Decision(
        id=f"dc-{attempt}",
        trigger_ids=[trigger_id],
        outcome=DecisionOutcome.OBSERVE,
        strength=DecisionStrength.INFERRED,
        reason_codes=["would_accept"],
        policy_version="p6-v1",
        request_digest="digest-1",
        attempt=attempt,
        created_at=10 * attempt,
    )


def test_decision_round_trip_keeps_trigger_links(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    store.insert_decision(_decision())
    loaded = store.get_decision("dc-1")
    assert loaded is not None
    assert loaded.trigger_ids == ["tg-0001"]
    assert store.list_decisions(trigger_id="tg-0001")[0].id == "dc-1"
    assert store.list_decisions(trigger_id="tg-404") == []


def test_decision_request_digest_is_idempotent_per_attempt(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    store.insert_decision(_decision())
    duplicate = _decision().model_copy(update={"id": "dc-dup"})
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_decision(duplicate)
    store.insert_decision(_decision(attempt=2).model_copy(update={"id": "dc-2"}))
    assert [d.attempt for d in store.list_decisions()] == [1, 2]


def test_decision_filters_by_mandate(store: SupervisorStore) -> None:
    store.insert_trigger(_trigger())
    bound = _decision().model_copy(update={"id": "dc-bound", "mandate_id": "md-1"})
    store.insert_decision(bound)
    assert [d.id for d in store.list_decisions(mandate_id="md-1")] == ["dc-bound"]


def test_source_cursor_upsert_keeps_one_row(store: SupervisorStore) -> None:
    store.put_source_cursor(SourceCursor(source="github", cursor="1"))
    store.put_source_cursor(
        SourceCursor(
            source="github",
            cursor="2",
            last_success_at=20,
            consecutive_failures=3,
            health=SourceHealth.DEGRADED,
        )
    )
    loaded = store.get_source_cursor("github")
    assert loaded is not None
    assert (loaded.cursor, loaded.consecutive_failures, loaded.health) == (
        "2",
        3,
        SourceHealth.DEGRADED,
    )
    assert [c.source for c in store.list_source_cursors()] == ["github"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py -k 'decision or cursor' --no-cov`
Expected: FAIL，`AttributeError: 'SupervisorStore' object has no attribute 'insert_decision'`。

- [ ] **Step 3: 追加表、索引与 CRUD**

在 `_SUPERVISOR_SCHEMA` 中追加：

```python
    "decisions": (
        "CREATE TABLE IF NOT EXISTS decisions ("
        " id TEXT PRIMARY KEY, outcome TEXT NOT NULL, strength TEXT NOT NULL,"
        " reason_codes TEXT NOT NULL DEFAULT '[]', policy_version TEXT NOT NULL,"
        " mandate_id TEXT, request_digest TEXT NOT NULL, attempt INTEGER NOT NULL,"
        " created_at INTEGER NOT NULL, UNIQUE (request_digest, attempt))"
    ),
    "decision_triggers": (
        "CREATE TABLE IF NOT EXISTS decision_triggers ("
        " decision_id TEXT NOT NULL, trigger_id TEXT NOT NULL,"
        " PRIMARY KEY (decision_id, trigger_id))"
    ),
    "source_cursors": (
        "CREATE TABLE IF NOT EXISTS source_cursors ("
        " source TEXT PRIMARY KEY, cursor TEXT, last_success_at INTEGER,"
        " consecutive_failures INTEGER NOT NULL DEFAULT 0,"
        " health TEXT NOT NULL DEFAULT 'ok')"
    ),
```

在 `_SUPERVISOR_INDEXES` 中追加：

```python
    "CREATE INDEX IF NOT EXISTS decisions_created ON decisions (created_at, id)",
    "CREATE INDEX IF NOT EXISTS decisions_mandate ON decisions (mandate_id, created_at)",
    "CREATE INDEX IF NOT EXISTS decision_triggers_trigger ON decision_triggers (trigger_id)",
```

在 `SupervisorStore` 内追加：

```python
    def insert_decision(self, decision: Decision) -> None:
        self._conn.execute(
            "INSERT INTO decisions"
            " (id, outcome, strength, reason_codes, policy_version, mandate_id,"
            " request_digest, attempt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision.id,
                decision.outcome.value,
                decision.strength.value,
                json.dumps(decision.reason_codes, sort_keys=True, ensure_ascii=False),
                decision.policy_version,
                decision.mandate_id,
                decision.request_digest,
                decision.attempt,
                decision.created_at,
            ),
        )
        self._conn.executemany(
            "INSERT INTO decision_triggers (decision_id, trigger_id) VALUES (?, ?)",
            [(decision.id, trigger_id) for trigger_id in decision.trigger_ids],
        )

    def _row_to_decision(self, row: sqlite3.Row) -> Decision:
        trigger_ids = [
            str(r[0])
            for r in self._conn.execute(
                "SELECT trigger_id FROM decision_triggers WHERE decision_id = ?"
                " ORDER BY trigger_id ASC",
                (row["id"],),
            ).fetchall()
        ]
        return Decision(
            id=row["id"],
            trigger_ids=trigger_ids,
            outcome=DecisionOutcome(row["outcome"]),
            strength=DecisionStrength(row["strength"]),
            reason_codes=json.loads(row["reason_codes"]),
            policy_version=row["policy_version"],
            mandate_id=row["mandate_id"],
            request_digest=row["request_digest"],
            attempt=row["attempt"],
            created_at=row["created_at"],
        )

    def get_decision(self, decision_id: str) -> Decision | None:
        row = self._conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        return self._row_to_decision(row) if row else None

    def list_decisions(
        self,
        *,
        trigger_id: str | None = None,
        mandate_id: str | None = None,
        limit: int = 100,
    ) -> list[Decision]:
        clauses, params = [], []
        joins = ""
        if trigger_id is not None:
            joins = " JOIN decision_triggers dt ON dt.decision_id = d.id"
            clauses.append("dt.trigger_id = ?")
            params.append(trigger_id)
        if mandate_id is not None:
            clauses.append("d.mandate_id = ?")
            params.append(mandate_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT DISTINCT d.* FROM decisions d{joins} {where}"
            " ORDER BY d.created_at ASC, d.id ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._row_to_decision(row) for row in rows]

    def get_source_cursor(self, source: str) -> SourceCursor | None:
        row = self._conn.execute(
            "SELECT * FROM source_cursors WHERE source = ?", (source,)
        ).fetchone()
        return _row_to_source_cursor(row) if row else None

    def put_source_cursor(self, cursor: SourceCursor) -> None:
        self._conn.execute(
            "INSERT INTO source_cursors"
            " (source, cursor, last_success_at, consecutive_failures, health)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(source) DO UPDATE SET cursor = excluded.cursor,"
            " last_success_at = excluded.last_success_at,"
            " consecutive_failures = excluded.consecutive_failures,"
            " health = excluded.health",
            (
                cursor.source,
                cursor.cursor,
                cursor.last_success_at,
                cursor.consecutive_failures,
                cursor.health.value,
            ),
        )

    def list_source_cursors(self) -> list[SourceCursor]:
        rows = self._conn.execute("SELECT * FROM source_cursors ORDER BY source ASC").fetchall()
        return [_row_to_source_cursor(row) for row in rows]

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM approval_requests WHERE status = 'pending'"
            " ORDER BY requested_at ASC, id ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_failed_effects(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT e.id AS effect_id, e.kind AS kind, e.last_error AS last_error,"
            " e.due_at AS due_at, a.ticket_id AS ticket_id"
            " FROM effects e"
            " JOIN assignments a ON json_extract(e.payload, '$.assignment_id') = a.id"
            " WHERE e.state = 'failed' ORDER BY e.due_at ASC, e.id ASC"
        ).fetchall()
        return [dict(row) for row in rows]
```

模块级追加：

```python
def _row_to_source_cursor(row: sqlite3.Row) -> SourceCursor:
    return SourceCursor(
        source=row["source"],
        cursor=row["cursor"],
        last_success_at=row["last_success_at"],
        consecutive_failures=row["consecutive_failures"],
        health=SourceHealth(row["health"]),
    )
```

同时在顶部 import 中补 `Decision`、`DecisionOutcome`、`DecisionStrength`、`SourceCursor`、
`SourceHealth`、`Any`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_store.py --no-cov`
Expected: PASS（15 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_store.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/store.py tests/unit/test_supervisor_store.py
git commit -m "feat: persist supervisor decisions and source cursors"
```

---

### Task 6：只读监管视图（M1-6）

**Files:**
- Create: `stableagent/supervisor/views.py`
- Test: `tests/unit/test_supervisor_views.py`

**Interfaces:**
- Consumes: `SupervisorStore` 的只读方法、`orchestration.model` 的 Ticket/Report/TicketInput。
- Produces:
  - `AttentionItem`、`TicketSummary`、`FailedEffectView`、`TimelineEvent`
  - `SupervisorViews.needs_attention() -> list[AttentionItem]`
  - `SupervisorViews.open_tickets() -> list[TicketSummary]`
  - `SupervisorViews.failed_effects() -> list[FailedEffectView]`
  - `SupervisorViews.source_health() -> list[SourceCursor]`
  - `SupervisorViews.thread_timeline(thread_key: str) -> list[TimelineEvent]`
  - `SupervisorViews.ticket_timeline(ticket_id: str) -> list[TimelineEvent]`

- [ ] **Step 1: 写失败测试**

```python
"""只读监管视图：优先级顺序、失败 effect 归属、时间线不泄露 payload。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Effect,
    EffectKind,
    EffectState,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketInput,
    TicketStatus,
)
from stableagent.supervisor.model import (
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import SupervisorViews
from tests.support import FixedClock


@pytest.fixture
def store(tmp_path: Path) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=FixedClock())


def _ticket(store: SupervisorStore, ticket_id: str, status: TicketStatus) -> Ticket:
    clock = FixedClock()
    store.insert_mandate(Mandate(id=f"md-{ticket_id}", granted_at=clock.now_ms()))
    ticket = Ticket(
        id=ticket_id,
        title=f"title-{ticket_id}",
        task="SECRET-TASK",
        created_at=clock.now_ms(),
        status=status,
        mandate_id=f"md-{ticket_id}",
    )
    store.insert_ticket(ticket)
    return ticket


def test_needs_attention_is_ordered_by_actionability(store: SupervisorStore) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    failed = _ticket(store, "tk-fail", TicketStatus.RUNNING)
    ready = _ticket(store, "tk-ready", TicketStatus.READY)
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="push",
        resource_digest="d",
        reason="r",
        requested_at=1,
    )
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=failed.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )

    items = SupervisorViews(store).needs_attention()

    assert [(i.priority, i.source, i.ticket_id) for i in items] == [
        (0, "approval", waiting.id),
        (1, "failed_effect", failed.id),
        (2, "waiting_ticket", waiting.id),
        (3, "ready_ticket", ready.id),
    ]


def test_open_tickets_excludes_terminal_statuses(store: SupervisorStore) -> None:
    _ticket(store, "tk-run", TicketStatus.RUNNING)
    _ticket(store, "tk-done", TicketStatus.COMPLETED)
    _ticket(store, "tk-cancel", TicketStatus.CANCELLED)
    assert [t.id for t in SupervisorViews(store).open_tickets()] == ["tk-run"]


def test_failed_effects_carry_ticket_identity(store: SupervisorStore) -> None:
    failed = _ticket(store, "tk-fail", TicketStatus.RUNNING)
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=failed.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )
    rows = SupervisorViews(store).failed_effects()
    assert [(r.ticket_id, r.effect_id) for r in rows] == [(failed.id, "ef-1")]


def test_source_health_lists_persisted_cursors(store: SupervisorStore) -> None:
    store.put_source_cursor(SourceCursor(source="github", cursor="9", health=SourceHealth.DEGRADED))
    assert [c.health for c in SupervisorViews(store).source_health()] == [SourceHealth.DEGRADED]


def test_thread_timeline_includes_triggers_and_decisions_without_payload(
    store: SupervisorStore,
) -> None:
    trigger = Trigger(
        id="tg-1",
        external_id="issue:1",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"body": "DO-NOT-LEAK"},
    )
    store.insert_trigger(trigger)
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=["tg-1"],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            policy_version="p6-v1",
            request_digest="d-1",
            created_at=12,
        )
    )

    events = SupervisorViews(store).thread_timeline("issue:acme/widgets#1")

    assert [e.kind for e in events] == ["trigger", "decision"]
    assert "DO-NOT-LEAK" not in json.dumps([e.model_dump() for e in events])


def test_ticket_timeline_uses_sanctioned_summaries_only(store: SupervisorStore) -> None:
    ticket = _ticket(store, "tk-1", TicketStatus.RUNNING)
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id="as-1",
            kind=ReportKind.PROGRESS,
            summary="local-restricted exit=0 output=intact duration_ms=0",
            created_at=20,
        )
    )
    store.insert_input(
        TicketInput(
            id="in-1",
            ticket_id=ticket.id,
            sequence=1,
            payload="RAW-SECRET-INPUT",
            created_at=21,
        )
    )

    events = SupervisorViews(store).ticket_timeline(ticket.id)

    assert [e.kind for e in events] == ["report", "input"]
    rendered = json.dumps([e.model_dump() for e in events])
    assert "exit=0" in rendered
    assert "RAW-SECRET-INPUT" not in rendered and "SECRET-TASK" not in rendered
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_views.py --no-cov`
Expected: FAIL，`ModuleNotFoundError: No module named 'stableagent.supervisor.views'`。

- [ ] **Step 3: 实现只读视图**

```python
"""人工监管的只读查询。只读：本模块不写库、不建 Ticket、不投递消息。

排序是产品契约的一部分（规格 2 的注意力预算）：待人工审批 → 失败 effect →
waiting Ticket → ready Ticket。时间线只返回已批准的结构化摘要，不返回 payload。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from stableagent.orchestration.model import TicketStatus
from stableagent.supervisor.model import SourceCursor
from stableagent.supervisor.store import SupervisorStore


class AttentionItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    priority: int
    source: Literal["approval", "failed_effect", "waiting_ticket", "ready_ticket"]
    ticket_id: str | None
    detail: str


class TicketSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    status: TicketStatus
    created_at: int


class FailedEffectView(BaseModel):
    model_config = ConfigDict(frozen=True)

    effect_id: str
    kind: str
    ticket_id: str | None
    last_error: str | None
    due_at: int


class TimelineEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: int
    kind: Literal["trigger", "decision", "report", "input"]
    summary: str


_OPEN_STATUSES = (
    TicketStatus.OPEN,
    TicketStatus.QUEUED,
    TicketStatus.RUNNING,
    TicketStatus.WAITING,
    TicketStatus.READY,
    TicketStatus.BLOCKED,
)


class SupervisorViews:
    def __init__(self, store: SupervisorStore) -> None:
        self._store = store

    def needs_attention(self) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        for row in self._store.list_pending_approvals():
            items.append(
                AttentionItem(
                    priority=0,
                    source="approval",
                    ticket_id=row["ticket_id"],
                    detail=f"approval pending: {row['action']}",
                )
            )
        for row in self._store.list_failed_effects():
            items.append(
                AttentionItem(
                    priority=1,
                    source="failed_effect",
                    ticket_id=row["ticket_id"],
                    detail=f"effect failed: {row['kind']}",
                )
            )
        for ticket in self._store.list_tickets(status=TicketStatus.WAITING):
            items.append(
                AttentionItem(
                    priority=2,
                    source="waiting_ticket",
                    ticket_id=ticket.id,
                    detail="ticket waiting for human input",
                )
            )
        for ticket in self._store.list_tickets(status=TicketStatus.READY):
            items.append(
                AttentionItem(
                    priority=3,
                    source="ready_ticket",
                    ticket_id=ticket.id,
                    detail="ticket ready for review",
                )
            )
        return sorted(items, key=lambda item: (item.priority, item.ticket_id or "", item.detail))

    def open_tickets(self) -> list[TicketSummary]:
        rows = []
        for status in _OPEN_STATUSES:
            rows.extend(self._store.list_tickets(status=status))
        return [
            TicketSummary(
                id=ticket.id,
                title=ticket.title,
                status=ticket.status,
                created_at=ticket.created_at,
            )
            for ticket in sorted(rows, key=lambda ticket: (ticket.created_at, ticket.id))
        ]

    def failed_effects(self) -> list[FailedEffectView]:
        return [
            FailedEffectView(
                effect_id=row["effect_id"],
                kind=row["kind"],
                ticket_id=row["ticket_id"],
                last_error=row["last_error"],
                due_at=row["due_at"],
            )
            for row in self._store.list_failed_effects()
        ]

    def source_health(self) -> list[SourceCursor]:
        return self._store.list_source_cursors()

    def thread_timeline(self, thread_key: str) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        decisions: dict[str, TimelineEvent] = {}
        for trigger in self._store.list_triggers(thread_key=thread_key):
            events.append(
                TimelineEvent(
                    at=trigger.occurred_at,
                    kind="trigger",
                    summary=f"{trigger.kind.value}:{trigger.actor}",
                )
            )
            for decision in self._store.list_decisions(trigger_id=trigger.id):
                decisions[decision.id] = TimelineEvent(
                    at=decision.created_at,
                    kind="decision",
                    summary=decision.outcome.value,
                )
        events.extend(decisions.values())
        return sorted(events, key=lambda event: (event.at, event.kind, event.summary))

    def ticket_timeline(self, ticket_id: str) -> list[TimelineEvent]:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        events = [
            TimelineEvent(at=report.created_at, kind="report", summary=report.summary)
            for report in self._store.list_reports(ticket_id)
        ]
        events.extend(
            TimelineEvent(
                at=ticket_input.created_at,
                kind="input",
                summary=f"{ticket_input.kind}:{ticket_input.state}",
            )
            for ticket_input in self._store.list_inputs(ticket_id)
        )
        events.extend(
            TimelineEvent(at=decision.created_at, kind="decision", summary=decision.outcome.value)
            for decision in self._store.list_decisions(mandate_id=ticket.mandate_id)
        )
        return sorted(events, key=lambda event: (event.at, event.kind, event.summary))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_supervisor_views.py --no-cov`
Expected: PASS（6 passed）。

- [ ] **Step 5: 跑静态检查**

Run: `.venv/bin/ruff check stableagent/supervisor tests/unit/test_supervisor_views.py && .venv/bin/ruff format --check stableagent/supervisor tests/unit/test_supervisor_views.py && .venv/bin/mypy`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/views.py tests/unit/test_supervisor_views.py
git commit -m "feat: expose read-only supervisor views with attention ordering"
```

---

### Task 7：CI FOCUS 25 → 28（M1-7）

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/unit/test_ci_focus_gate.py`

**Interfaces:**
- Consumes: Task 2/3/6 的三个新模块。
- Produces: FOCUS 名单 28 项，新增 `supervisor/model.py`、`supervisor/store.py`、
  `supervisor/views.py`。

- [ ] **Step 1: 改契约测试（先失败）**

把 `FOCUS_FILES` 追加：

```python
    "supervisor/model.py",
    "supervisor/store.py",
    "supervisor/views.py",
```

把测试名与断言改为：

```python
def test_focus_covers_the_twenty_eight_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 28
    assert "28 文件" in text
    assert text.count('".py"') == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q tests/unit/test_ci_focus_gate.py --no-cov`
Expected: FAIL，`assert len(FOCUS_FILES) == 28` 或 workflow 缺少新文件名。

- [ ] **Step 3: 同步 CI 工作流**

把步骤名里的 `25 文件` 改为 `28 文件`，并在 Python heredoc 的 `FOCUS` 元组追加：

```python
              "supervisor/model.py",
              "supervisor/store.py",
              "supervisor/views.py",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest -q tests/unit/test_ci_focus_gate.py --no-cov`
Expected: PASS。

- [ ] **Step 5: 本地重算 FOCUS**

Run:

```bash
.venv/bin/pytest -q --cov=stableagent --cov-report=xml
python - <<'PY'
import xml.etree.ElementTree as ET
files = ("supervisor/model.py", "supervisor/store.py", "supervisor/views.py")
rows = [
    (k.get("filename", ""), 100 * float(k.get("line-rate")))
    for k in ET.parse("coverage.xml").getroot().iter("class")
    if any((k.get("filename") or "").endswith(f) for f in files)
]
print(rows)
assert len(rows) == 3 and all(rate >= 90 for _, rate in rows), rows
PY
```

Expected: 三行均 ≥90%。

- [ ] **Step 6: 提交**

```bash
git add .github/workflows/ci.yml tests/unit/test_ci_focus_gate.py
git commit -m "ci: extend the cardiac coverage gate to supervisor"
```

---

### Task 8：M1 出口（M1-8）

**Files:**
- Modify: `docs/acceptance/p6-supervisor-beta.md`
- Modify: `README.md`、`docs/README.md`、`docs/Roadmap.md`、`docs/acceptance/README.md`、
  `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1~7 的产出与门禁读数。
- Produces: M1 勾选、状态面同步、可推送的收口提交。

- [ ] **Step 1: 跑全量门禁并落盘**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m1-pytest.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-pytest.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff check stableagent tests examples benchmarks > /tmp/p6-m1-ruff.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-ruff.txt
env -u NO_PROXY -u no_proxy .venv/bin/ruff format --check stableagent tests examples benchmarks > /tmp/p6-m1-format.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-format.txt
env -u NO_PROXY -u no_proxy .venv/bin/mypy > /tmp/p6-m1-mypy.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-mypy.txt
```

Expected: 四份文件均 `exit=0`；pytest 无 failed。

- [ ] **Step 2: 三解释器等价**

```bash
env -u NO_PROXY -u no_proxy .matrix/py311/bin/python -m pytest -q -p no:cacheprovider > /tmp/p6-m1-py311.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-py311.txt
env -u NO_PROXY -u no_proxy .matrix/py312/bin/python -m pytest -q -p no:cacheprovider > /tmp/p6-m1-py312.txt 2>&1; echo "exit=$?" >> /tmp/p6-m1-py312.txt
```

Expected: 两份均 `exit=0`，passed/skip 与 3.10 一致。

- [ ] **Step 3: 边界复核**

```bash
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m1-l1.txt
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py > /tmp/p6-m1-c1.txt
git diff --check
```

Expected: 前两份 0 行；`git diff --check` 无输出。

- [ ] **Step 4: 回填账本**

把第 1 节 M1-1~M1-8 改为 `[x]`，并在每条下追加证据：提交号、测试文件与用例数、
门禁读数、FOCUS 三文件覆盖率、L1/约束 1 的 0 行结论。

- [ ] **Step 5: 同步状态面**

- `README.md` P6 行：`🟡 active — M1 done (8/8); milestones 1/6`。
- `docs/README.md`：当前主线改为 P6 M1 完成，链接本账本、ADR-06 与
  `docs/guides/supervisor-migration.md`；阶段表 P6 行同步。
- `docs/acceptance/README.md`：P6 行标 Active（M1 完成）。
- `docs/Roadmap.md`：P6 段写明 M1 完成与下一步 M2。
- `CHANGELOG.md`：新增 P6 M1 条目。

- [ ] **Step 6: 提交**

```bash
git add docs/acceptance/p6-supervisor-beta.md README.md docs/README.md docs/Roadmap.md \
  docs/acceptance/README.md CHANGELOG.md
git commit -m "docs: close p6 m1 with the local gate evidence"
```

## 6. 自检记录（writing-plans）

- 规格覆盖：WP-6.1 的 5 条分别由 M1-2（模型）、M1-3（唯一约束/迁移）、
  M1-6（六个视图）、M1-1（迁移文档口径）覆盖；FOCUS ≥90% 由 M1-7 覆盖。
- 类型一致性：`Trigger.payload`/`digest`、`SupervisorStore` 方法名、视图类名在 Task
  2~6 的 Interfaces 与代码块中一致。
- 无占位符：所有 Step 都给出可执行命令或完整代码。

## 7. M2 任务级实施计划（writing-plans 产出，2026-11-08）

M2 展开为 **7 个 Task / 43 个 Step**，覆盖 M2-1~M2-8。主路径为 **polling**：读客户端只做
只读列举，写入只发生在 `poll` 的单个事务里；读权限与写权限分离，Channel 只是目的地。

### Task 1：只读客户端接缝与 Source 契约（M2-1）

**Files:**
- Modify: `stableagent/integrations/github.py`
- Create: `stableagent/supervisor/source.py`
- Test: `tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: `stableagent.supervisor.model.{Channel, ChannelKind, Thread, Trigger}`。
- Produces:
  - `GitHubReadKind`（6 值 str 枚举，与 `TriggerKind` 一一对应；方向锁禁止 integrations
    import supervisor，所以两边各有一个枚举，由 `TriggerKind(item.kind.value)` 对齐）
  - `GitHubReadItem(kind, occurred_at, resource, deleted=False)`、
    `GitHubReadPage(items, next_cursor)`
  - `GitHubReadClient.list_events(*, repository, cursor, limit) -> GitHubReadPage`、
    `FakeGitHubReadClient(items, *, page_size=None)`（`calls` / `fail_next`）
  - `SourceError` 四子类：`source.transient` / `source.auth` / `source.gone` / `source.schema`
  - `SourceEvent(trigger, thread, channel=None)`、`PollBatch(events, next_cursor, duplicates)`
  - `Source.poll`、`ChannelWriter.write`、`FakeChannelWriter`
  - `stable_trigger_id`、`stable_channel_id`、`issue_thread_key`、`pull_request_thread_key`、
    `review_thread_key(repository, pull_number, review_id)`、`repository_thread_key`

- [ ] **Step 1: 写失败测试**

```python
"""WP-6.2：只读轮询到 Trigger/Thread/Channel 的确定性映射。"""

from __future__ import annotations

import pytest

from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
    GitHubReadPage,
)
from stableagent.supervisor.model import Channel, ChannelKind
from stableagent.supervisor.source import (
    FakeChannelWriter,
    SourceAuthError,
    SourceGoneError,
    SourceSchemaError,
    SourceTransientError,
    issue_thread_key,
    pull_request_thread_key,
    repository_thread_key,
    review_thread_key,
    stable_channel_id,
    stable_trigger_id,
)


def _item(number: int = 1, *, occurred_at: int = 1) -> GitHubReadItem:
    return GitHubReadItem(
        kind=GitHubReadKind.ISSUE, occurred_at=occurred_at, resource={"number": number}
    )


async def test_read_fake_pages_with_offset_cursors_and_records_calls() -> None:
    client = FakeGitHubReadClient([_item(1), _item(2), _item(3)], page_size=2)
    assert isinstance(client, GitHubReadClient)

    first = await client.list_events(repository="owner/repo", cursor=None, limit=10)
    second = await client.list_events(repository="owner/repo", cursor=first.next_cursor, limit=10)

    assert [item.resource["number"] for item in first.items] == [1, 2]
    assert first.next_cursor == "offset:2"
    assert [item.resource["number"] for item in second.items] == [3]
    assert second.next_cursor is None
    assert client.calls == [
        {"repository": "owner/repo", "cursor": None, "limit": 10},
        {"repository": "owner/repo", "cursor": "offset:2", "limit": 10},
    ]


async def test_read_fake_fail_next_raises_once_then_clears() -> None:
    client = FakeGitHubReadClient([])
    client.fail_next = SourceTransientError("503")
    with pytest.raises(SourceTransientError):
        await client.list_events(repository="owner/repo", cursor=None, limit=1)
    empty = await client.list_events(repository="owner/repo", cursor=None, limit=1)
    assert empty == GitHubReadPage()
    assert client.fail_next is None


def test_source_errors_carry_stable_codes() -> None:
    assert SourceTransientError("503").code == "source.transient"
    assert SourceAuthError("403").code == "source.auth"
    assert SourceGoneError("404").code == "source.gone"
    assert SourceSchemaError("bad").code == "source.schema"


async def test_channel_writer_fake_records_and_fails_once() -> None:
    writer = FakeChannelWriter()
    channel = Channel(
        id="ch-1",
        thread_key="issue:owner/repo#1",
        kind=ChannelKind.ISSUE_COMMENT,
        external_ref="#1",
    )
    assert await writer.write(channel=channel, body="hi") == {"id": "fake-1"}
    writer.fail_next = SourceTransientError("boom")
    with pytest.raises(SourceTransientError):
        await writer.write(channel=channel, body="again")
    assert [body for _, body in writer.calls] == ["hi", "again"]
    assert writer.fail_next is None


def test_thread_keys_and_ids_are_stable() -> None:
    assert issue_thread_key("owner/repo", 7) == "issue:owner/repo#7"
    assert pull_request_thread_key("owner/repo", 7) == "pr:owner/repo#7"
    assert review_thread_key("owner/repo", 7, 42) == "review:owner/repo#7:42"
    assert repository_thread_key("owner/repo") == "repo:owner/repo"
    assert stable_trigger_id("github:owner/repo:issue:7") == stable_trigger_id(
        "github:owner/repo:issue:7"
    )
    assert stable_trigger_id("a") != stable_trigger_id("b")
    assert stable_trigger_id("a").startswith("tg-")
    first = stable_channel_id("issue:owner/repo#7", ChannelKind.ISSUE_COMMENT, "#7")
    second = stable_channel_id("issue:owner/repo#7", ChannelKind.ISSUE_COMMENT, "#7")
    assert first == second and first.startswith("ch-")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py`

Expected: FAIL —— `ModuleNotFoundError: No module named 'stableagent.supervisor.source'`
（`GitHubReadClient` 也尚未存在）。

- [ ] **Step 3: 在 `integrations/github.py` 追加只读接缝**

导入行改为：

```python
from collections.abc import Iterable
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field
```

文件末尾追加：

```python
class GitHubReadKind(str, Enum):
    """只读侧事件分类；与 supervisor 的 TriggerKind 一一对应，方向锁禁止反向 import。"""

    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    ISSUE_COMMENT = "issue_comment"
    REVIEW_COMMENT = "review_comment"
    REVIEW = "review"
    CI_STATUS = "ci_status"


class GitHubReadItem(BaseModel):
    """一条只读事件：`resource` 保持 GitHub 形状，`occurred_at` 已归一到毫秒。"""

    kind: GitHubReadKind
    occurred_at: int
    resource: dict[str, Any]
    deleted: bool = False


class GitHubReadPage(BaseModel):
    items: list[GitHubReadItem] = Field(default_factory=list)
    next_cursor: str | None = None


@runtime_checkable
class GitHubReadClient(Protocol):
    async def list_events(
        self, *, repository: str, cursor: str | None, limit: int
    ) -> GitHubReadPage: ...


class FakeGitHubReadClient:
    """确定性只读 fake：`offset:<n>` 游标分页，按调用顺序记录，失败一次即清空。"""

    def __init__(self, items: Iterable[GitHubReadItem], *, page_size: int | None = None) -> None:
        self.items = list(items)
        self.page_size = page_size
        self.calls: list[dict[str, Any]] = []
        self.fail_next: Exception | None = None

    async def list_events(
        self, *, repository: str, cursor: str | None, limit: int
    ) -> GitHubReadPage:
        self.calls.append({"repository": repository, "cursor": cursor, "limit": limit})
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        offset = int(cursor.split(":")[1]) if cursor else 0
        size = min(limit, self.page_size) if self.page_size is not None else limit
        chunk = self.items[offset : offset + size]
        consumed = offset + len(chunk)
        next_cursor = f"offset:{consumed}" if consumed < len(self.items) else None
        return GitHubReadPage(items=chunk, next_cursor=next_cursor)
```

- [ ] **Step 4: 新建 `supervisor/source.py` 契约层**

```python
"""GitHub Source 契约：把只读轮询结果标准化为 Trigger / Thread / Channel（规格 WP-6.2）。

方向锁：本模块只 import `integrations` 与 `supervisor.model`；读取只产生候选事件，
Channel 只是回写目的地（ADR-06 决策 8），不携带授权。
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from stableagent.supervisor.model import Channel, ChannelKind, Thread, Trigger


class SourceError(Exception):
    """Source 层稳定错误：四分类决定 cursor health 与是否可重试。"""

    code = "source.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class SourceTransientError(SourceError):
    """网络抖动、限流、5xx：cursor 记 degraded，下一轮可重试。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.transient", message)


class SourceAuthError(SourceError):
    """403：读权限不足或 token 失效，需要人工处理。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.auth", message)


class SourceGoneError(SourceError):
    """404：仓库不可见或事件已不存在，继续轮询不会恢复。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.gone", message)


class SourceSchemaError(SourceError):
    """载荷形状不合约：整批不落库，避免写进半个事实。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.schema", message)


class SourceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: Trigger
    thread: Thread
    channel: Channel | None = None


class PollBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: list[SourceEvent] = Field(default_factory=list)
    next_cursor: str | None = None
    duplicates: int = Field(default=0, ge=0)


@runtime_checkable
class Source(Protocol):
    async def poll(self, *, limit: int = 100) -> PollBatch: ...


@runtime_checkable
class ChannelWriter(Protocol):
    async def write(self, *, channel: Channel, body: str) -> dict[str, Any]: ...


class FakeChannelWriter:
    """记录 `(channel, body)`；`fail_next` 抛一次后清空。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Channel, str]] = []
        self.fail_next: Exception | None = None

    async def write(self, *, channel: Channel, body: str) -> dict[str, Any]:
        self.calls.append((channel, body))
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        return {"id": f"fake-{len(self.calls)}"}


def stable_trigger_id(external_id: str) -> str:
    return "tg-" + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:12]


def stable_channel_id(thread_key: str, kind: ChannelKind, external_ref: str) -> str:
    seed = f"{thread_key}|{kind.value}|{external_ref}"
    return "ch-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def issue_thread_key(repository: str, number: int) -> str:
    return f"issue:{repository}#{number}"


def pull_request_thread_key(repository: str, number: int) -> str:
    return f"pr:{repository}#{number}"


def review_thread_key(repository: str, pull_number: int, review_id: int) -> str:
    return f"review:{repository}#{pull_number}:{review_id}"


def repository_thread_key(repository: str) -> str:
    return f"repo:{repository}"
```

- [ ] **Step 5: 跑测试确认通过并做静态检查**

Run:
`.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py`
`.venv/bin/ruff check stableagent/integrations/github.py stableagent/supervisor/source.py tests/unit/test_github_source.py`
`.venv/bin/ruff format --check stableagent/integrations/github.py stableagent/supervisor/source.py tests/unit/test_github_source.py`
`.venv/bin/mypy`

Expected: 5 passed；`All checks passed!`；格式已排列；mypy 无新增错误。

- [ ] **Step 6: 提交**

```bash
git add stableagent/integrations/github.py stableagent/supervisor/source.py \
  tests/unit/test_github_source.py
git commit -m "feat: add github read client seam and source contracts"
```

### Task 2：Issue / PR 标准化与 bot 过滤（M2-2）

**Files:**
- Modify: `stableagent/supervisor/source.py`
- Test: `tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: Task 1 的 `GitHubReadItem` / `stable_trigger_id` / thread key 函数；
  `SupervisorStore`、`Clock`（`stableagent.orchestration.model`）。
- Produces:
  - `GitHubSource(store, client, *, repository, bot_login, clock)`
  - `GitHubSource.normalize(item) -> SourceEvent | None`（`None` 表示自身 bot 事件，被过滤）
  - Trigger `external_id = github:{repository}:{kind}:{subject}`，`id = tg-<sha256 前 12>`

- [ ] **Step 1: 追加失败测试**

测试文件导入补充：

```python
import pathlib

from stableagent.supervisor.model import ThreadKind, ThreadStatus, TriggerKind
from stableagent.supervisor.source import GitHubSource
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock
```

```python
def _source(tmp_path: pathlib.Path):
    clock = FixedClock()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    client = FakeGitHubReadClient([])
    source = GitHubSource(
        store, client, repository="owner/repo", bot_login="sa-agent[bot]", clock=clock
    )
    return source, store, clock


def _read_item(
    kind: GitHubReadKind,
    resource: dict[str, object],
    *,
    occurred_at: int = 10,
    deleted: bool = False,
) -> GitHubReadItem:
    return GitHubReadItem(
        kind=kind, occurred_at=occurred_at, resource=resource, deleted=deleted
    )


def test_issue_normalizes_to_trigger_thread_and_digest(tmp_path: pathlib.Path) -> None:
    source, _, clock = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 7, "state": "open", "user": {"login": "octocat"}},
        )
    )
    assert event is not None
    assert event.thread.key == "issue:owner/repo#7"
    assert event.thread.external_ref == "#7"
    assert event.thread.kind is ThreadKind.ISSUE
    assert event.thread.status is ThreadStatus.OPEN
    assert event.channel is None
    assert event.trigger.kind is TriggerKind.ISSUE
    assert event.trigger.external_id == "github:owner/repo:issue:7"
    assert event.trigger.id.startswith("tg-")
    assert event.trigger.thread_keys == ["issue:owner/repo#7"]
    assert event.trigger.actor == "octocat"
    assert event.trigger.occurred_at == 10
    assert event.trigger.received_at == clock.now_ms()
    assert event.trigger.payload == {
        "resource": {"number": 7, "state": "open", "user": {"login": "octocat"}},
        "deleted": False,
    }
    assert event.trigger.digest


def test_closed_pull_request_maps_to_a_closed_pr_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.PULL_REQUEST,
            {"number": 9, "state": "closed", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.thread.status is ThreadStatus.CLOSED
    assert event.trigger.kind is TriggerKind.PULL_REQUEST
    assert event.trigger.external_id == "github:owner/repo:pull_request:9"


def test_bot_identity_is_filtered_case_insensitively(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 7, "state": "open", "user": {"login": "SA-Agent[BOT]"}},
        )
    )
    assert event is None


@pytest.mark.parametrize(
    "resource",
    [
        {"state": "open", "user": {"login": "alice"}},
        {"number": "7", "state": "open", "user": {"login": "alice"}},
        {"number": 7, "state": "open"},
    ],
)
def test_malformed_issue_is_a_schema_error(
    tmp_path: pathlib.Path, resource: dict[str, object]
) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError):
        source.normalize(_read_item(GitHubReadKind.ISSUE, resource))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py -k "issue or pull_request or bot"`

Expected: FAIL —— `ImportError: cannot import name 'GitHubSource'`。

- [ ] **Step 3: 扩展导入**

```python
from stableagent.integrations.github import GitHubReadClient, GitHubReadItem
from stableagent.orchestration.model import Clock
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore
```

- [ ] **Step 4: 追加 `GitHubSource` 与字段校验辅助函数**

```python
class GitHubSource:
    """单仓库只读 Source：`normalize` 无副作用；写库只发生在 `poll`。"""

    def __init__(
        self,
        store: SupervisorStore,
        client: GitHubReadClient,
        *,
        repository: str,
        bot_login: str,
        clock: Clock,
    ) -> None:
        self._store = store
        self._client = client
        self._repository = repository
        self._bot_login = bot_login
        self._clock = clock

    def normalize(self, item: GitHubReadItem) -> SourceEvent | None:
        kind = TriggerKind(item.kind.value)
        if kind is TriggerKind.ISSUE:
            return self._issue(item)
        if kind is TriggerKind.PULL_REQUEST:
            return self._pull_request(item)
        raise SourceSchemaError(f"unsupported kind {item.kind.value}")

    def _issue(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        number = _int_field(resource, "number")
        key = issue_thread_key(self._repository, number)
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.ISSUE, number, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.ISSUE,
                external_ref=f"#{number}",
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
        )

    def _pull_request(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        number = _int_field(resource, "number")
        key = pull_request_thread_key(self._repository, number)
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.PULL_REQUEST, number, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.PULL_REQUEST,
                external_ref=f"#{number}",
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
        )

    def _trigger(
        self,
        item: GitHubReadItem,
        kind: TriggerKind,
        subject: int,
        actor: str,
        thread_key: str,
    ) -> Trigger:
        external_id = f"github:{self._repository}:{kind.value}:{subject}"
        return Trigger(
            id=stable_trigger_id(external_id),
            external_id=external_id,
            kind=kind,
            actor=actor,
            thread_keys=[thread_key],
            occurred_at=item.occurred_at,
            received_at=self._clock.now_ms(),
            payload={"resource": item.resource, "deleted": item.deleted},
        )

    def _is_self(self, actor: str) -> bool:
        return actor.casefold() == self._bot_login.casefold()


def _int_field(resource: dict[str, Any], field: str) -> int:
    value = resource.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SourceSchemaError(f"{field} 必须是整数")
    return value


def _actor(resource: dict[str, Any]) -> str:
    user = resource.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    if isinstance(login, str) and login:
        return login
    actor = resource.get("actor")
    if isinstance(actor, str) and actor:
        return actor
    raise SourceSchemaError("缺少 actor / user.login")


def _thread_status(
    item: GitHubReadItem, resource: dict[str, Any], *, closed_state: str = "closed"
) -> ThreadStatus:
    if item.deleted or resource.get("state") == closed_state:
        return ThreadStatus.CLOSED
    return ThreadStatus.OPEN
```

- [ ] **Step 5: 跑测试与静态检查**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py`
`.venv/bin/ruff check stableagent/supervisor/source.py tests/unit/test_github_source.py`
`.venv/bin/mypy`

Expected: 11 passed；ruff/mypy 无新增问题（Task 2 新增 6 个用例：3 个主路径 + 3 个参数化非法载荷）。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/source.py tests/unit/test_github_source.py
git commit -m "feat: normalize github issues and pull requests into supervisor threads"
```

### Task 3：Comment / Review 映射与 Channel（M2-3）

**Files:**
- Modify: `stableagent/supervisor/source.py`
- Test: `tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: Task 2 的 `_actor` / `_int_field` / `_thread_status` / `_trigger`。
- Produces:
  - issue comment → issue 或 PR thread；`ChannelKind.ISSUE_COMMENT` / `PULL_REQUEST_COMMENT`，
    `external_ref = "#{number}"`
  - review comment / review → review thread `review:{repo}#{pull}#{review_id}`；
    `ChannelKind.REVIEW_REPLY`，`external_ref = "review:{review_id}"`
  - review `state == "dismissed"` 或删除事件把 review thread 置 `closed`

- [ ] **Step 1: 追加失败测试**

```python
def test_issue_comment_maps_to_issue_thread_and_channel(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 11, "issue_number": 7, "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "issue:owner/repo#7"
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.ISSUE_COMMENT
    assert event.channel.external_ref == "#7"
    assert event.channel.thread_key == event.thread.key
    assert event.trigger.kind is TriggerKind.ISSUE_COMMENT
    assert event.trigger.external_id == "github:owner/repo:issue_comment:11"


def test_comment_on_pull_request_uses_the_pr_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {
                "id": 12,
                "issue_number": 9,
                "is_pull_request": True,
                "user": {"login": "alice"},
            },
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.PULL_REQUEST_COMMENT


def test_review_comment_creates_review_thread_and_reply_channel(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW_COMMENT,
            {"id": 13, "pull_number": 9, "review_id": 42, "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "review:owner/repo#9:42"
    assert event.thread.kind is ThreadKind.REVIEW
    assert event.thread.external_ref == "review:42"
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.REVIEW_REPLY
    assert event.channel.external_ref == "review:42"
    assert event.trigger.kind is TriggerKind.REVIEW_COMMENT
    assert event.trigger.external_id == "github:owner/repo:review_comment:13"


def test_review_submission_maps_to_an_open_review_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW,
            {"id": 42, "pull_number": 9, "state": "submitted", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.kind is ThreadKind.REVIEW
    assert event.thread.status is ThreadStatus.OPEN
    assert event.trigger.kind is TriggerKind.REVIEW
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.REVIEW_REPLY


def test_dismissed_review_closes_the_review_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW,
            {"id": 42, "pull_number": 9, "state": "dismissed", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.status is ThreadStatus.CLOSED


def test_bot_review_comment_is_not_a_candidate(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW_COMMENT,
            {
                "id": 13,
                "pull_number": 9,
                "review_id": 42,
                "user": {"login": "sa-agent[bot]"},
            },
        )
    )
    assert event is None


def test_review_comment_without_review_id_is_a_schema_error(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError, match="review_id"):
        source.normalize(
            _read_item(
                GitHubReadKind.REVIEW_COMMENT,
                {"id": 13, "pull_number": 9, "user": {"login": "alice"}},
            )
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py -k "comment or review"`

Expected: FAIL —— `SourceSchemaError: unsupported kind issue_comment`（其余 kind 也会失败）。

- [ ] **Step 3: 加入分发分支**

```python
        if kind is TriggerKind.ISSUE_COMMENT:
            return self._issue_comment(item)
        if kind is TriggerKind.REVIEW_COMMENT:
            return self._review_comment(item)
        if kind is TriggerKind.REVIEW:
            return self._review(item)
```

- [ ] **Step 4: 追加三个映射方法**

```python
    def _issue_comment(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        comment_id = _int_field(resource, "id")
        number = _int_field(resource, "issue_number")
        if bool(resource.get("is_pull_request")):
            key = pull_request_thread_key(self._repository, number)
            thread_kind = ThreadKind.PULL_REQUEST
            channel_kind = ChannelKind.PULL_REQUEST_COMMENT
        else:
            key = issue_thread_key(self._repository, number)
            thread_kind = ThreadKind.ISSUE
            channel_kind = ChannelKind.ISSUE_COMMENT
        external_ref = f"#{number}"
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.ISSUE_COMMENT, comment_id, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=thread_kind,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
            channel=Channel(
                id=stable_channel_id(key, channel_kind, external_ref),
                thread_key=key,
                kind=channel_kind,
                external_ref=external_ref,
            ),
        )

    def _review_comment(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        comment_id = _int_field(resource, "id")
        pull_number = _int_field(resource, "pull_number")
        review_id = _int_field(resource, "review_id")
        key = review_thread_key(self._repository, pull_number, review_id)
        external_ref = f"review:{review_id}"
        return SourceEvent(
            trigger=self._trigger(
                item, TriggerKind.REVIEW_COMMENT, comment_id, actor, key
            ),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REVIEW,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
            channel=Channel(
                id=stable_channel_id(key, ChannelKind.REVIEW_REPLY, external_ref),
                thread_key=key,
                kind=ChannelKind.REVIEW_REPLY,
                external_ref=external_ref,
            ),
        )

    def _review(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        review_id = _int_field(resource, "id")
        pull_number = _int_field(resource, "pull_number")
        key = review_thread_key(self._repository, pull_number, review_id)
        external_ref = f"review:{review_id}"
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.REVIEW, review_id, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REVIEW,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource, closed_state="dismissed"),
            ),
            channel=Channel(
                id=stable_channel_id(key, ChannelKind.REVIEW_REPLY, external_ref),
                thread_key=key,
                kind=ChannelKind.REVIEW_REPLY,
                external_ref=external_ref,
            ),
        )
```

- [ ] **Step 5: 跑测试与静态检查**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py`
`.venv/bin/ruff check stableagent/supervisor/source.py tests/unit/test_github_source.py`
`.venv/bin/mypy`

Expected: 18 passed；ruff/mypy 无新增问题（Task 3 新增 7 个用例）。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/source.py tests/unit/test_github_source.py
git commit -m "feat: map github comments and reviews to threads and reply channels"
```

### Task 4：CI status 与 repository Thread（M2-4）

**Files:**
- Modify: `stableagent/supervisor/model.py`
- Modify: `stableagent/supervisor/source.py`
- Test: `tests/unit/test_supervisor_model.py`、`tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: 现有 `ThreadKind`、`repository_thread_key`。
- Produces: `ThreadKind.REPOSITORY = "repository"`；CI 有 `pull_number` 时挂
  `pr:{repo}#{n}`，否则挂 `repo:{repo}`；CI 的 `actor` 取 `resource["actor"]`，缺省 `"github"`。
  CI 事件不创建 Channel（没有回写目的地）。

- [ ] **Step 1: 追加失败测试**

`tests/unit/test_supervisor_model.py` 追加：

```python
def test_thread_kinds_are_pinned() -> None:
    assert {k.value for k in ThreadKind} == {
        "issue",
        "pull_request",
        "review",
        "repository",
    }
```

`tests/unit/test_github_source.py` 追加：

```python
def test_ci_status_with_pull_request_attaches_to_the_pr_thread(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.CI_STATUS,
            {"id": 501, "sha": "abc123", "state": "success", "pull_number": 9},
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.trigger.kind is TriggerKind.CI_STATUS
    assert event.trigger.external_id == "github:owner/repo:ci_status:501"
    assert event.trigger.actor == "github"
    assert event.channel is None


def test_ci_status_without_pull_request_uses_the_repository_thread(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.CI_STATUS,
            {"id": 502, "sha": "abc123", "state": "failure"},
        )
    )
    assert event is not None
    assert event.thread.key == "repo:owner/repo"
    assert event.thread.kind is ThreadKind.REPOSITORY
    assert event.thread.external_ref == "owner/repo"
    assert event.channel is None


def test_unknown_ci_state_is_a_schema_error(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError):
        source.normalize(
            _read_item(
                GitHubReadKind.CI_STATUS,
                {"id": 503, "sha": "abc123", "state": "exploded"},
            )
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_model.py tests/unit/test_github_source.py -k "thread_kinds or ci_status"`

Expected: FAIL —— `ThreadKind.REPOSITORY` 不存在；`ci_status` 报 `unsupported kind`。

- [ ] **Step 3: 扩展枚举**

```python
class ThreadKind(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    REVIEW = "review"
    REPOSITORY = "repository"
```

- [ ] **Step 4: 加入 CI 分发与映射**

分发分支：

```python
        if kind is TriggerKind.CI_STATUS:
            return self._ci_status(item)
```

```python
    def _ci_status(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = resource.get("actor", "github")
        if not isinstance(actor, str) or not actor:
            raise SourceSchemaError("actor 必须是非空字符串")
        if self._is_self(actor):
            return None
        status_id = _int_field(resource, "id")
        _str_field(resource, "sha")
        state = _str_field(resource, "state")
        if state not in {"pending", "success", "failure", "error"}:
            raise SourceSchemaError(f"未知 CI state：{state}")
        pull_number = resource.get("pull_number")
        if pull_number is not None and (
            not isinstance(pull_number, int) or isinstance(pull_number, bool)
        ):
            raise SourceSchemaError("pull_number 必须是整数或 null")
        if pull_number is not None:
            key = pull_request_thread_key(self._repository, pull_number)
            thread = Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.PULL_REQUEST,
                external_ref=f"#{pull_number}",
                last_trigger_at=item.occurred_at,
                status=ThreadStatus.OPEN,
            )
        else:
            key = repository_thread_key(self._repository)
            thread = Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REPOSITORY,
                external_ref=self._repository,
                last_trigger_at=item.occurred_at,
                status=ThreadStatus.OPEN,
            )
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.CI_STATUS, status_id, actor, key),
            thread=thread,
        )
```

新增辅助函数：

```python
def _str_field(resource: dict[str, Any], field: str) -> str:
    value = resource.get(field)
    if not isinstance(value, str) or not value:
        raise SourceSchemaError(f"{field} 必须是非空字符串")
    return value
```

- [ ] **Step 5: 跑测试与静态检查**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_model.py tests/unit/test_github_source.py`
`.venv/bin/ruff check stableagent/supervisor tests/unit/test_github_source.py`
`.venv/bin/mypy`

Expected: model 6 passed、source 21 passed；ruff/mypy 无新增问题。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/model.py stableagent/supervisor/source.py \
  tests/unit/test_supervisor_model.py tests/unit/test_github_source.py
git commit -m "feat: map ci status to pr or repository threads"
```

### Task 5：poll 分页、排序、去重、删除与错误健康（M2-5）

**Files:**
- Modify: `stableagent/supervisor/source.py`
- Modify: `tests/unit/test_control_boundaries.py`
- Test: `tests/unit/test_github_source.py`

**Interfaces:**
- Consumes: `SupervisorStore.{get_source_cursor, insert_trigger, upsert_thread, insert_channel,
  put_source_cursor}`、`SourceCursor` / `SourceHealth`。
- Produces: `GitHubSource.poll(*, limit=100, max_pages=10) -> PollBatch`，满足 `Source`：
  - 每轮最多 `max_pages` 页；未读完时把 `next_cursor` 持久化，下一轮从该 cursor 继续
  - 批内按 `external_id` 去重，输出按 `(occurred_at, external_id)` 升序
  - 与库内 `UNIQUE(source, external_id)` 重复的事件不返回，计入 `duplicates`
  - Trigger / Thread / Channel / cursor 在**同一个事务**内写
  - `SourceTransientError` → cursor `degraded`；其余 `SourceError` → `failed`；都先落健康再抛出

- [ ] **Step 1: 追加失败测试**

导入补充：

```python
import contextlib

from stableagent.supervisor.model import SourceHealth
from stableagent.supervisor.source import Source
```

```python
def _poll_source(
    tmp_path: pathlib.Path,
    items: list[GitHubReadItem],
    *,
    page_size: int | None = None,
):
    clock = FixedClock()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    client = FakeGitHubReadClient(items, page_size=page_size)
    source = GitHubSource(
        store, client, repository="owner/repo", bot_login="sa-agent[bot]", clock=clock
    )
    return source, store, client


async def test_poll_pages_sorts_dedupes_and_writes_in_one_transaction(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 2, "state": "open", "user": {"login": "alice"}},
            occurred_at=200,
        ),
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
            occurred_at=100,
        ),
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 2, "state": "open", "user": {"login": "alice"}},
            occurred_at=200,
        ),
    ]
    source, store, client = _poll_source(tmp_path, items, page_size=2)
    assert isinstance(source, Source)

    batch = await source.poll(limit=10)

    assert [event.thread.external_ref for event in batch.events] == ["#1", "#2"]
    assert batch.duplicates == 1
    assert batch.next_cursor is None
    assert [call["cursor"] for call in client.calls] == [None, "offset:2"]
    assert store.get_trigger_by_external("github", "github:owner/repo:issue:1") is not None
    cursor = store.get_source_cursor("github")
    assert cursor is not None
    assert cursor.cursor is None
    assert cursor.health is SourceHealth.OK
    assert cursor.consecutive_failures == 0


async def test_second_poll_skips_triggers_already_in_the_store(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
        )
    ]
    source, _, _ = _poll_source(tmp_path, items)
    assert len((await source.poll(limit=10)).events) == 1

    again = await source.poll(limit=10)

    assert again.events == []
    assert again.duplicates == 1


async def test_poll_stops_at_max_pages_and_resumes_from_cursor(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": number, "state": "open", "user": {"login": "alice"}},
            occurred_at=number,
        )
        for number in (1, 2, 3)
    ]
    source, store, _ = _poll_source(tmp_path, items, page_size=1)

    first = await source.poll(limit=10, max_pages=1)
    assert [event.thread.external_ref for event in first.events] == ["#1"]
    assert first.next_cursor == "offset:1"
    stored = store.get_source_cursor("github")
    assert stored is not None and stored.cursor == "offset:1"

    second = await source.poll(limit=10, max_pages=2)
    assert [event.thread.external_ref for event in second.events] == ["#2", "#3"]
    assert second.next_cursor is None
    stored = store.get_source_cursor("github")
    assert stored is not None and stored.cursor is None


@pytest.mark.parametrize(
    ("error", "health"),
    [
        (SourceTransientError("503"), SourceHealth.DEGRADED),
        (SourceAuthError("403"), SourceHealth.FAILED),
        (SourceGoneError("404"), SourceHealth.FAILED),
        (SourceSchemaError("bad payload"), SourceHealth.FAILED),
    ],
)
async def test_poll_records_cursor_health_before_reraising(
    tmp_path: pathlib.Path, error: Exception, health: SourceHealth
) -> None:
    source, store, client = _poll_source(tmp_path, [])
    client.fail_next = error

    with pytest.raises(type(error)):
        await source.poll(limit=10)

    cursor = store.get_source_cursor("github")
    assert cursor is not None
    assert cursor.health is health
    assert cursor.consecutive_failures == 1


async def test_malformed_item_fails_the_whole_batch_without_writing(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
        ),
        _read_item(GitHubReadKind.ISSUE, {"state": "open", "user": {"login": "alice"}}),
    ]
    source, store, _ = _poll_source(tmp_path, items)

    with pytest.raises(SourceSchemaError):
        await source.poll(limit=10)

    assert store.list_triggers() == []
    assert store.list_threads() == []
    cursor = store.get_source_cursor("github")
    assert cursor is not None and cursor.health is SourceHealth.FAILED


async def test_deleted_event_closes_the_thread_and_still_records_the_trigger(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
            deleted=True,
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)

    batch = await source.poll(limit=10)

    assert len(batch.events) == 1
    assert batch.events[0].thread.status is ThreadStatus.CLOSED
    thread = store.get_thread("issue:owner/repo#1")
    assert thread is not None and thread.status is ThreadStatus.CLOSED


async def test_poll_drops_our_own_bot_events_but_still_advances(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 1, "issue_number": 7, "user": {"login": "sa-agent[bot]"}},
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)

    batch = await source.poll(limit=10)

    assert batch.events == []
    assert batch.duplicates == 0
    assert store.get_source_cursor("github") is not None


async def test_poll_writes_state_in_a_single_transaction(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 1, "issue_number": 7, "user": {"login": "alice"}},
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)
    seen: list[bool] = []
    original = store.transaction

    @contextlib.contextmanager
    def watched():
        with original():
            seen.append(store.in_transaction)
            yield

    monkeypatch.setattr(store, "transaction", watched)

    await source.poll(limit=10)

    assert seen == [True]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py -k poll`

Expected: FAIL —— `AttributeError: 'GitHubSource' object has no attribute 'poll'`。

- [ ] **Step 3: 导入 cursor 记录**

```python
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    SourceCursor,
    SourceHealth,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
```

- [ ] **Step 4: 在 `GitHubSource` 内追加 `poll` / `_fetch` / `_persist` / `_dedupe` / `_record_failure`**

L3 静态检查（`tests/unit/test_execution_boundaries.py`）禁止在同一函数体内同时出现
`await` 与 `Store.transaction()`：`poll` 只做编排，`_fetch` 只做异步读，`_persist`
只做同步事务写，`_record_failure` 自己持有一个事务。

```python
    async def poll(self, *, limit: int = 100, max_pages: int = 10) -> PollBatch:
        stored = self._store.get_source_cursor("github")
        cursor = stored.cursor if stored else None
        try:
            events, next_cursor, in_batch_duplicates = await self._fetch(cursor, limit, max_pages)
        except SourceError as error:
            self._record_failure(stored, error)
            raise
        return self._persist(events, next_cursor, in_batch_duplicates)

    async def _fetch(
        self, cursor: str | None, limit: int, max_pages: int
    ) -> tuple[list[SourceEvent], str | None, int]:
        next_cursor = cursor
        items: list[GitHubReadItem] = []
        for _ in range(max_pages):
            page = await self._client.list_events(
                repository=self._repository, cursor=next_cursor, limit=limit
            )
            items.extend(page.items)
            if page.next_cursor is not None and page.next_cursor == next_cursor:
                raise SourceSchemaError("cursor 未推进")
            next_cursor = page.next_cursor
            if next_cursor is None:
                break
        events, duplicates = self._dedupe(items)
        return events, next_cursor, duplicates

    def _persist(
        self, events: list[SourceEvent], next_cursor: str | None, in_batch_duplicates: int
    ) -> PollBatch:
        with self._store.transaction():
            fresh: list[SourceEvent] = []
            duplicates = 0
            for event in events:
                if not self._store.insert_trigger(event.trigger):
                    duplicates += 1
                    continue
                self._store.upsert_thread(event.thread)
                if event.channel is not None:
                    self._store.insert_channel(event.channel)
                fresh.append(event)
            self._store.put_source_cursor(
                SourceCursor(
                    source="github",
                    cursor=next_cursor,
                    last_success_at=self._clock.now_ms(),
                    consecutive_failures=0,
                    health=SourceHealth.OK,
                )
            )
        return PollBatch(
            events=fresh,
            next_cursor=next_cursor,
            duplicates=duplicates + in_batch_duplicates,
        )

    def _dedupe(self, items: list[GitHubReadItem]) -> tuple[list[SourceEvent], int]:
        by_external: dict[str, SourceEvent] = {}
        duplicates = 0
        for item in items:
            event = self.normalize(item)
            if event is None:
                continue
            if event.trigger.external_id in by_external:
                duplicates += 1
                continue
            by_external[event.trigger.external_id] = event
        ordered = sorted(
            by_external.values(),
            key=lambda event: (event.trigger.occurred_at, event.trigger.external_id),
        )
        return ordered, duplicates

    def _record_failure(self, stored: SourceCursor | None, error: SourceError) -> None:
        health = (
            SourceHealth.DEGRADED
            if isinstance(error, SourceTransientError)
            else SourceHealth.FAILED
        )
        previous = stored.consecutive_failures if stored else 0
        with self._store.transaction():
            self._store.put_source_cursor(
                SourceCursor(
                    source="github",
                    cursor=stored.cursor if stored else None,
                    last_success_at=stored.last_success_at if stored else None,
                    consecutive_failures=previous + 1,
                    health=health,
                )
            )
```

- [ ] **Step 5: 调整 P6 方向锁测试**

`supervisor` 是 P6 新顶层，按 ADR-06 合法导入 `integrations`；P5 的"旧包不得导入
P5 层"用例必须把 P6 排除，同时补一条反向断言（旧包不得导入 supervisor）。

```python
P6_PACKAGES = ("supervisor",)


def _legacy_packages() -> list[str]:
    """ROOT 下除 P5 三新包与 P6 supervisor 以外的全部包目录。"""
    excluded = {*P5_PACKAGES, *P6_PACKAGES, "__pycache__"}
    return sorted(
        path.name for path in ROOT.iterdir() if path.is_dir() and path.name not in excluded
    )


def test_no_package_imports_supervisor() -> None:
    """L2 反向：supervisor 在最上游，旧包与 P5 包都不许 import 它。"""
    offenders: list[tuple[str, list[str]]] = []
    for module in _modules(*(_legacy_packages() + list(P5_PACKAGES))):
        banned = sorted(
            name for name in _imports_of(module) if name.startswith("stableagent.supervisor")
        )
        if banned:
            offenders.append((_label(module), banned))
    assert not offenders
```

- [ ] **Step 6: 跑测试与静态检查**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py tests/unit/test_control_boundaries.py tests/unit/test_execution_boundaries.py`
`.venv/bin/ruff check stableagent/supervisor/source.py tests/unit/test_github_source.py tests/unit/test_control_boundaries.py`
`.venv/bin/ruff format --check stableagent/supervisor/source.py tests/unit/test_github_source.py tests/unit/test_control_boundaries.py`
`.venv/bin/mypy`

Expected: 全绿（source 32 + 两个边界文件共 45）；ruff/mypy 无新增问题。

- [ ] **Step 7: 提交**

```bash
git add stableagent/supervisor/source.py tests/unit/test_github_source.py tests/unit/test_control_boundaries.py
git commit -m "feat: poll github with cursors dedupe and source health"
```

### Task 6：CI FOCUS 28 → 29（M2-7）

**Files:**
- Modify: `tests/unit/test_ci_focus_gate.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `supervisor/source.py`（Task 1~5 完成）。
- Produces: 心脏闸门 29 文件；契约测试同时钉住数量、步骤名与"禁止通配兜底"。

- [ ] **Step 1: 先改契约测试（红）**

`FOCUS_FILES` 末尾追加 `"supervisor/source.py",`；测试函数与断言改为：

```python
def test_focus_covers_the_twenty_nine_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 29
    assert "29 文件" in text  # 步骤名与注释同步
    assert text.count('".py"') == 0  # 不许用通配兜底
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py > /tmp/p6-m2-focus-red.txt 2>&1`

Read: `/tmp/p6-m2-focus-red.txt`

Expected: FAIL —— `"supervisor/source.py"` 不在工作流中（第 2 行断言）。

- [ ] **Step 3: 同步工作流**

步骤名改为：

```yaml
      - name: 心脏模块覆盖率闸门（29 文件：loop/checkpoint/errors/context/evidence/orchestration/control/execution/integrations/supervisor 各 ≥90%）
```

`FOCUS` 元组在 `"supervisor/views.py",` 之后追加：

```python
              "supervisor/source.py",
```

- [ ] **Step 4: 跑契约测试确认通过**

Run: `.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py > /tmp/p6-m2-focus-green.txt 2>&1`

Read: `/tmp/p6-m2-focus-green.txt`

Expected: `1 passed`。

- [ ] **Step 5: 全量测试并实测 `source.py` 覆盖率**

```bash
.venv/bin/pytest -q --cov=stableagent --cov-report=xml > /tmp/p6-m2-pytest.txt 2>&1
.venv/bin/python - <<'PY' > /tmp/p6-m2-coverage.txt
import xml.etree.ElementTree as ET

root = ET.parse("coverage.xml").getroot()
for cls in root.iter("class"):
    if cls.get("filename") == "supervisor/source.py":
        print("supervisor/source.py", cls.get("line-rate"))
PY
```

Read: `/tmp/p6-m2-pytest.txt`、`/tmp/p6-m2-coverage.txt`

Expected: `0 failed`；`supervisor/source.py` 行覆盖率 ≥ `0.9`（CI 闸门口径）。

本机提示：若 shell 的 `NO_PROXY` 含 `[::1]`，macOS `getproxies()` 会让 httpx 把
`no` 当代理 scheme（`Invalid port: ':1]'`）。跑全量前用
`env -u NO_PROXY -u no_proxy` 包一层即可；该失败与仓库代码无关。

- [ ] **Step 6: 提交**

```bash
git add tests/unit/test_ci_focus_gate.py .github/workflows/ci.yml
git commit -m "ci: include supervisor source in the cardiac coverage gate"
```

### Task 7：Source 部署文档与 M2 出口（M2-6 + M2-8）

**Files:**
- Create: `docs/guides/supervisor-github-source.md`
- Modify: `docs/acceptance/p6-supervisor-beta.md`、`README.md`、`docs/README.md`、
  `docs/acceptance/README.md`、`docs/Roadmap.md`、`CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1~6 的全部产物。
- Produces: 可操作的 polling 部署说明；M2 出口证据；状态面同步。

- [ ] **Step 1: 写部署与 cursor 契约文档**

`docs/guides/supervisor-github-source.md` 全文：

```markdown
# GitHub Source 部署与 cursor 契约（P6 M2）

本指南说明 P6 Beta 的 GitHub 事件接入：只有 polling 一条主路径，只读、单仓库、
`observe` 模式。接入只产生 Trigger/Thread/Channel 候选，**不产生任何授权**；建票与
执行仍由 P5 Policy/Mandate 决定。

## 1. 只读权限

读 token 与 P5 的写 token 分离：

| 权限 | 级别 | 用途 |
|---|---|---|
| Metadata | Read | 仓库可见性 |
| Issues | Read | Issue 与 issue comment |
| Pull requests | Read | PR、review、review comment |
| Actions / Checks | Read | CI status |

读 token **不得**包含 Issues/Pull requests 写权限，也不得授予 `contents: write`。
凭据只存在于真实客户端内部（L7）：不进 prompt、日志、Report、Ticket、AgentState、
worktree 元数据或 effect payload。

## 2. polling 主路径

每一轮调用 `GitHubSource.poll(limit=100, max_pages=10)`：

1. 从 `source_cursors` 读出 `source='github'` 的 cursor；首次为空。
2. 最多读 `max_pages` 页；未读完时把 `next_cursor` 持久化，下一轮从该 cursor 继续。
3. 整批标准化后按 `(occurred_at, external_id)` 升序输出。
4. Trigger、Thread、Channel 与 cursor 在**同一个 `BEGIN IMMEDIATE` 事务**内写；
   任一步失败整批回滚，不留下半个事实。

cursor 对调用方是不透明字符串。真实客户端必须在内部编码每种资源的分页位置并保证
前进；`FakeGitHubReadClient` 用 `offset:<n>` 模拟。若客户端返回与输入相同的 cursor，
Source 以 `source.schema` 失败，避免死循环。

## 3. 事件映射

| kind | external_id | Thread key | Channel |
|---|---|---|---|
| issue | `github:{repo}:issue:{n}` | `issue:{repo}#{n}` | 无 |
| pull_request | `github:{repo}:pull_request:{n}` | `pr:{repo}#{n}` | 无 |
| issue_comment | `github:{repo}:issue_comment:{id}` | Issue 或 PR thread | `#n`（ISSUE_COMMENT / PULL_REQUEST_COMMENT） |
| review_comment | `github:{repo}:review_comment:{id}` | `review:{repo}#{pull}:{review_id}` | `review:{review_id}`（REVIEW_REPLY） |
| review | `github:{repo}:review:{id}` | `review:{repo}#{pull}:{review_id}` | `review:{review_id}`（REVIEW_REPLY） |
| ci_status | `github:{repo}:ci_status:{id}` | 有 PR 用 `pr:{repo}#{n}`，否则 `repo:{repo}` | 无 |

Thread 状态：Issue/PR `state == "closed"`、review `state == "dismissed"` 或删除事件
一律落 `closed`；`upsert_thread` 只允许状态单调前进（`open → closed → stopped`）。

## 4. 去重

- 数据库：`triggers` 以 `UNIQUE(source, external_id)` 兜底；重复插入返回 false。
- 记录：Trigger id 由 `sha256(external_id)` 派生，重启后仍是同一个 id。
- 批内：同一 `external_id` 只保留首次出现，重复计入 `PollBatch.duplicates`。
- 自身事件：`actor` 与配置的 `bot_login` 大小写无关地比较，命中即丢弃，不进入候选。

## 5. 错误与健康

| 错误 | code | cursor health | 处理 |
|---|---|---|---|
| 网络 / 限流 / 5xx | `source.transient` | `degraded` | 下一轮重试，`consecutive_failures` 递增 |
| 403 | `source.auth` | `failed` | 人工检查 token 与可见性 |
| 404 | `source.gone` | `failed` | 仓库不可见或事件已删除，停止重试 |
| 载荷不符合契约 | `source.schema` | `failed` | 修复映射或 fixture 后再启动 |

失败时整批不落库；健康记录在抛错前写入，监管视图的 `source_health` 因此能看到原因。

## 6. webhook 的后续接法

P6 Beta 不启用 webhook。后续若接入：

- webhook 只负责把同一份 GitHub 资源转换成 `GitHubReadItem`，走同一个 `normalize`。
- 去重仍只在 Trigger 层做（`UNIQUE(source, external_id)`），不新增第二条写路径。
- 在 M6 之前不改变 `observe` 锁定，不因 webhook 到达而自动建票。

## 7. 安全边界

- 外部文本（Issue/PR 正文、评论、CI 日志）只进 Trigger payload 与 digest，永不参与准入（L4）。
- Channel 只是回写目的地，不携带授权（ADR-06 决策 4）。
- 读权限不扩大 Mandate；Subscription 的 `context` 模式同样不扩大（I3）。
```

- [ ] **Step 2: 全量门禁（读数落盘后读回）**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m2-full.txt 2>&1
.venv/bin/ruff check stableagent tests examples benchmarks > /tmp/p6-m2-ruff.txt 2>&1
.venv/bin/ruff format --check stableagent tests examples benchmarks > /tmp/p6-m2-format.txt 2>&1
.venv/bin/mypy > /tmp/p6-m2-mypy.txt 2>&1
```

Read: 四个 `/tmp/p6-m2-*.txt`

Expected: `0 failed`；`All checks passed!`；`... files already formatted`；
`Success: no issues found`。

- [ ] **Step 3: 三解释器等价**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m2-py310.txt 2>&1
env -u NO_PROXY -u no_proxy .matrix/py311/bin/pytest -q > /tmp/p6-m2-py311.txt 2>&1
env -u NO_PROXY -u no_proxy .matrix/py312/bin/pytest -q > /tmp/p6-m2-py312.txt 2>&1
```

Read: 三份 `/tmp/p6-m2-py31*.txt`

Expected: 三份末行均为 `0 failed`，passed 数一致（跳过数可不同）。

- [ ] **Step 4: 边界复核**

```bash
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m2-l1.txt
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py > /tmp/p6-m2-c1.txt
grep -rnE "(from|import) +stableagent\.supervisor" --include='*.py' \
  stableagent/core stableagent/orchestration stableagent/control \
  stableagent/execution stableagent/integrations > /tmp/p6-m2-imports.txt
git diff --check > /tmp/p6-m2-check.txt 2>&1
```

Read: 四份 `/tmp/p6-m2-*.txt`

Expected: 前两份 0 行；`grep` 无命中；`git diff --check` 无输出。

- [ ] **Step 5: 回填账本**

1. 第 1 节 M2-1~M2-8 改为 `[x]`，每条追加：提交号、测试文件与用例数、覆盖率读数、
   三解释器结论。
2. 第 4 节补一句："M2 展开为 **7 个 Task / 43 个 Step**（见 §7）。"
3. 第 1 节 M2 状态行改为："**状态：M2 收口（Task 1~7 全部完成）。** 证据提交：…"

- [ ] **Step 6: 同步状态面**

- `README.md` P6 行：`🟡 active — M2 done (8/8); milestones 2/6`。
- `docs/README.md`：当前主线改为 P6 M2 完成；阶段表 P6 行改为
  `进行中（M2 完成，8/8；里程碑 2/6）`；链接本账本与
  `docs/guides/supervisor-github-source.md`。
- `docs/acceptance/README.md`：P6 行 `Active（M2 完成 8/8；里程碑 2/6）`。
- `docs/Roadmap.md`：P6 段写明 M2 完成与下一步 M3（Router Intake）。
- `CHANGELOG.md`：新增 P6 M2 条目（只读 polling、事件映射、去重、cursor 健康、
  FOCUS 29）。

- [ ] **Step 7: 提交**

```bash
git add docs/guides/supervisor-github-source.md docs/acceptance/p6-supervisor-beta.md \
  README.md docs/README.md docs/acceptance/README.md docs/Roadmap.md CHANGELOG.md
git commit -m "docs: close p6 m2 with the github source evidence"
```

## 8. M2 自检记录（writing-plans）

- 规格覆盖：WP-6.2 第 1 条 → Task 1（`Source` / `ChannelWriter`）；第 2 条 →
  Task 2~4（六种 kind 的 fixture 与映射）；第 3 条 → Task 5 + Task 7 文档（polling 主路径、
  权限、webhook 后续接法）；第 4 条 → Task 5（cursor/分页/错误/删除/乱序/重复）；
  第 5 条 → Task 2~3 与 Task 5 的 bot 过滤用例。
- 类型一致性：`GitHubReadItem` / `GitHubReadPage` / `PollBatch` / `SourceEvent` 在 Task 1
  定义，Task 2~5 只使用不重定义；`review_thread_key` 从 Task 1 起就是三参数
  `(repository, pull_number, review_id)`；`GitHubSource.poll` 的 `max_pages` 只在 Task 5 出现。
- 无占位符：所有实现步骤给出完整代码；出口读数在 Task 7 执行时落盘后回填，不预填数字。
- 边界：integrations 侧只新增 `GitHubReadKind`，不 import supervisor（L2）；事务体内无
  `await`（L3）；Trigger payload 只进 digest 与持久化，不参与判定（L4）。

## 9. M3 任务级实施计划（writing-plans 产出，2026-11-09）

**Goal：** 让 `RouterService.receive(trigger)` 成为 Trigger → Subscription 扇出 →
Decision/Ticket 的唯一入口，并在 `observe` 锁定下完成六种 outcome 与七条命令。

**Architecture：** `supervisor/assessor.py` 只做确定性候选判断；`supervisor/router.py`
在一个 `BEGIN IMMEDIATE` 内完成去重、扇出与决策；`supervisor/service.py` 承载 operator
命令。Ticket/Assignment/Effect 的写入沿用 P5 `GitHubIntake` 的"五件事实"口径，不调用
`OrchestrationService`（其命令自带事务，嵌套会触发 `ReentrantTransactionError`）。

**Spec：** [P6 实施规格](../specs/p6-single-repository-super-agent-beta.md) §2.1–2.3 与
WP-6.3；决策来源 [ADR-06](../design-decisions/06-super-agent-router.md) 决策 3–9。

**Global Constraints（每个 Task 都隐含遵守）：**

- L1：`stableagent/core/` 对 `234f620` 零 diff；约束 1：`orchestration/service.py` 零字符改动。
- L2：方向锁 `supervisor → integrations → execution → control → orchestration → core`；
  旧包与 P5 包不得 import `stableagent.supervisor`。
- L3：`Store.transaction()` 体内不得出现 `await`；`receive` / `reevaluate` / 命令各只开
  一个事务。
- L4：外部正文只进 digest、TicketInput 与 Decision reason，不决定 action 集合；action
  集合只来自 `RouterConfig` 与 `StaticPolicyConfig`。
- L5：`resume` 的 Mandate 复检用 `when="launch"` 并写审计行。
- L6：不新增 `merge` / `enable_auto_merge` 路径；`EffectKind` 不加成员。
- L8：全部用例离线；定向测试必须加 `--no-cov`（全局 `fail_under = 70`）。

---

### Task 1：AutonomyMode 与 observe 评估器（M3-1）

**Files:**
- Modify: `stableagent/supervisor/model.py`
- Create: `stableagent/supervisor/assessor.py`
- Test: `tests/unit/test_supervisor_assessor.py`

**Interfaces:**
- Consumes: `supervisor.model` 的 `Trigger` / `Thread` / `ThreadStatus` / `TriggerKind`。
- Produces: `AutonomyMode`（`off` / `observe` / `auto`）；`AssessmentContext`、
  `CandidateAssessment`、`CandidateAssessor`（`assess(context) -> CandidateAssessment`）、
  `ObserveOnlyAssessor`。Task 2~6 只使用这些名字，不重定义。

- [ ] **Step 1: 写失败测试**

```python
"""WP-6.3：observe 唯一评估器的确定性规则。"""

from __future__ import annotations

from stableagent.supervisor.assessor import (
    AssessmentContext,
    CandidateAssessor,
    ObserveOnlyAssessor,
)
from stableagent.supervisor.model import (
    AutonomyMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)


def _trigger(kind: TriggerKind = TriggerKind.ISSUE) -> Trigger:
    return Trigger(
        id="tg-1",
        external_id="github:o/r:issue:1",
        kind=kind,
        actor="alice",
        thread_keys=["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload={"resource": {"number": 1}, "deleted": False},
    )


def _thread(status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    return Thread(
        key="issue:o/r#1",
        repository="o/r",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=1,
        status=status,
    )


def _context(**overrides: object) -> AssessmentContext:
    values: dict[str, object] = {
        "trigger": _trigger(),
        "thread": _thread(),
        "policy_version": "p5-v1",
    }
    values.update(overrides)
    return AssessmentContext(**values)


def test_autonomy_mode_has_three_values() -> None:
    assert [mode.value for mode in AutonomyMode] == ["off", "observe", "auto"]


def test_observe_only_assessor_marks_a_fresh_human_candidate() -> None:
    assessment = ObserveOnlyAssessor().assess(_context())
    assert assessment.would_accept is True
    assert assessment.reason_codes == ["inferred_candidate"]


def test_observe_only_assessor_rejects_ci_as_context_only() -> None:
    assessment = ObserveOnlyAssessor().assess(_context(trigger=_trigger(TriggerKind.CI_STATUS)))
    assert assessment.would_accept is False
    assert assessment.reason_codes == ["ci_context_only"]


def test_observe_only_assessor_rejects_non_open_threads() -> None:
    assessment = ObserveOnlyAssessor().assess(_context(thread=_thread(ThreadStatus.CLOSED)))
    assert assessment.would_accept is False
    assert assessment.reason_codes == ["thread_not_open"]


def test_observe_only_assessor_rejects_existing_tickets_and_subscriptions() -> None:
    existing = ObserveOnlyAssessor().assess(_context(existing_ticket_id="tk-1"))
    owned = ObserveOnlyAssessor().assess(_context(has_live_own_subscription=True))
    context = ObserveOnlyAssessor().assess(_context(has_live_context_subscription=True))
    assert existing.reason_codes == ["existing_ticket_context"]
    assert owned.reason_codes == ["already_owned"]
    assert context.reason_codes == ["context_only"]
    assert not existing.would_accept and not owned.would_accept and not context.would_accept


def test_structural_fake_satisfies_the_candidate_assessor_protocol() -> None:
    class Stub:
        def assess(self, context: AssessmentContext):
            return ObserveOnlyAssessor().assess(context)

    assert isinstance(Stub(), CandidateAssessor)
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_assessor.py
```

Expected: collection error `ModuleNotFoundError: stableagent.supervisor.assessor`。

- [ ] **Step 3: 最小实现**

`stableagent/supervisor/model.py` 在 `SourceHealth` 之后追加：

```python
class AutonomyMode(str, Enum):
    """分级自治：Beta 只允许 off/observe；auto 仅保留接口（规格 2.3）。"""

    OFF = "off"
    OBSERVE = "observe"
    AUTO = "auto"
```

`stableagent/supervisor/assessor.py` 全文：

```python
"""推断候选评估：窄协议 + observe 下的确定性规则（规格 2.3、WP-6.3 第 3 条）。

评估器只回答"若开启 auto 是否接受"，不写库、不创建 Ticket；失败由 Router 记 unavailable。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from stableagent.supervisor.model import Thread, ThreadStatus, Trigger, TriggerKind


class AssessmentContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: Trigger
    thread: Thread | None = None
    has_live_own_subscription: bool = False
    has_live_context_subscription: bool = False
    existing_ticket_id: str | None = None
    policy_version: str


class CandidateAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    would_accept: bool
    reason_codes: list[str] = Field(default_factory=list)


@runtime_checkable
class CandidateAssessor(Protocol):
    def assess(self, context: AssessmentContext) -> CandidateAssessment: ...


class ObserveOnlyAssessor:
    """Beta 的确定性评估器；规则顺序即 reason code 的优先级。"""

    def assess(self, context: AssessmentContext) -> CandidateAssessment:
        if context.trigger.kind is TriggerKind.CI_STATUS:
            return CandidateAssessment(would_accept=False, reason_codes=["ci_context_only"])
        if context.thread is not None and context.thread.status is not ThreadStatus.OPEN:
            return CandidateAssessment(would_accept=False, reason_codes=["thread_not_open"])
        if context.existing_ticket_id is not None:
            return CandidateAssessment(
                would_accept=False, reason_codes=["existing_ticket_context"]
            )
        if context.has_live_own_subscription:
            return CandidateAssessment(would_accept=False, reason_codes=["already_owned"])
        if context.has_live_context_subscription:
            return CandidateAssessment(would_accept=False, reason_codes=["context_only"])
        return CandidateAssessment(would_accept=True, reason_codes=["inferred_candidate"])
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_assessor.py
.venv/bin/ruff check stableagent/supervisor/assessor.py stableagent/supervisor/model.py
.venv/bin/ruff format --check stableagent/supervisor/assessor.py
.venv/bin/mypy
```

Expected: `7 passed`；ruff `All checks passed!`；mypy `Success`。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/model.py stableagent/supervisor/assessor.py \
  tests/unit/test_supervisor_assessor.py
git commit -m "feat: add supervisor autonomy mode and observe-only assessor"
```

---

### Task 2：Source request 信号与决策 attempt 仓储（M3-2）

**Files:**
- Modify: `stableagent/supervisor/source.py`
- Modify: `stableagent/supervisor/store.py`
- Test: `tests/unit/test_github_source.py`、`tests/unit/test_supervisor_store.py`

**Interfaces:**
- Consumes: `GitHubSource._trigger` 已有的 `bot_login`；`DecisionOutcome`。
- Produces: Trigger payload 的 `request = {"text": str, "mentioned": bool, "url": str | None}`；
  `SupervisorStore.next_decision_attempt(digest) -> int`；
  `SupervisorStore.find_accepted_decision(digest) -> Decision | None`。
- 兼容：M2 的 `payload == {...}` 断言使用无正文 fixture，payload 不变。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_github_source.py` 末尾追加：

```python
def test_comment_body_carries_a_normalized_request_signal(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {
                "id": 21,
                "issue_number": 7,
                "user": {"login": "alice"},
                "body": "please @SA-Agent take a look",
                "html_url": "https://github.com/owner/repo/issues/7#issuecomment-21",
            },
        )
    )
    assert event is not None
    assert event.trigger.payload["request"] == {
        "text": "please @SA-Agent take a look",
        "mentioned": True,
        "url": "https://github.com/owner/repo/issues/7#issuecomment-21",
    }


def test_plain_body_and_ci_status_have_no_false_mention(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    comment = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {
                "id": 22,
                "issue_number": 7,
                "user": {"login": "alice"},
                "body": "unrelated note",
            },
        )
    )
    ci = source.normalize(
        _read_item(
            GitHubReadKind.CI_STATUS,
            {"id": 501, "sha": "abc123", "state": "success", "pull_number": 9},
        )
    )
    assert comment is not None and ci is not None
    assert comment.trigger.payload["request"]["mentioned"] is False
    assert comment.trigger.payload["request"]["url"] is None
    assert "request" not in ci.trigger.payload
```

在 `tests/unit/test_supervisor_store.py` 末尾追加：

```python
def test_next_decision_attempt_counts_per_digest(store: SupervisorStore) -> None:
    assert store.next_decision_attempt("digest-a") == 1
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=[],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=["x"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=1,
            created_at=1,
        )
    )
    assert store.next_decision_attempt("digest-a") == 2
    assert store.next_decision_attempt("digest-b") == 1


def test_find_accepted_decision_skips_observe_rows(store: SupervisorStore) -> None:
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=[],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=["x"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=1,
            created_at=1,
        )
    )
    store.insert_decision(
        Decision(
            id="dc-2",
            trigger_ids=[],
            outcome=DecisionOutcome.ACCEPT,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=["admitted"],
            policy_version="p5-v1",
            request_digest="digest-a",
            attempt=2,
            created_at=2,
        )
    )
    found = store.find_accepted_decision("digest-a")
    assert found is not None
    assert found.id == "dc-2"
    assert store.find_accepted_decision("digest-missing") is None
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py \
  tests/unit/test_supervisor_store.py
```

Expected: 2 个用例因 `KeyError: 'request'` / `AttributeError: next_decision_attempt` 失败。

- [ ] **Step 3: 实现 Source request 块**

`stableagent/supervisor/source.py` 的 `_trigger` 改为：

```python
    def _trigger(
        self,
        item: GitHubReadItem,
        kind: TriggerKind,
        subject: int,
        actor: str,
        thread_key: str,
    ) -> Trigger:
        external_id = f"github:{self._repository}:{kind.value}:{subject}"
        payload: dict[str, Any] = {"resource": item.resource, "deleted": item.deleted}
        request = self._request_block(item.resource)
        if request is not None:
            payload["request"] = request
        return Trigger(
            id=stable_trigger_id(external_id),
            external_id=external_id,
            kind=kind,
            actor=actor,
            thread_keys=[thread_key],
            occurred_at=item.occurred_at,
            received_at=self._clock.now_ms(),
            payload=payload,
        )

    def _request_block(self, resource: dict[str, Any]) -> dict[str, Any] | None:
        """只把"有人写了正文"的事件标成候选请求；mention 命中不决定 action 集合（L4）。"""
        body = resource.get("body")
        if not isinstance(body, str) or not body:
            return None
        handle = self._bot_login.removesuffix("[bot]")
        url = resource.get("html_url")
        return {
            "text": body,
            "mentioned": f"@{handle}".casefold() in body.casefold(),
            "url": url if isinstance(url, str) else None,
        }
```

- [ ] **Step 4: 实现 Store 决策 helper**

`stableagent/supervisor/store.py` 在 `find_accepted_decision` 之前/之后追加（放在
`list_decisions` 之后）：

```python
    def next_decision_attempt(self, request_digest: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) + 1 FROM decisions WHERE request_digest = ?",
            (request_digest,),
        ).fetchone()
        return int(row[0])

    def find_accepted_decision(self, request_digest: str) -> Decision | None:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE request_digest = ? AND outcome = ?"
            " ORDER BY attempt ASC LIMIT 1",
            (request_digest, DecisionOutcome.ACCEPT.value),
        ).fetchone()
        return self._row_to_decision(row) if row else None
```

- [ ] **Step 5: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py \
  tests/unit/test_supervisor_store.py
.venv/bin/ruff check stableagent/supervisor/source.py stableagent/supervisor/store.py
.venv/bin/ruff format --check stableagent/supervisor/source.py stableagent/supervisor/store.py
.venv/bin/mypy
```

Expected: `40 passed`（M2 32 + M3 2 + Store 新增 2）；ruff / mypy 全绿。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/source.py stableagent/supervisor/store.py \
  tests/unit/test_github_source.py tests/unit/test_supervisor_store.py
git commit -m "feat: normalize github request signals and decision attempts"
```

---

### Task 3：receive 原子推进、扇出与三档决策（M3-3 / M3-4 / M3-5）

**Files:**
- Create: `stableagent/supervisor/router.py`
- Test: `tests/unit/test_supervisor_router.py`

**Interfaces:**
- Consumes: `AutonomyMode` / `ObserveOnlyAssessor`（Task 1）、`next_decision_attempt` /
  `find_accepted_decision`（Task 2）、P5 `StaticPolicy.admit_explicit` / `create_mandate`。
- Produces: `RouterConfig`、`RouterError` 五子类、`RequestSignal`、`Reception`、
  `record_decision()`、`ensure_subscription()`、`latest_trigger()`、`RouterService.receive()`。
  Task 4 只新增 `reevaluate()` 与 unavailable 分支；Task 5/6 只调用既有 helper。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_router.py` 全文：

```python
"""WP-6.3：receive 的原子推进、扇出与三档决策。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind, TicketStatus
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    DecisionOutcome,
    DecisionStrength,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import (
    RequestSignal,
    RouterConfig,
    RouterModeError,
    RouterService,
    ensure_subscription,
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _thread(key: str = "issue:o/r#1", *, status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    kind = ThreadKind.PULL_REQUEST if key.startswith("pr:") else ThreadKind.ISSUE
    ref = key.rsplit("#", 1)[-1]
    return Thread(
        key=key,
        repository="o/r",
        kind=kind,
        external_ref=f"#{ref}",
        last_trigger_at=1,
        status=status,
    )


def _trigger(
    *,
    external_id: str = "github:o/r:issue:1",
    kind: TriggerKind = TriggerKind.ISSUE,
    actor: str = "alice",
    thread_keys: list[str] | None = None,
    text: str | None = None,
    mentioned: bool = True,
    request: Any = None,
) -> Trigger:
    payload: dict[str, Any] = {
        "resource": {"number": 1, "user": {"login": actor}},
        "deleted": False,
    }
    if request is not None:
        payload["request"] = request
    elif text is not None:
        payload["request"] = {"text": text, "mentioned": mentioned, "url": None}
    return Trigger(
        id=f"tg-{external_id}",
        external_id=external_id,
        kind=kind,
        actor=actor,
        thread_keys=thread_keys or ["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload=payload,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> SequenceIds:
    return SequenceIds()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=clock)


@pytest.fixture
def policy(clock: FixedClock, ids: SequenceIds) -> StaticPolicy:
    return StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.RUN_COMMAND}),
            allowed_commands=["pytest"],
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )


def _router(
    store: SupervisorStore,
    policy: StaticPolicy,
    clock: FixedClock,
    ids: SequenceIds,
    *,
    mode: AutonomyMode = AutonomyMode.OBSERVE,
    audit: AuditWriter | None = None,
    repository: str = "o/r",
) -> RouterService:
    return RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        RouterConfig(
            repository=repository,
            mode=mode,
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
        audit=audit,
    )


def _accept(
    router: RouterService,
    store: SupervisorStore,
    *,
    thread: str = "issue:o/r#1",
    text: str = "fix it",
    external_id: str = "github:o/r:issue:1",
):
    store.upsert_thread(_thread(thread))
    reception = router.receive(
        _trigger(external_id=external_id, thread_keys=[thread], text=text)
    )
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None
    return ticket


def test_router_rejects_auto_mode(store, policy, clock, ids) -> None:
    with pytest.raises(RouterModeError):
        _router(store, policy, clock, ids, mode=AutonomyMode.AUTO)


def test_request_signal_reads_only_the_normalized_request_block() -> None:
    signal = RequestSignal.from_trigger(_trigger(text="fix it"))
    assert signal is not None
    assert (signal.text, signal.mentioned, signal.stable_id) == (
        "fix it",
        True,
        "github:o/r:issue:1",
    )


def test_receive_persists_trigger_and_writes_one_observe_decision(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(_trigger())
    assert reception.duplicate is False
    assert len(reception.decision_ids) == 1
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.strength is DecisionStrength.INFERRED
    assert decision.reason_codes == ["inferred_candidate"]
    assert store.get_trigger(_trigger().id) is not None


def test_receive_duplicate_returns_duplicate_without_new_side_effects(
    store, policy, clock, ids
) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids)
    first = router.receive(_trigger())
    second = router.receive(_trigger())
    assert first.duplicate is False
    assert second.duplicate is True
    assert second.decision_ids == [] and second.input_ids == []
    assert len(store.list_decisions(trigger_id=_trigger().id)) == 1


def test_receive_accepts_a_trigger_already_persisted_by_the_source(
    store, policy, clock, ids
) -> None:
    store.upsert_thread(_thread())
    store.insert_trigger(_trigger())
    reception = _router(store, policy, clock, ids).receive(_trigger())
    assert reception.duplicate is False
    assert len(reception.decision_ids) == 1


def test_off_mode_ignores_inferred_without_consulting_the_assessor(
    store, policy, clock, ids
) -> None:
    class Bomb:
        def assess(self, context):  # pragma: no cover - 被调用即失败
            raise AssertionError("off 模式不得调用评估器")

    store.upsert_thread(_thread())
    router = RouterService(
        store,
        policy,
        Bomb(),
        RouterConfig(repository="o/r", mode=AutonomyMode.OFF, operators=frozenset({"alice"})),
        clock=clock,
        ids=ids,
    )
    reception = router.receive(_trigger())
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.reason_codes == ["autonomy_off"]


def test_explicit_admission_creates_seven_facts_atomically(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    audit = AuditWriter(policy, store, clock=clock, ids=ids)
    router = _router(store, policy, clock, ids, audit=audit)
    reception = router.receive(_trigger(text="fix the bug"))
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None and ticket.status is TicketStatus.QUEUED
    mandate = store.get_mandate(ticket.mandate_id)
    assert mandate is not None
    assert mandate.origin_kind == "github"
    assert mandate.requester == "alice"
    assert mandate.scope == "github:o/r:issue:1"
    assert mandate.actions == ["read_repository", "run_command"]
    assert [(item.kind, item.payload) for item in store.list_inputs(ticket.id)] == [
        ("task", "fix the bug")
    ]
    assert len(store.list_assignments(ticket.id)) == 1
    assert [effect.kind for effect in store.due_effects(clock.now_ms())] == [
        EffectKind.LAUNCH_ASSIGNMENT
    ]
    assert [
        subscription.mode
        for subscription in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    ] == [SubscriptionMode.OWN]
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ACCEPT
    assert decision.strength is DecisionStrength.EXPLICIT
    assert decision.mandate_id == mandate.id
    assert decision.reason_codes == ["admitted"]
    assert len(store.list_audit(kind="admit")) == 1


def test_explicit_rejection_records_not_admitted_ignore(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids)
    reception = router.receive(_trigger(actor="mallory", text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.strength is DecisionStrength.NOT_ADMITTED
    assert decision.reason_codes == ["actor_not_allowlisted"]
    assert store.list_tickets() == [] and reception.ticket_id is None


def test_explicit_without_a_mention_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(_trigger(text="fix it", mentioned=False))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["mention_required"]


def test_router_and_policy_repository_mismatch_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids, repository="other/r")
    reception = router.receive(_trigger(text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["repository_mismatch"]
    assert store.list_tickets() == []


def test_malformed_request_block_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(
        _trigger(request={"text": 1, "mentioned": True})
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.strength is DecisionStrength.NOT_ADMITTED
    assert decision.reason_codes == ["malformed_request"]


def test_explicit_without_a_thread_row_is_ignored(store, policy, clock, ids) -> None:
    reception = _router(store, policy, clock, ids).receive(_trigger(text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["thread_unknown"]
    assert store.list_tickets() == []


def test_followup_on_an_owned_thread_attaches_without_a_new_ticket(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:2",
            kind=TriggerKind.ISSUE_COMMENT,
            text="also add tests",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ATTACH
    assert decision.strength is DecisionStrength.EXPLICIT
    assert decision.reason_codes == ["owned_thread_followup"]
    assert reception.ticket_id == ticket.id
    assert len(store.list_tickets()) == 1
    assert len(store.list_inputs(ticket.id)) == 2


def test_duplicate_request_digest_attaches_to_the_existing_ticket(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store, text="fix it")
    store.upsert_thread(_thread("issue:o/r#2"))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue:2",
            thread_keys=["issue:o/r#2"],
            text="fix it",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ATTACH
    assert decision.reason_codes == ["duplicate_request"]
    assert decision.attempt == 2
    assert reception.ticket_id == ticket.id
    assert len(store.list_tickets()) == 1
    subscriptions = {
        (item.thread_key, item.mode)
        for item in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    }
    assert ("issue:o/r#2", SubscriptionMode.CONTEXT) in subscriptions


def test_followup_on_a_stopped_ticket_is_a_stop_decision(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.CANCELLED}))
    before = len(store.list_inputs(ticket.id))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:3",
            kind=TriggerKind.ISSUE_COMMENT,
            text="please continue",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.STOP
    assert decision.reason_codes == ["stopped_ignored"]
    assert reception.ticket_id == ticket.id
    assert len(store.list_inputs(ticket.id)) == before


def test_followup_on_a_completed_ticket_keeps_the_input_and_observes(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:4",
            kind=TriggerKind.ISSUE_COMMENT,
            text="one more thing",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["resume_requires_operator"]
    assert len(store.list_inputs(ticket.id)) == 2
    assert EffectKind.DELIVER_INPUT not in {
        effect.kind for effect in store.due_effects(clock.now_ms())
    }


def test_multiple_owners_observe_ambiguous_without_a_new_ticket(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    first = _accept(router, store, thread="issue:o/r#1", text="first task")
    second = _accept(
        router,
        store,
        thread="issue:o/r#2",
        text="second task",
        external_id="github:o/r:issue:2",
    )
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=second.id,
        thread_key="issue:o/r#1",
        mode=SubscriptionMode.OWN,
        created_at=clock.now_ms(),
    )
    reception = router.receive(_trigger(text="third task"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["ambiguous_owner"]
    assert len(store.list_tickets()) == 2
    assert first.id != second.id


def test_fanout_delivers_one_input_per_ticket_across_thread_keys(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    first = _accept(router, store, thread="issue:o/r#1", text="first task")
    second = _accept(
        router,
        store,
        thread="issue:o/r#2",
        text="second task",
        external_id="github:o/r:issue:2",
    )
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=second.id,
        thread_key="issue:o/r#1",
        mode=SubscriptionMode.CONTEXT,
        created_at=clock.now_ms(),
    )
    reception = router.receive(
        _trigger(thread_keys=["issue:o/r#1", "issue:o/r#2"])
    )
    assert reception.fanned_out == 2
    assert len(store.list_inputs(first.id)) == 2
    assert len(store.list_inputs(second.id)) == 2
    deliver = [
        effect
        for effect in store.due_effects(clock.now_ms())
        if effect.kind is EffectKind.DELIVER_INPUT
    ]
    assert len(deliver) == 2


def test_terminal_subscriptions_are_not_fanned_out(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    before = len(store.list_inputs(ticket.id))
    reception = router.receive(_trigger())
    assert reception.fanned_out == 0
    assert len(store.list_inputs(ticket.id)) == before


def test_context_subscription_never_expands_the_mandate(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store, thread="issue:o/r#1", text="first task")
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=ticket.id,
        thread_key="issue:o/r#2",
        mode=SubscriptionMode.CONTEXT,
        created_at=clock.now_ms(),
    )
    mandate_before = store.get_mandate(ticket.mandate_id)
    router.receive(_trigger(thread_keys=["issue:o/r#2"]))
    mandate_after = store.get_mandate(ticket.mandate_id)
    assert mandate_before is not None and mandate_after is not None
    assert mandate_after.actions == mandate_before.actions
    assert len(store.list_tickets()) == 1
    assert len(store.list_inputs(ticket.id)) == 2
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py
```

Expected: `ModuleNotFoundError: stableagent.supervisor.router`。

- [ ] **Step 3: 实现 router.py（完整文件）**

```python
"""Router Intake：Trigger 进入领域的唯一命令（规格 WP-6.3、ADR-06 决策 3/4/6/7/8）。

方向锁：只 import supervisor 记录与 Store、P5 Policy 与 orchestration 记录；外部形态由
Source 标准化，Router 不解析 GitHub 原始字段。事务：`receive` 只开一个 BEGIN IMMEDIATE，
体内无 await（L3）；任一步失败整体回滚。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Admission, ExplicitRequest
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    Assignment,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Ticket,
    TicketInput,
    TicketStatus,
)
from stableagent.supervisor.assessor import AssessmentContext, CandidateAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    Decision,
    DecisionOutcome,
    DecisionStrength,
    Subscription,
    SubscriptionMode,
    Thread,
    Trigger,
    TriggerKind,
    canonical_json,
)
from stableagent.supervisor.store import SupervisorStore

_ALLOWED_MODES = (AutonomyMode.OFF, AutonomyMode.OBSERVE)


class RouterError(Exception):
    code = "router.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class RouterModeError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.mode", message)


class RouterPayloadError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.payload", message)


class RouterNotFoundError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.not_found", message)


class RouterPermissionError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.permission", message)


class RouterStateError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.state", message)


class RouterConfig(BaseModel):
    """operator 写定的单仓库配置；actions 只来自这里，不来自外部文本（L4）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str
    mode: AutonomyMode = AutonomyMode.OBSERVE
    operators: frozenset[str] = frozenset()
    actions: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    allowed_mcp: tuple[str, ...] = ()
    network_profile: str | None = None
    base_ref: str | None = None
    requires_approval: bool = False


class RequestSignal(BaseModel):
    """Source 标准化后的显式请求；text 只进 digest 与 Input，不参与判定（L4）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    mentioned: bool
    stable_id: str
    url: str | None = None

    @classmethod
    def from_trigger(cls, trigger: Trigger) -> RequestSignal | None:
        block = trigger.payload.get("request")
        if block is None:
            return None
        if not isinstance(block, dict):
            raise RouterPayloadError("request 块不是对象")
        text = block.get("text")
        mentioned = block.get("mentioned")
        if not isinstance(text, str) or not isinstance(mentioned, bool):
            raise RouterPayloadError("request 块缺少 text/mentioned")
        url = block.get("url")
        if url is not None and not isinstance(url, str):
            raise RouterPayloadError("request.url 不是字符串")
        return cls(text=text, mentioned=mentioned, stable_id=trigger.external_id, url=url)


class Reception(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_id: str
    duplicate: bool = False
    decision_ids: list[str] = Field(default_factory=list)
    ticket_id: str | None = None
    input_ids: list[str] = Field(default_factory=list)
    fanned_out: int = 0


def record_decision(
    store: SupervisorStore,
    *,
    ids: IdFactory,
    created_at: int,
    trigger_ids: list[str],
    outcome: DecisionOutcome,
    strength: DecisionStrength,
    reason_codes: list[str],
    policy_version: str,
    request_digest: str,
    mandate_id: str | None = None,
) -> Decision:
    decision = Decision(
        id=ids.new_id("dc"),
        trigger_ids=list(trigger_ids),
        outcome=outcome,
        strength=strength,
        reason_codes=list(reason_codes),
        policy_version=policy_version,
        mandate_id=mandate_id,
        request_digest=request_digest,
        attempt=store.next_decision_attempt(request_digest),
        created_at=created_at,
    )
    store.insert_decision(decision)
    return decision


def ensure_subscription(
    store: SupervisorStore,
    *,
    ids: IdFactory,
    ticket_id: str,
    thread_key: str,
    mode: SubscriptionMode,
    created_at: int,
) -> Subscription:
    for subscription in store.list_subscriptions(
        ticket_id=ticket_id, thread_key=thread_key, live_only=True
    ):
        if subscription.mode is mode:
            return subscription
    subscription = Subscription(
        id=ids.new_id("sb"),
        ticket_id=ticket_id,
        thread_key=thread_key,
        mode=mode,
        created_at=created_at,
    )
    store.insert_subscription(subscription)
    return subscription


def latest_trigger(store: SupervisorStore, thread_key: str) -> Trigger | None:
    triggers = store.list_triggers(thread_key=thread_key, limit=1_000)
    return triggers[-1] if triggers else None


def _input_text(trigger: Trigger, signal: RequestSignal | None) -> str:
    if signal is not None:
        return signal.text
    return canonical_json(trigger.payload)


class RouterService:
    def __init__(
        self,
        store: SupervisorStore,
        policy: StaticPolicy,
        assessor: CandidateAssessor,
        config: RouterConfig,
        *,
        clock: Clock,
        ids: IdFactory,
        audit: AuditWriter | None = None,
    ) -> None:
        if config.mode not in _ALLOWED_MODES:
            raise RouterModeError(f"{config.mode.value} 未启用：P6 Beta 锁定 observe")
        self._store = store
        self._policy = policy
        self._assessor = assessor
        self._config = config
        self._clock = clock
        self._ids = ids
        self._audit = audit

    @property
    def config(self) -> RouterConfig:
        return self._config

    def receive(self, trigger: Trigger) -> Reception:
        signal, signal_error = self._read_signal(trigger)
        admission = self._admit(trigger, signal)
        with self._store.transaction():
            return self._route(
                trigger, signal=signal, signal_error=signal_error, admission=admission
            )

    # ------------------------------------------------------------------ 路由
    def _route(
        self,
        trigger: Trigger,
        *,
        signal: RequestSignal | None,
        signal_error: str | None,
        admission: Admission | None,
    ) -> Reception:
        if self._already_decided(trigger.id):
            return Reception(trigger_id=trigger.id, duplicate=True)
        self._store.insert_trigger(trigger)
        now = self._clock.now_ms()
        input_ids = self._fanout(trigger, now, signal)
        threads = [
            thread
            for key in trigger.thread_keys
            if (thread := self._store.get_thread(key)) is not None
        ]
        primary = threads[0] if threads else None
        if signal_error is not None:
            decision = self._ignore(
                trigger,
                now,
                signal_error,
                DecisionStrength.NOT_ADMITTED,
                digest=trigger.digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        if admission is not None and signal is not None:
            return self._route_explicit(
                trigger, signal, admission, primary, now, input_ids
            )
        return self._route_inferred(trigger, threads, now, input_ids)

    def _route_explicit(
        self,
        trigger: Trigger,
        signal: RequestSignal,
        admission: Admission,
        primary: Thread | None,
        now: int,
        input_ids: list[str],
    ) -> Reception:
        if not admission.admitted:
            decision = self._ignore(
                trigger,
                now,
                admission.reason_code,
                DecisionStrength.NOT_ADMITTED,
                digest=admission.request_digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        owners = self._live_own_tickets(trigger)
        if len(owners) == 1:
            owner = owners[0]
            if owner.status is TicketStatus.CANCELLED:
                decision = self._stop(
                    trigger, now, "stopped_ignored", admission.request_digest
                )
                return self._reception(trigger, decision, ticket_id=owner.id)
            if owner.status in (TicketStatus.COMPLETED, TicketStatus.FAILED):
                stored = self._store_input(owner, trigger, signal, now)
                decision = self._observe(
                    trigger,
                    now,
                    ["resume_requires_operator"],
                    digest=admission.request_digest,
                )
                return self._reception(
                    trigger, decision, ticket_id=owner.id, input_ids=[stored]
                )
            decision = self._attach(
                trigger, now, "owned_thread_followup", admission.request_digest
            )
            return self._reception(
                trigger, decision, ticket_id=owner.id, input_ids=input_ids
            )
        if len(owners) > 1:
            decision = self._observe(
                trigger, now, ["ambiguous_owner"], digest=admission.request_digest
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        existing = self._existing_ticket(admission)
        if existing is not None:
            if primary is not None:
                ensure_subscription(
                    self._store,
                    ids=self._ids,
                    ticket_id=existing.id,
                    thread_key=primary.key,
                    mode=SubscriptionMode.CONTEXT,
                    created_at=now,
                )
            decision = self._attach(
                trigger, now, "duplicate_request", admission.request_digest
            )
            return self._reception(
                trigger, decision, ticket_id=existing.id, input_ids=input_ids
            )
        if primary is None:
            decision = self._ignore(
                trigger, now, "thread_unknown", DecisionStrength.NOT_ADMITTED
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        ticket = self._accept(signal, admission, primary, now)
        decision = record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.ACCEPT,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=["admitted"],
            policy_version=self._policy.policy_version,
            request_digest=admission.request_digest,
            mandate_id=ticket.mandate_id,
        )
        return self._reception(trigger, decision, ticket_id=ticket.id, input_ids=input_ids)

    def _route_inferred(
        self, trigger: Trigger, threads: list[Thread], now: int, input_ids: list[str]
    ) -> Reception:
        if self._config.mode is AutonomyMode.OFF:
            decision = self._ignore(
                trigger, now, "autonomy_off", DecisionStrength.INFERRED
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        context = self._assessment_context(trigger, threads)
        assessment = self._assessor.assess(context)
        decision = self._observe(
            trigger, now, assessment.reason_codes, digest=trigger.digest
        )
        return self._reception(trigger, decision, input_ids=input_ids)

    def _accept(
        self,
        signal: RequestSignal,
        admission: Admission,
        primary: Thread,
        now: int,
    ) -> Ticket:
        mandate = self._policy.create_mandate(
            admission,
            actions=self._config.actions,
            allowed_paths=self._config.allowed_paths,
            allowed_commands=self._config.allowed_commands,
            allowed_mcp=self._config.allowed_mcp,
            network_profile=self._config.network_profile,
            base_ref=self._config.base_ref,
            requires_approval=self._config.requires_approval,
        )
        ticket = Ticket(
            id=self._ids.new_id("tk"),
            title=signal.text[:40],
            task=signal.text,
            created_at=now,
            status=TicketStatus.QUEUED,
            queued_at=now,
            mandate_id=mandate.id,
        )
        assignment = Assignment(id=self._ids.new_id("as"), ticket_id=ticket.id, attempt=1)
        self._store.insert_mandate(mandate)
        self._store.insert_ticket(ticket)
        self._store.insert_input(
            TicketInput(
                id=self._ids.new_id("in"),
                ticket_id=ticket.id,
                sequence=1,
                kind="task",
                payload=signal.text,
                created_at=now,
            )
        )
        self._store.insert_assignment(assignment)
        self._store.enqueue_effect(
            Effect(
                id=self._ids.new_id("ef"),
                kind=EffectKind.LAUNCH_ASSIGNMENT,
                idempotency_key=f"launch:{assignment.id}:1",
                payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
                due_at=now,
            )
        )
        ensure_subscription(
            self._store,
            ids=self._ids,
            ticket_id=ticket.id,
            thread_key=primary.key,
            mode=SubscriptionMode.OWN,
            created_at=now,
        )
        return ticket

    # ------------------------------------------------------------------ 判定辅助
    def _read_signal(self, trigger: Trigger) -> tuple[RequestSignal | None, str | None]:
        if trigger.kind is TriggerKind.CI_STATUS:
            return None, None
        try:
            return RequestSignal.from_trigger(trigger), None
        except RouterPayloadError:
            return None, "malformed_request"

    def _admit(self, trigger: Trigger, signal: RequestSignal | None) -> Admission | None:
        if signal is None:
            return None
        admission = self._policy.admit_explicit(
            ExplicitRequest(
                repository=self._config.repository,
                requester=trigger.actor,
                mentioned=signal.mentioned,
                stable_id=signal.stable_id,
                text=signal.text,
            )
        )
        if self._audit is not None:
            self._audit.admitted(admission)
        return admission

    def _already_decided(self, trigger_id: str) -> bool:
        return bool(self._store.list_decisions(trigger_id=trigger_id, limit=1))

    def _live_subscriptions(
        self, trigger: Trigger, mode: SubscriptionMode
    ) -> list[Subscription]:
        seen: set[str] = set()
        result: list[Subscription] = []
        for thread_key in trigger.thread_keys:
            for subscription in self._store.list_subscriptions(
                thread_key=thread_key, live_only=True
            ):
                if subscription.mode is not mode or subscription.id in seen:
                    continue
                seen.add(subscription.id)
                result.append(subscription)
        return result

    def _live_own_tickets(self, trigger: Trigger) -> list[Ticket]:
        tickets: dict[str, Ticket] = {}
        for subscription in self._live_subscriptions(trigger, SubscriptionMode.OWN):
            ticket = self._store.get_ticket(subscription.ticket_id)
            if ticket is not None:
                tickets[ticket.id] = ticket
        return list(tickets.values())

    def _assessment_context(
        self, trigger: Trigger, threads: list[Thread]
    ) -> AssessmentContext:
        owners = self._live_own_tickets(trigger)
        return AssessmentContext(
            trigger=trigger,
            thread=threads[0] if threads else None,
            has_live_own_subscription=bool(owners),
            has_live_context_subscription=bool(
                self._live_subscriptions(trigger, SubscriptionMode.CONTEXT)
            ),
            existing_ticket_id=owners[0].id if len(owners) == 1 else None,
            policy_version=self._policy.policy_version,
        )

    def _existing_ticket(self, admission: Admission) -> Ticket | None:
        decision = self._store.find_accepted_decision(admission.request_digest)
        if decision is None or decision.mandate_id is None:
            return None
        mandate = self._store.get_mandate(decision.mandate_id)
        if mandate is None:
            return None
        return self._store.get_ticket_by_mandate_scope(
            mandate.scope, origin_kind="github"
        )

    def _fanout(
        self, trigger: Trigger, now: int, signal: RequestSignal | None
    ) -> list[str]:
        seen: set[str] = set()
        input_ids: list[str] = []
        for thread_key in trigger.thread_keys:
            for subscription in self._store.list_subscriptions(
                thread_key=thread_key, live_only=True
            ):
                ticket = self._store.get_ticket(subscription.ticket_id)
                if ticket is None or ticket.status in TERMINAL_TICKET_STATUSES:
                    continue
                if ticket.id in seen:
                    continue
                seen.add(ticket.id)
                input_ids.append(self._append_input(ticket, trigger, signal, now))
        return input_ids

    def _append_input(
        self,
        ticket: Ticket,
        trigger: Trigger,
        signal: RequestSignal | None,
        now: int,
    ) -> str:
        sequence = len(self._store.list_inputs(ticket.id)) + 1
        item = TicketInput(
            id=self._ids.new_id("in"),
            ticket_id=ticket.id,
            sequence=sequence,
            kind="text",
            payload=_input_text(trigger, signal),
            created_at=now,
        )
        self._store.insert_input(item)
        if ticket.status is not TicketStatus.RUNNING:
            self._store.update_ticket(
                ticket.model_copy(update={"woken_at": ticket.woken_at or now})
            )
        self._store.enqueue_effect(
            Effect(
                id=self._ids.new_id("ef"),
                kind=EffectKind.DELIVER_INPUT,
                idempotency_key=f"deliver:{ticket.id}:{sequence}",
                payload={
                    "ticket_id": ticket.id,
                    "input_id": item.id,
                    "sequence": sequence,
                },
                due_at=now,
            )
        )
        return item.id

    def _store_input(
        self, ticket: Ticket, trigger: Trigger, signal: RequestSignal, now: int
    ) -> str:
        text = _input_text(trigger, signal)
        for item in self._store.list_inputs(ticket.id):
            if item.state == "pending" and item.payload == text:
                return item.id
        sequence = len(self._store.list_inputs(ticket.id)) + 1
        item = TicketInput(
            id=self._ids.new_id("in"),
            ticket_id=ticket.id,
            sequence=sequence,
            kind="text",
            payload=text,
            created_at=now,
        )
        self._store.insert_input(item)
        return item.id

    def _reception(
        self,
        trigger: Trigger,
        decision: Decision,
        *,
        ticket_id: str | None = None,
        input_ids: list[str] | None = None,
    ) -> Reception:
        ids = list(input_ids or [])
        return Reception(
            trigger_id=trigger.id,
            decision_ids=[decision.id],
            ticket_id=ticket_id,
            input_ids=ids,
            fanned_out=len(ids),
        )

    def _ignore(
        self,
        trigger: Trigger,
        now: int,
        reason: str,
        strength: DecisionStrength,
        *,
        digest: str | None = None,
    ) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.IGNORE,
            strength=strength,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest or trigger.digest,
        )

    def _observe(
        self,
        trigger: Trigger,
        now: int,
        reasons: list[str],
        *,
        digest: str | None = None,
    ) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=list(reasons),
            policy_version=self._policy.policy_version,
            request_digest=digest or trigger.digest,
        )

    def _attach(
        self, trigger: Trigger, now: int, reason: str, digest: str
    ) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.ATTACH,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest,
        )

    def _stop(self, trigger: Trigger, now: int, reason: str, digest: str) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.STOP,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest,
        )
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py
.venv/bin/ruff check stableagent/supervisor/router.py
.venv/bin/ruff format --check stableagent/supervisor/router.py
.venv/bin/mypy
```

Expected: `20 passed`；ruff / mypy 全绿。若 `test_receive_accepts_a_trigger_already_persisted_by_the_source`
因 `insert_trigger` 返回 False 但决策仍写入而通过，保留该用例作为 M2/M3 接缝契约。

- [ ] **Step 5: 回归 M2 与边界测试**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_github_source.py \
  tests/unit/test_supervisor_model.py tests/unit/test_supervisor_store.py \
  tests/unit/test_control_boundaries.py tests/unit/test_execution_boundaries.py
```

Expected: 全绿；`test_execution_boundaries` 不应报 `router.py`（无 `await`）。

- [ ] **Step 6: 提交**

```bash
git add stableagent/supervisor/router.py tests/unit/test_supervisor_router.py
git commit -m "feat: route supervisor triggers through one atomic receive"
```

---

### Task 4：unavailable 与 reevaluate（M3-6）

**Files:**
- Modify: `stableagent/supervisor/router.py`
- Test: `tests/unit/test_supervisor_router.py`

**Interfaces:**
- Consumes: Task 3 的 `_route` / `_read_signal` / `_admit` / `_route_inferred`。
- Produces: `RouterService.reevaluate(trigger_id) -> Reception`；评估器异常只落
  `UNAVAILABLE`，重评估不重复扇出、不新增 Trigger。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_supervisor_router.py` 追加：

```python
class FlakyAssessor:
    """第一次失败、之后委托给 ObserveOnlyAssessor；用于 unavailable → reevaluate。"""

    def __init__(self) -> None:
        self.calls = 0

    def assess(self, context):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("assessor down")
        return ObserveOnlyAssessor().assess(context)


def _flaky_router(store, policy, clock, ids) -> RouterService:
    return RouterService(
        store,
        policy,
        FlakyAssessor(),
        RouterConfig(
            repository="o/r",
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
    )


def test_assessor_failure_records_unavailable(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _flaky_router(store, policy, clock, ids).receive(_trigger())
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.UNAVAILABLE
    assert decision.strength is DecisionStrength.INFERRED
    assert decision.reason_codes == ["assessor_error"]


def test_reevaluate_retries_unavailable_without_refanout(store, policy, clock, ids) -> None:
    router = _flaky_router(store, policy, clock, ids)
    ticket = _accept(router, store)
    trigger = _trigger(
        external_id="github:o/r:issue_comment:9",
        kind=TriggerKind.ISSUE_COMMENT,
    )
    first = router.receive(trigger)
    assert store.get_decision(first.decision_ids[0]).outcome is DecisionOutcome.UNAVAILABLE
    inputs_before = len(store.list_inputs(ticket.id))
    retried = router.reevaluate(trigger.id)
    assert retried.duplicate is False
    assert retried.input_ids == []
    decision = store.get_decision(retried.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.attempt == 2
    assert "reevaluated" in decision.reason_codes
    assert len(store.list_inputs(ticket.id)) == inputs_before
    assert len(store.list_decisions(trigger_id=trigger.id)) == 2


def test_reevaluate_of_a_missing_trigger_raises(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    with pytest.raises(RouterNotFoundError):
        router.reevaluate("tg-missing")
```

需要把 `RouterNotFoundError` 加入 import 列表。

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py -k "unavailable or reevaluate"
```

Expected: `RouterService` 无 `reevaluate`；评估器异常直接冒泡导致前两条失败。

- [ ] **Step 3: 实现**

`receive` 与 `_route` 改为带 `reevaluate` 参数；新增方法：

```python
    def reevaluate(self, trigger_id: str) -> Reception:
        trigger = self._store.get_trigger(trigger_id)
        if trigger is None:
            raise RouterNotFoundError(f"trigger 不存在：{trigger_id}")
        signal, signal_error = self._read_signal(trigger)
        admission = self._admit(trigger, signal)
        with self._store.transaction():
            return self._route(
                trigger,
                signal=signal,
                signal_error=signal_error,
                admission=admission,
                reevaluate=True,
            )
```

`_route` 的签名与开头改为：

```python
    def _route(
        self,
        trigger: Trigger,
        *,
        signal: RequestSignal | None,
        signal_error: str | None,
        admission: Admission | None,
        reevaluate: bool = False,
    ) -> Reception:
        if not reevaluate:
            if self._already_decided(trigger.id):
                return Reception(trigger_id=trigger.id, duplicate=True)
            self._store.insert_trigger(trigger)
        now = self._clock.now_ms()
        input_ids = [] if reevaluate else self._fanout(trigger, now, signal)
        ...
        return self._route_inferred(trigger, threads, now, input_ids, reevaluate=reevaluate)
```

`receive` 的调用补 `reevaluate=False`。`_route_inferred` 改为：

```python
    def _route_inferred(
        self,
        trigger: Trigger,
        threads: list[Thread],
        now: int,
        input_ids: list[str],
        *,
        reevaluate: bool = False,
    ) -> Reception:
        if self._config.mode is AutonomyMode.OFF:
            decision = self._ignore(
                trigger, now, "autonomy_off", DecisionStrength.INFERRED
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        context = self._assessment_context(trigger, threads)
        try:
            assessment = self._assessor.assess(context)
        except Exception:
            decision = record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=[trigger.id],
                outcome=DecisionOutcome.UNAVAILABLE,
                strength=DecisionStrength.INFERRED,
                reason_codes=["assessor_error"],
                policy_version=self._policy.policy_version,
                request_digest=trigger.digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        reasons = list(assessment.reason_codes)
        if reevaluate:
            reasons.append("reevaluated")
        decision = self._observe(trigger, now, reasons, digest=trigger.digest)
        return self._reception(trigger, decision, input_ids=input_ids)
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py
.venv/bin/ruff check stableagent/supervisor/router.py
.venv/bin/ruff format --check stableagent/supervisor/router.py
.venv/bin/mypy
```

Expected: `23 passed`；ruff / mypy 全绿。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/router.py tests/unit/test_supervisor_router.py
git commit -m "feat: record assessor failures as unavailable and allow reevaluation"
```

---

### Task 5：命令层 attach / subscribe / unsubscribe / stop（M3-7 前半）

**Files:**
- Create: `stableagent/supervisor/service.py`
- Test: `tests/unit/test_supervisor_service.py`

**Interfaces:**
- Consumes: Task 3 的 `record_decision` / `ensure_subscription` / `latest_trigger`；
  `RouterService.config.operators`。
- Produces: `SupervisorService(store, policy, router, *, clock, ids, audit=None)`；`receive`
  透传；`reevaluate`、`attach`、`subscribe`、`unsubscribe`、`stop`。Task 6 只追加
  `handback` / `resume` / `record_pull_request` 与私有 helper。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_supervisor_service.py` 全文：

```python
"""WP-6.3：命令层权限、context 订阅与人工 stop。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind, TicketStatus
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    DecisionOutcome,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import (
    RouterConfig,
    RouterNotFoundError,
    RouterPermissionError,
    RouterService,
    RouterStateError,
)
from stableagent.supervisor.service import SupervisorService
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _thread(key: str = "issue:o/r#1") -> Thread:
    kind = ThreadKind.PULL_REQUEST if key.startswith("pr:") else ThreadKind.ISSUE
    ref = key.rsplit("#", 1)[-1]
    return Thread(
        key=key,
        repository="o/r",
        kind=kind,
        external_ref=f"#{ref}",
        last_trigger_at=1,
        status=ThreadStatus.OPEN,
    )


def _trigger(
    *,
    external_id: str = "github:o/r:issue:1",
    thread_keys: list[str] | None = None,
    text: str | None = None,
) -> Trigger:
    payload: dict[str, Any] = {"resource": {"number": 1}, "deleted": False}
    if text is not None:
        payload["request"] = {"text": text, "mentioned": True, "url": None}
    return Trigger(
        id=f"tg-{external_id}",
        external_id=external_id,
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=thread_keys or ["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload=payload,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> SequenceIds:
    return SequenceIds()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=clock)


@pytest.fixture
def service(store: SupervisorStore, clock: FixedClock, ids: SequenceIds) -> SupervisorService:
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.RUN_COMMAND}),
            allowed_commands=["pytest"],
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    router = RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        RouterConfig(
            repository="o/r",
            mode=AutonomyMode.OBSERVE,
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
    )
    return SupervisorService(store, policy, router, clock=clock, ids=ids)


def _accept(service: SupervisorService, store: SupervisorStore):
    store.upsert_thread(_thread())
    reception = service.receive(_trigger(text="fix it"))
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None
    return ticket


def test_non_operators_cannot_attach_or_stop(service, store) -> None:
    ticket = _accept(service, store)
    with pytest.raises(RouterPermissionError):
        service.attach(ticket.id, thread_key="issue:o/r#2", actor="mallory")
    with pytest.raises(RouterPermissionError):
        service.stop(ticket.id, actor="mallory")
    with pytest.raises(RouterPermissionError):
        service.unsubscribe(ticket.id, thread_key="issue:o/r#1", actor="mallory")


def test_attach_is_an_idempotent_context_subscription(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    first = service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    second = service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    assert first.id == second.id
    assert first.mode is SubscriptionMode.CONTEXT
    assert len(store.list_subscriptions(ticket_id=ticket.id, thread_key="issue:o/r#2")) == 1


def test_subscribe_uses_the_trigger_primary_thread(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    trigger = _trigger(external_id="github:o/r:issue:2", thread_keys=["issue:o/r#2"])
    store.insert_trigger(trigger)
    subscription = service.subscribe(ticket.id, trigger_id=trigger.id, actor="alice")
    assert subscription.thread_key == "issue:o/r#2"
    assert subscription.mode is SubscriptionMode.CONTEXT


def test_unsubscribe_ends_live_subscriptions_and_stops_fanout(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    assert service.unsubscribe(ticket.id, thread_key="issue:o/r#2", actor="alice") == 1
    before = len(store.list_inputs(ticket.id))
    reception = service.receive(
        _trigger(external_id="github:o/r:issue:3", thread_keys=["issue:o/r#2"])
    )
    assert reception.fanned_out == 0
    assert len(store.list_inputs(ticket.id)) == before


def test_stop_cancels_ticket_effects_and_context_but_keeps_own(
    service, store, clock
) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    stopped = service.stop(ticket.id, actor="alice", reason="operator stop")
    assert stopped.status is TicketStatus.CANCELLED
    kinds = {effect.kind for effect in store.due_effects(clock.now_ms())}
    assert EffectKind.CANCEL_ASSIGNMENT in kinds
    live = store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]
    decisions = store.list_decisions(trigger_id=_trigger().id)
    assert decisions[-1].outcome is DecisionOutcome.STOP
    assert decisions[-1].reason_codes == ["manual_stop"]


def test_stop_is_idempotent_for_a_cancelled_ticket(service, store) -> None:
    ticket = _accept(service, store)
    once = service.stop(ticket.id, actor="alice")
    twice = service.stop(ticket.id, actor="alice")
    assert once.status is TicketStatus.CANCELLED
    assert twice.status is TicketStatus.CANCELLED
    assert twice.ended_at == once.ended_at


def test_stop_on_completed_ticket_only_ends_context_subscriptions(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    stopped = service.stop(ticket.id, actor="alice")
    assert stopped.status is TicketStatus.COMPLETED
    live = store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py
```

Expected: `ModuleNotFoundError: stableagent.supervisor.service`。

- [ ] **Step 3: 实现 service.py（完整文件）**

```python
"""Router 的运维命令层：ticket 域命令，全部检查 operator 与状态（规格 WP-6.3 第 4~6 条）。

方向锁：service → router → supervisor.store → orchestration；不 import integrations。
receive / reevaluate 留在 RouterService；命令层只做 Ticket / Subscription 的受控变更。
"""

from __future__ import annotations

from stableagent.control.audit import AuditWriter
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    AssignmentStatus,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Ticket,
    TicketAction,
    TicketStatus,
    next_ticket_status,
)
from stableagent.supervisor.model import (
    Decision,
    DecisionOutcome,
    DecisionStrength,
    Subscription,
    SubscriptionMode,
    Trigger,
    payload_digest,
)
from stableagent.supervisor.router import (
    Reception,
    RouterNotFoundError,
    RouterPermissionError,
    RouterService,
    RouterStateError,
    ensure_subscription,
    latest_trigger,
    record_decision,
)
from stableagent.supervisor.store import SupervisorStore

_LIVE_ASSIGNMENT_STATUSES = (
    AssignmentStatus.PENDING,
    AssignmentStatus.RUNNING,
    AssignmentStatus.INTERRUPTED,
)


class SupervisorService:
    def __init__(
        self,
        store: SupervisorStore,
        policy: StaticPolicy,
        router: RouterService,
        *,
        clock: Clock,
        ids: IdFactory,
        audit: AuditWriter | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._router = router
        self._clock = clock
        self._ids = ids
        self._audit = audit

    @property
    def router(self) -> RouterService:
        return self._router

    def receive(self, trigger: Trigger) -> Reception:
        return self._router.receive(trigger)

    def reevaluate(self, trigger_id: str, *, actor: str) -> Decision:
        self._require_operator(actor)
        reception = self._router.reevaluate(trigger_id)
        if not reception.decision_ids:
            raise RouterStateError("reevaluate 未产生 Decision")
        decision = self._store.get_decision(reception.decision_ids[0])
        if decision is None:  # pragma: no cover - 刚写入的行必可读
            raise RouterStateError("Decision 缺失")
        return decision

    def attach(self, ticket_id: str, *, thread_key: str, actor: str) -> Subscription:
        """把一个相关 Thread 作为 context 订阅接上；已有 live 订阅则原样返回。"""
        self._require_operator(actor)
        with self._store.transaction():
            ticket = self._require_live_ticket(ticket_id)
            self._require_thread(thread_key)
            live = self._store.list_subscriptions(
                ticket_id=ticket.id, thread_key=thread_key, live_only=True
            )
            if live:
                return live[0]
            return ensure_subscription(
                self._store,
                ids=self._ids,
                ticket_id=ticket.id,
                thread_key=thread_key,
                mode=SubscriptionMode.CONTEXT,
                created_at=self._clock.now_ms(),
            )

    def subscribe(self, ticket_id: str, *, trigger_id: str, actor: str) -> Subscription:
        trigger = self._require_trigger(trigger_id)
        if not trigger.thread_keys:
            raise RouterStateError("trigger 没有 thread key")
        return self.attach(ticket_id, thread_key=trigger.thread_keys[0], actor=actor)

    def unsubscribe(self, ticket_id: str, *, thread_key: str, actor: str) -> int:
        self._require_operator(actor)
        with self._store.transaction():
            self._require_ticket(ticket_id)
            ended = 0
            for subscription in self._store.list_subscriptions(
                ticket_id=ticket_id, thread_key=thread_key, live_only=True
            ):
                ended += self._store.end_subscription(
                    subscription.id, ended_at=self._clock.now_ms()
                )
            return ended

    def stop(self, ticket_id: str, *, actor: str, reason: str | None = None) -> Ticket:
        """人工停：取消未结束 Ticket、入队 cancel effect、只结束 context 订阅。

        own 订阅故意保留：被 stop 的 Ticket 之后收到的明确后续消息由 Router 记 STOP 并
        丢弃，不会因为订阅消失而被当成新任务；人工 resume 也拒绝 CANCELLED。
        """
        self._require_operator(actor)
        with self._store.transaction():
            ticket = self._require_ticket(ticket_id)
            now = self._clock.now_ms()
            if ticket.status is TicketStatus.CANCELLED:
                return ticket
            updated = ticket
            if ticket.status not in TERMINAL_TICKET_STATUSES:
                updated = ticket.model_copy(
                    update={
                        "status": next_ticket_status(ticket.status, TicketAction.CANCEL),
                        "ended_at": now,
                    }
                )
                self._store.update_ticket(updated)
                for assignment in self._store.list_assignments(ticket_id):
                    if assignment.status not in _LIVE_ASSIGNMENT_STATUSES:
                        continue
                    self._store.enqueue_effect(
                        Effect(
                            id=self._ids.new_id("ef"),
                            kind=EffectKind.CANCEL_ASSIGNMENT,
                            idempotency_key=f"cancel:{assignment.id}",
                            payload={
                                "assignment_id": assignment.id,
                                "ticket_id": ticket_id,
                                "reason": reason,
                            },
                            due_at=now,
                        )
                    )
            for subscription in self._store.list_subscriptions(
                ticket_id=ticket_id, live_only=True
            ):
                if subscription.mode is SubscriptionMode.CONTEXT:
                    self._store.end_subscription(subscription.id, ended_at=now)
            record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=self._decision_triggers(ticket_id),
                outcome=DecisionOutcome.STOP,
                strength=DecisionStrength.EXPLICIT,
                reason_codes=["manual_stop"],
                policy_version=self._policy.policy_version,
                request_digest=payload_digest({"command": "stop", "ticket": ticket_id}),
            )
            return updated

    # ------------------------------------------------------------- 内部
    def _require_operator(self, actor: str) -> None:
        allowed = {name.casefold() for name in self._router.config.operators}
        if actor.casefold() not in allowed:
            raise RouterPermissionError(f"actor 不是 operator：{actor}")

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise RouterNotFoundError(f"ticket 不存在：{ticket_id}")
        return ticket

    def _require_live_ticket(self, ticket_id: str) -> Ticket:
        ticket = self._require_ticket(ticket_id)
        if ticket.status in TERMINAL_TICKET_STATUSES:
            raise RouterStateError(f"ticket 已结束：{ticket.status.value}")
        return ticket

    def _require_thread(self, thread_key: str) -> None:
        if self._store.get_thread(thread_key) is None:
            raise RouterNotFoundError(f"thread 不存在：{thread_key}")

    def _require_trigger(self, trigger_id: str) -> Trigger:
        trigger = self._store.get_trigger(trigger_id)
        if trigger is None:
            raise RouterNotFoundError(f"trigger 不存在：{trigger_id}")
        return trigger

    def _decision_triggers(self, ticket_id: str) -> list[str]:
        own = [
            subscription.thread_key
            for subscription in self._store.list_subscriptions(ticket_id=ticket_id)
            if subscription.mode is SubscriptionMode.OWN
        ]
        if not own:
            return []
        trigger = latest_trigger(self._store, own[0])
        return [trigger.id] if trigger is not None else []
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py
.venv/bin/ruff check stableagent/supervisor/service.py
.venv/bin/ruff format --check stableagent/supervisor/service.py
.venv/bin/mypy
```

Expected: `8 passed`；ruff / mypy 全绿。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/service.py tests/unit/test_supervisor_service.py
git commit -m "feat: add supervisor command service for subscriptions and stop"
```

---

### Task 6：handback / resume / record_pull_request（M3-7 后半）

**Files:**
- Modify: `stableagent/supervisor/service.py`
- Test: `tests/unit/test_supervisor_service.py`

**Interfaces:**
- Consumes: `RequestSignal` / `ensure_subscription` / `latest_trigger`；`StaticPolicy.revalidate`；
  `pull_request_thread_key`（`supervisor.source`）。
- Produces: `handback(ticket_id, *, thread_key, trigger_id, actor) -> Decision`；
  `resume(ticket_id, *, trigger_id, actor) -> Ticket`；
  `record_pull_request(ticket_id, *, report_id, actor) -> Subscription`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_supervisor_service.py` 追加（并把 `Assignment`、`Report`、`ReportKind`、
`DecisionOutcome` 加入 import）：

```python
def test_handback_turns_own_into_context_and_allows_a_new_ticket(service, store) -> None:
    ticket = _accept(service, store)
    follow = _trigger(
        external_id="github:o/r:issue_comment:7", text="follow up"
    )
    store.insert_trigger(follow)
    decision = service.handback(
        ticket.id, thread_key="issue:o/r#1", trigger_id=follow.id, actor="alice"
    )
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["handback"]
    live = {
        item.mode
        for item in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    }
    assert live == {SubscriptionMode.CONTEXT}
    reception = service.receive(
        _trigger(
            external_id="github:o/r:issue_comment:8",
            text="new task",
        )
    )
    assert reception.ticket_id is not None
    assert reception.ticket_id != ticket.id
    assert len(store.list_tickets()) == 2


def test_resume_refuses_cancelled_and_live_tickets(service, store) -> None:
    ticket = _accept(service, store)
    trigger = _trigger(external_id="github:o/r:issue_comment:9", text="resume please")
    store.insert_trigger(trigger)
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.CANCELLED}))
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")


def test_resume_requires_an_admitted_explicit_request(service, store) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    plain = _trigger(external_id="github:o/r:issue_comment:10")
    store.insert_trigger(plain)
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=plain.id, actor="alice")


def test_resume_rejects_an_expired_mandate(service, store, clock) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    trigger = _trigger(external_id="github:o/r:issue_comment:11", text="resume please")
    store.insert_trigger(trigger)
    clock.advance(60_001)
    with pytest.raises(RouterStateError, match="revalidation_failed"):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")


def test_resume_requeues_with_a_new_assignment_and_reuses_the_pending_input(
    service, store
) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    follow = _trigger(
        external_id="github:o/r:issue_comment:12",
        text="continue the work",
    )
    service.receive(follow)
    inputs_before = len(store.list_inputs(ticket.id))
    resumed = service.resume(ticket.id, trigger_id=follow.id, actor="alice")
    assert resumed.status is TicketStatus.QUEUED
    assert len(store.list_assignments(ticket.id)) == 2
    assignments = store.list_assignments(ticket.id)
    assert [item.attempt for item in assignments] == [1, 2]
    assert len(store.list_inputs(ticket.id)) == inputs_before
    decisions = store.list_decisions(trigger_id=follow.id)
    assert decisions[-1].outcome is DecisionOutcome.ACCEPT
    assert decisions[-1].reason_codes == ["resumed"]


def test_record_pull_request_adds_own_subscription_from_a_report(service, store, clock) -> None:
    ticket = _accept(service, store)
    assignment = store.list_assignments(ticket.id)[0]
    store.upsert_thread(_thread("pr:o/r#9"))
    service.attach(ticket.id, thread_key="pr:o/r#9", actor="alice")
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=ReportKind.READY,
            summary="opened a PR",
            created_at=clock.now_ms(),
            details={"pull_number": 9},
        )
    )
    subscription = service.record_pull_request(
        ticket.id, report_id="rp-1", actor="alice"
    )
    assert subscription.thread_key == "pr:o/r#9"
    assert subscription.mode is SubscriptionMode.OWN
    live = store.list_subscriptions(ticket_id=ticket.id, thread_key="pr:o/r#9", live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]
    assert store.get_thread("pr:o/r#9") is not None


def test_record_pull_request_rejects_a_report_without_a_number(service, store, clock) -> None:
    ticket = _accept(service, store)
    assignment = store.list_assignments(ticket.id)[0]
    store.insert_report(
        Report(
            id="rp-2",
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=ReportKind.READY,
            summary="no number",
            created_at=clock.now_ms(),
            details={},
        )
    )
    with pytest.raises(RouterStateError, match="pull_number"):
        service.record_pull_request(ticket.id, report_id="rp-2", actor="alice")
```

- [ ] **Step 2: 跑红**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py -k "handback or resume or pull_request"
```

Expected: `SupervisorService` 无对应方法。

- [ ] **Step 3: 实现**

`stableagent/supervisor/service.py` 的 import 追加：

```python
from stableagent.control.model import ExplicitRequest
from stableagent.orchestration.model import Assignment, TicketInput
from stableagent.supervisor.model import Thread, ThreadKind, ThreadStatus
from stableagent.supervisor.router import RequestSignal
from stableagent.supervisor.source import pull_request_thread_key
```

在 `stop` 之后、`# ------------------------------------------------------------- 内部`
之前追加：

```python
    def handback(
        self,
        ticket_id: str,
        *,
        thread_key: str,
        trigger_id: str,
        actor: str,
    ) -> Decision:
        """把 own 降为 context：之后的明确消息重新走 Router，可创建新 Ticket。"""
        self._require_operator(actor)
        trigger = self._require_trigger(trigger_id)
        with self._store.transaction():
            ticket = self._require_live_ticket(ticket_id)
            self._require_thread(thread_key)
            now = self._clock.now_ms()
            own = [
                subscription
                for subscription in self._store.list_subscriptions(
                    ticket_id=ticket.id, thread_key=thread_key, live_only=True
                )
                if subscription.mode is SubscriptionMode.OWN
            ]
            if not own:
                raise RouterStateError("ticket 不 own 该 thread")
            for subscription in own:
                self._store.end_subscription(subscription.id, ended_at=now)
            ensure_subscription(
                self._store,
                ids=self._ids,
                ticket_id=ticket.id,
                thread_key=thread_key,
                mode=SubscriptionMode.CONTEXT,
                created_at=now,
            )
            return record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=[trigger.id],
                outcome=DecisionOutcome.OBSERVE,
                strength=DecisionStrength.INFERRED,
                reason_codes=["handback"],
                policy_version=self._policy.policy_version,
                request_digest=trigger.digest,
            )

    def resume(self, ticket_id: str, *, trigger_id: str, actor: str) -> Ticket:
        """已结束 Ticket 的唯一重开路径：operator + 显式请求 + Mandate 复检。"""
        self._require_operator(actor)
        trigger = self._require_trigger(trigger_id)
        signal = RequestSignal.from_trigger(trigger)
        if signal is None:
            raise RouterStateError("resume 需要显式 request")
        admission = self._policy.admit_explicit(
            ExplicitRequest(
                repository=self._router.config.repository,
                requester=trigger.actor,
                mentioned=signal.mentioned,
                stable_id=signal.stable_id,
                text=signal.text,
            )
        )
        if self._audit is not None:
            self._audit.admitted(admission)
        if not admission.admitted:
            raise RouterPermissionError(f"resume 未准入：{admission.reason_code}")
        with self._store.transaction():
            ticket = self._require_ticket(ticket_id)
            if ticket.status is TicketStatus.CANCELLED:
                raise RouterStateError("cancelled ticket 不可 resume")
            if ticket.status not in (TicketStatus.COMPLETED, TicketStatus.FAILED):
                raise RouterStateError(f"ticket 未结束：{ticket.status.value}")
            mandate = self._store.get_mandate(ticket.mandate_id)
            if mandate is None:
                raise RouterStateError("mandate 不存在")
            verdict = self._policy.revalidate(mandate, when="launch")
            if self._audit is not None:
                self._audit.revalidated(mandate, "launch", verdict, ticket_id=ticket.id)
            if not verdict.allowed:
                raise RouterStateError(f"revalidation_failed:{verdict.reason}")
            now = self._clock.now_ms()
            pending = [
                item
                for item in self._store.list_inputs(ticket.id)
                if item.state == "pending" and item.payload == signal.text
            ]
            if not pending:
                sequence = len(self._store.list_inputs(ticket.id)) + 1
                self._store.insert_input(
                    TicketInput(
                        id=self._ids.new_id("in"),
                        ticket_id=ticket.id,
                        sequence=sequence,
                        kind="task",
                        payload=signal.text,
                        created_at=now,
                    )
                )
            updated = ticket.model_copy(
                update={
                    "status": TicketStatus.QUEUED,
                    "queued_at": now,
                    "ended_at": None,
                    "woken_at": None,
                }
            )
            self._store.update_ticket(updated)
            attempt = len(self._store.list_assignments(ticket.id)) + 1
            assignment = Assignment(
                id=self._ids.new_id("as"), ticket_id=ticket.id, attempt=attempt
            )
            self._store.insert_assignment(assignment)
            self._store.enqueue_effect(
                Effect(
                    id=self._ids.new_id("ef"),
                    kind=EffectKind.LAUNCH_ASSIGNMENT,
                    idempotency_key=f"launch:{assignment.id}:{attempt}",
                    payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
                    due_at=now,
                )
            )
            own = [
                subscription
                for subscription in self._store.list_subscriptions(
                    ticket_id=ticket.id, live_only=True
                )
                if subscription.mode is SubscriptionMode.OWN
            ]
            if not own and trigger.thread_keys:
                ensure_subscription(
                    self._store,
                    ids=self._ids,
                    ticket_id=ticket.id,
                    thread_key=trigger.thread_keys[0],
                    mode=SubscriptionMode.OWN,
                    created_at=now,
                )
            record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=[trigger.id],
                outcome=DecisionOutcome.ACCEPT,
                strength=DecisionStrength.EXPLICIT,
                reason_codes=["resumed"],
                policy_version=self._policy.policy_version,
                request_digest=admission.request_digest,
                mandate_id=mandate.id,
            )
            return updated

    def record_pull_request(
        self, ticket_id: str, *, report_id: str, actor: str
    ) -> Subscription:
        """Worker 报告里出现的 PR 才能升为 own；正文只取数字，不取授权（L4）。"""
        self._require_operator(actor)
        with self._store.transaction():
            ticket = self._require_live_ticket(ticket_id)
            report = next(
                (
                    item
                    for item in self._store.list_reports(ticket.id)
                    if item.id == report_id
                ),
                None,
            )
            if report is None:
                raise RouterNotFoundError(f"report 不存在：{report_id}")
            number = report.details.get("pull_number", report.details.get("pr_number"))
            if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                raise RouterStateError("report 缺少 pull_number")
            thread_key = pull_request_thread_key(
                self._router.config.repository, number
            )
            self._store.upsert_thread(
                Thread(
                    key=thread_key,
                    repository=self._router.config.repository,
                    kind=ThreadKind.PULL_REQUEST,
                    external_ref=f"#{number}",
                    last_trigger_at=report.created_at,
                    status=ThreadStatus.OPEN,
                )
            )
            now = self._clock.now_ms()
            for subscription in self._store.list_subscriptions(
                ticket_id=ticket.id, thread_key=thread_key, live_only=True
            ):
                if subscription.mode is SubscriptionMode.CONTEXT:
                    self._store.end_subscription(subscription.id, ended_at=now)
            return ensure_subscription(
                self._store,
                ids=self._ids,
                ticket_id=ticket.id,
                thread_key=thread_key,
                mode=SubscriptionMode.OWN,
                created_at=now,
            )
```

- [ ] **Step 4: 跑绿并做静态检查**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_service.py
.venv/bin/pytest -q --no-cov tests/unit/test_supervisor_router.py \
  tests/unit/test_supervisor_service.py
.venv/bin/ruff check stableagent/supervisor/service.py
.venv/bin/ruff format --check stableagent/supervisor/service.py
.venv/bin/mypy
```

Expected: `15 passed`（Service）；Router + Service 合计 `38 passed`；ruff / mypy 全绿。

- [ ] **Step 5: 提交**

```bash
git add stableagent/supervisor/service.py tests/unit/test_supervisor_service.py
git commit -m "feat: handback resume and pull-request subscription commands"
```

---

### Task 7：CI FOCUS 32、Router 指南与 M3 出口（M3-8）

**Files:**
- Modify: `tests/unit/test_ci_focus_gate.py`
- Modify: `.github/workflows/ci.yml`
- Create: `docs/guides/supervisor-router.md`
- Modify: `docs/acceptance/p6-supervisor-beta.md`、`README.md`、`docs/README.md`、
  `docs/acceptance/README.md`、`docs/Roadmap.md`、`CHANGELOG.md`

**Interfaces:**
- Consumes: 前 6 个 Task 的测试与模块。
- Produces: 32 文件心脏闸门；Router 运维文档；M3 收口读数与状态面。

- [ ] **Step 1: FOCUS 29 → 32（先写红）**

`tests/unit/test_ci_focus_gate.py`：

```python
    "supervisor/source.py",
    "supervisor/assessor.py",
    "supervisor/router.py",
    "supervisor/service.py",
]


def test_focus_covers_the_thirty_two_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 32
    assert "32 文件" in text  # 步骤名与注释同步
    assert text.count('".py"') == 0  # 不许用通配兜底
```

`.github/workflows/ci.yml` 的步骤名改为
`心脏模块覆盖率闸门（32 文件：loop/checkpoint/errors/context/evidence/orchestration/control/execution/integrations/supervisor 各 ≥90%）`，
并在 `FOCUS` 元组中 `"supervisor/source.py",` 之后追加：

```python
              "supervisor/assessor.py",
              "supervisor/router.py",
              "supervisor/service.py",
```

- [ ] **Step 2: 跑红再跑绿**

```bash
.venv/bin/pytest -q --no-cov tests/unit/test_ci_focus_gate.py
```

Expected（只改测试、未改 workflow 时）：`assert "32 文件" in text` 失败；补齐 workflow
后 `1 passed`。

- [ ] **Step 3: 写 Router 运维指南**

`docs/guides/supervisor-router.md` 全文：

```markdown
# Supervisor Router 运维指南（P6 Beta）

本指南对应当前 Beta 的 `observe` 锁定：Router 只接受显式任务，推断候选只记录不执行。

## 1. 唯一入口

`RouterService.receive(trigger)` 是外部事件进入领域的唯一命令。它在一个
`BEGIN IMMEDIATE` 内完成 Trigger 去重、Subscription 扇出与 Decision；任一步失败整体
回滚。Source 已持久化的 Trigger 仍可交给 `receive`：路由层用"是否已有 Decision"判断
是否重复，存储层用 `UNIQUE(source, external_id)` 兜底。

## 2. 去重与顺序

- 同一 `external_id` 只存一行 Trigger；重复投递返回 `duplicate=True`，不新增 Decision、
  Ticket 或 effect。
- 一个 Trigger 的同一 Ticket 至多扇出一条 Input；`sequence` 按 Ticket 递增，投递走
  `deliver_input` effect。
- `reevaluate` 是运维重评估，不重复扇出，只追加一条递增 `attempt` 的 Decision。

## 3. 三档自治

| mode | explicit | inferred |
|---|---|---|
| `off` | 接受 | IGNORE，记录 `autonomy_off`，不调评估器 |
| `observe` | 接受 | OBSERVE，只记录 would-accept 与 reason code |
| `auto` | 构造期拒绝 | 未启用 |

P6 Beta 不允许部署 `auto`；该枚举值只为 P7 保留。

## 4. outcome 与 reason code

| outcome | 触发条件 | 典型 reason |
|---|---|---|
| `accept` | 显式、未准入问题不存在、无既有 owner/重复 | `admitted` / `resumed` |
| `observe` | 推断候选；已结束 Ticket 的后续；多 owner | `inferred_candidate` / `resume_requires_operator` / `ambiguous_owner` |
| `ignore` | 未准入、正文畸形、无 Thread、`off` | `actor_not_allowlisted` / `malformed_request` / `thread_unknown` / `autonomy_off` |
| `attach` | 已有 Ticket 的 own 后续或重复请求 | `owned_thread_followup` / `duplicate_request` |
| `stop` | 已 stop Ticket 的后续 | `stopped_ignored` |
| `unavailable` | 评估器异常 | `assessor_error` |

## 5. 命令与权限

所有命令的 `actor` 必须在 `RouterConfig.operators` 中（大小写不敏感）：

- `attach(ticket, thread)`：相关 Thread 升为 context 订阅，幂等。
- `subscribe(ticket, trigger)`：按 Trigger 主 Thread 执行 attach。
- `unsubscribe(ticket, thread)`：结束该 Thread 的 live 订阅，返回结束行数。
- `stop(ticket)`：取消未结束 Ticket、入队 cancel effect、结束 context 订阅；own 订阅保留
  作为"已停"护栏，后续明确消息记 `stop` 并丢弃。
- `handback(ticket, thread, trigger)`：own 降为 context，之后的明确消息可创建新 Ticket。
- `resume(ticket, trigger)`：只允许 `completed` / `failed`；要求显式请求通过准入，并用
  `when="launch"` 复检 Mandate；`cancelled` 永久拒绝。
- `record_pull_request(ticket, report)`：Worker 报告里带 `pull_number` 才能把 PR Thread
  升为 own。

## 6. 订阅生命周期

- 创建 Ticket 时绑定 origin Thread 为 own。
- Worker 报告 PR 后由 `record_pull_request` 添加 own PR 订阅；同一 Thread 的 context
  订阅被结束，避免重复投递。
- 其他 Thread 只能是 context；context 只提供上下文，不扩大 Mandate、不创建 Ticket。
- Ticket 完成后 own 订阅保留；后续明确消息存为 pending Input 并记
  `resume_requires_operator`，由 operator 决定是否 `resume`。
- 人工 `stop` 后 own 订阅也保留但不接收：后续明确消息记 `stopped_ignored`。

## 7. 安全边界

- 外部正文只进 digest、TicketInput 与 reason 记录，不决定 action 集合（L4）。
- Router 事务体内无 `await`（L3）；Ticket/Assignment/Effect 与 Decision 同事务。
- Channel 不授予权限；`merge` / `auto_merge` 结构性缺席（L6）。
- 全部测试离线；真实 GitHub 演练与推送需维护者显式授权（L8）。
```

- [ ] **Step 4: 全量门禁、三解释器与边界复核**

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m3-full.txt 2>&1
.venv/bin/ruff check stableagent tests examples benchmarks > /tmp/p6-m3-ruff.txt 2>&1
.venv/bin/ruff format --check stableagent tests examples benchmarks > /tmp/p6-m3-format.txt 2>&1
.venv/bin/mypy > /tmp/p6-m3-mypy.txt 2>&1
.venv/bin/python - <<'PY' > /tmp/p6-m3-coverage.txt 2>&1
import xml.etree.ElementTree as ET

root = ET.parse("coverage.xml").getroot()
wanted = {"supervisor/assessor.py", "supervisor/router.py", "supervisor/service.py"}
for cls in root.iter("class"):
    name = cls.get("filename", "")
    if name in wanted:
        lines = cls.findall("./lines/line")
        hit = sum(1 for line in lines if int(line.get("hits", "0")) > 0)
        print(f"{name}: {hit}/{len(lines)} = {hit / len(lines):.2%}")
PY
git diff 234f620..HEAD --stat -- stableagent/core/ > /tmp/p6-m3-l1.txt 2>&1
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py > /tmp/p6-m3-c1.txt 2>&1
grep -rnE "(from|import) +stableagent\.supervisor" --include='*.py' \
  stableagent/orchestration stableagent/control stableagent/execution stableagent/integrations \
  > /tmp/p6-m3-reverse.txt 2>&1 || true
git diff --check > /tmp/p6-m3-diffcheck.txt 2>&1
```

三解释器全量（与 M2 相同，不用 `--no-cov`，避免只验证新文件）：

```bash
env -u NO_PROXY -u no_proxy .venv/bin/pytest -q > /tmp/p6-m3-py310.txt 2>&1
env -u NO_PROXY -u no_proxy .matrix/py311/bin/pytest -q > /tmp/p6-m3-py311.txt 2>&1
env -u NO_PROXY -u no_proxy .matrix/py312/bin/pytest -q > /tmp/p6-m3-py312.txt 2>&1
```

逐项读回 `/tmp/p6-m3-*.txt`，确认：全量 `0 failed`、三解释器 passed 数一致、三文件覆盖率均 `≥90%`、ruff
`All checks passed!`、format 已格式化、mypy `Success`、L1/C1 两个 diff 为空、反向 import
0 行、`git diff --check` 无输出。**只回填实测数字，不预填。**

- [ ] **Step 5: 回填账本与状态面**

- §1 的 M3-1…M3-8 全部改 `[x]`，每条写明用例文件、提交号与关键断言。
- M3 标题下状态改为 `M3 收口（Task 1~7 全部完成）`。
- §3 验证命令补 `tests/unit/test_supervisor_assessor.py`、
  `tests/unit/test_supervisor_router.py`、`tests/unit/test_supervisor_service.py`。
- §4 保持 `M3 展开为 7 个 Task / 38 个 Step`（与 Task 1~7 的 Step 数一致）。
- §9 末尾追加"出口读数"小节，粘贴 Step 4 的实测值。
- `README.md` P6 行、`docs/README.md` 主线、`docs/acceptance/README.md`、`docs/Roadmap.md`
  改为 `M3 完成（8/8）；里程碑 3/6`，并链接 `docs/guides/supervisor-router.md`。
- `CHANGELOG.md` 顶部新增 `### 实现（P6 M3 Router Intake，2026-11-09）`。

- [ ] **Step 6: 提交**

```bash
git add tests/unit/test_ci_focus_gate.py .github/workflows/ci.yml \
  docs/guides/supervisor-router.md docs/acceptance/p6-supervisor-beta.md \
  README.md docs/README.md docs/acceptance/README.md docs/Roadmap.md CHANGELOG.md
git commit -m "docs: close p6 m3 with router intake evidence"
```

---

### M3 出口读数（2026-11-09，落盘 `/tmp/p6-m3-*.txt` 后读回）

- 全量：`804 passed, 6 skipped, 0 failed`（19.54s，总覆盖 `98.76%`）。
- 三解释器：`.venv`（3.10）/ `.matrix/py311`（3.11.16）/ `.matrix/py312`（3.12.14）
  均为 `804 passed, 6 skipped`。
- 新增三文件覆盖率：`supervisor/assessor.py` `31/31 = 100.00%`；
  `supervisor/router.py` `267/274 = 97.45%`；`supervisor/service.py`
  `167/172 = 97.09%`（均 ≥90%）。
- 静态：`ruff check` `All checks passed!`；`ruff format --check`
  `151 files already formatted`；`mypy` `Success: no issues found in 68 source files`。
- 边界：`git diff 234f620..HEAD --stat -- stableagent/core/` 与
  `... -- stableagent/orchestration/service.py` 均为 0 行；下层包真实 import
  `stableagent.supervisor` 的 grep 命中 0 行；`git diff --check` 无输出。
- 计划预期偏差（以实测为准）：Task 1 计划写 `7 passed`，实际 6 例；Task 2 计划写
  `40 passed`，实际 51 例（含 M3 前存量）；Task 5 计划写 `8 passed`，实际 7 例；
  Task 6 计划写 `15 passed` / 合计 `38 passed`，实际 Service 14 例、合计 37 例。
  Task 7 为把 `service.py` 覆盖率从 86.63% 抬到闸门线，补了 8 例错误路径/审计用例，
  最终 Service 22 例、三文件合计 51 例。

## 10. M3 自检记录（writing-plans）

- **规格覆盖：** WP-6.3 第 1 条 → Task 3（`receive` 唯一入口、duplicate）；
  第 2 条 → Task 3（扇出与 sequence）；第 3 条 → Task 3（Policy gate 三档）与 Task 1；
  第 4 条 → Task 5/6（七条命令与权限）；第 5 条 → Task 3 + Task 6（own/context、
  PR own）；第 6 条 → Task 3（结束/stop/重复语义）与 Task 5（stop 记录）。
- **类型一致性：** `AutonomyMode` / `AssessmentContext` / `CandidateAssessment` 只在
  Task 1 定义；`RouterConfig` / `RequestSignal` / `Reception` / `RouterError` 只在 Task 3
  定义；`SupervisorService` 的构造签名从 Task 5 起不变，Task 6 只追加方法；
  `record_decision` / `ensure_subscription` / `latest_trigger` 三处 helper 只定义一次。
- **无占位符：** 所有实现步骤给出完整代码；Task 7 的出口读数在实测后回填，不预填数字。
- **边界：** `supervisor` 不 import `execution` / `integrations`（Task 2 的 Source 变更
  仍在 M2 包内）；`receive` / 命令各只开一个事务（L3）；外部正文只进 digest/Input（L4）；
  `resume` 的 `when="launch"` 与审计行是 L5 的唯一新增复检点；`auto` 构造期拒绝与
  `forbidden merge` 保持 L6。
