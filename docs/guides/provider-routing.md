# Provider 路由与成本指南（P7 M2）

## 选择时机

`ProviderRouter.select` **只在 Assignment 启动前**调用。结果经
`apply_provider_selection` 写入 `Assignment.worker_config["provider_selection"]`。
同一 Assignment 不得再次写入（禁止 step 内静默换模型）。

```python
from stableagent.providers.routing import (
    ProviderRouter,
    RoutingRequest,
    merge_provider_selection,
    read_provider_selection,
)

router = ProviderRouter([...])
selection = router.select(RoutingRequest(prefer_cheap=True, price_table_version="price-v1"))
assignment = assignment.model_copy(
    update={"worker_config": merge_provider_selection(assignment.worker_config, selection)}
)
assert read_provider_selection(assignment.worker_config) == selection
```

`ProviderSelection` 含：候选列表、理由、model、price_table_version、fallback 链。

## 成本与预算

`stableagent.control.cost`：

- `PricingTable.version` 必须进入选择记录
- `CostLedger.record_usage`：实际 usage 优先；缺失则估算并 `estimated=true`
- `BudgetedProvider` 包装内层 Provider：硬超支抛 `HardBudgetExceeded` 且**零次**内层调用
- 软超支可选 `raise_soft=True` → `SoftBudgetExceeded`（外层可转审批）

租户成本规则留在 Core 外，不塞进 `AgentLoop`。

## 两层超时

| 层 | 配置 | 作用 |
|---|---|---|
| httpx | `ProviderConfig.request_timeout_seconds` | 单次 HTTP 客户端超时 |
| 内层硬超时 | `ProviderConfig.call_hard_timeout_seconds` | `AgentLoop` 用 `asyncio.wait_for` 包裹整次 `acomplete`；`None`=关闭 |
| 外层 | `Assignment.deadline_at` | Scheduler 取消 Assignment → Ticket blocked |

## Provider

- OpenAI：既有 `/chat/completions`
- Anthropic：Messages `/v1/messages`
- Ollama：`/api/chat`

默认 CI 仅 MockTransport；`@pytest.mark.live` 为可选烟雾。
