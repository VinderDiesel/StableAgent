# Memory 指南（P7 M4）

知识 Memory 是 Worker/Router 可检索的受控服务，**不是** `AgentState` 字段，也**不是**
checkpoint 的 `MemoryCheckpointStore`（进程内 run 快照）。

## 分层

| layer | scope | 用途 |
|---|---|---|
| `short` | Ticket / Assignment | 摘要、偏好、已验证事实（短 TTL） |
| `long` | tenant / repository | 文档或事实记录（长 TTL 或无过期） |

同一 SQLite 表 `memories`，marker `p7-memory-v1`；`MemoryStore(ObservabilityStore)`
独立于 `SupervisorStore` 继承链。

## 写入必经 Policy

```python
from stableagent.memory.policy import MemoryPolicy
from stableagent.memory.service import MemoryService
from stableagent.memory.store import MemoryStore

store = MemoryStore(path, clock=clock)
svc = MemoryService(store, clock=clock, policy=MemoryPolicy())
svc.short_term.remember_ticket(tenant_id="default", ticket_id="tk-1", content="…")
svc.long_term.remember_repository(
    tenant_id="default", repository_id="org/repo", content="…"
)
```

- 允许 origin：`worker_summary` / `verified_fact` / `operator` / `policy` / `import`
- 同 scope `content_digest` 去重；容量与 TTL 由 Policy 控制
- `SECRET` 默认拒绝；内容入库前脱敏截断
- 模型/MCP **无**直写旁路

## 检索与注入

- `LexicalRetriever`：确定性 token overlap；无向量库、无在线 embedding
- 必须带 `MemoryAuthContext`（tenant + allowed scopes）；跨租户/越权 scope 不可见
- 过期与已 erase 不可检索
- `svc.inject(messages, query, auth)` 在消息前插入 system 块；不改 AgentLoop 签名

## 删除与降级

- `erase` / `erase_scope` → 软删 + `EraseReceipt`
- 存储/检索异常 → 空结果或跳过写入，记 `memory.degraded`；**不阻断 Ticket**

## 领域事件

`memory.written` / `memory.retrieved` / `memory.erased` / `memory.degraded`

## 离线评测

`stableagent/memory/eval_fixtures.py` + `tests/unit/test_memory_eval.py`：召回、精度、
陈旧事实、权限泄漏、token 成本；不依赖在线 embedding。
