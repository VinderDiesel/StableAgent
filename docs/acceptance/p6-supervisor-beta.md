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

- [ ] **M1-1** ADR-06 落盘并 Accepted，账本与本计划建立。
- [ ] **M1-2** `supervisor/` 包骨架与 `model.py`：六个记录、枚举、canonical digest。
- [ ] **M1-3** `SupervisorStore`：七张表、索引、`p6-supervisor-v1` marker、P5 库无损迁移、
      Trigger/Thread CRUD。
- [ ] **M1-4** Channel 与 Subscription CRUD：目的地唯一、live 订阅部分唯一、结束可重订。
- [ ] **M1-5** Decision（含 trigger 联结与 attempt 重评估）与 SourceCursor CRUD。
- [ ] **M1-6** `views.py`：`needs_attention`、`open_tickets`、`failed_effects`、
      `source_health`、`thread_timeline`、`ticket_timeline`。
- [ ] **M1-7** CI FOCUS 由 25 扩到 28（新增 supervisor model/store/views），契约测试同步。
- [ ] **M1-8** M1 出口：全量门禁、三解释器、L1/约束 1 复核、账本与状态面回填。

### M2：GitHubSource 与 Trigger 接入（规格 WP-6.2；待 M1 收口后展开）

### M3：Router Intake、Subscription 与 Decision（规格 WP-6.3；待 M2 收口后展开）

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
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
git diff 234f620..HEAD --stat -- stableagent/core/   # 必须 0 行（L1）
git diff 234f620..HEAD --stat -- stableagent/orchestration/service.py   # 必须 0 行
```

## 4. 任务级实施计划说明

M1 展开为 **8 个 Task / 47 个 Step**。每个 Task 独立可验收、独立提交；Task 之间的
接口在各自 `Interfaces` 块写清。任务级 Step 不计入里程碑步骤。

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
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
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
        rows = self._conn.execute(
            "SELECT * FROM source_cursors ORDER BY source ASC"
        ).fetchall()
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
    store.put_source_cursor(
        SourceCursor(source="github", cursor="9", health=SourceHealth.DEGRADED)
    )
    assert [c.health for c in SupervisorViews(store).source_health()] == [
        SourceHealth.DEGRADED
    ]


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
