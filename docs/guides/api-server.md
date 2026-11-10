# API Server 指南（P7 M5）

可选 HTTP 监管面：需安装 `stableagent[server]`（FastAPI）。Core 安装不含 Web 依赖。

## 启动

```bash
pip install 'stableagent[server]'
```

```python
from stableagent.api import create_app
from stableagent.supervisor.console import Console, ConsoleConfig
# console / store 由运行时装配
app = create_app(console, store, tenant_id="default")
# uvicorn stableagent...:app
```

App **绑定**单一 `tenant_id`；请求头 `X-Tenant-Id` 必须匹配，否则 403。

## 鉴权（Header Principal，非 JWT）

| Header | 含义 |
|---|---|
| `X-Tenant-Id` | 租户 |
| `X-Actor-Id` | 操作者 |
| `X-Roles` | 逗号分隔：`viewer` / `reviewer` / `operator` / `admin` |

角色等级：viewer → reviewer → operator → admin。写动作另需 Console `operators` 集合命中。

## 路由

| Method | Path | 最低角色 |
|---|---|---|
| GET | `/v1/status` | viewer |
| GET | `/v1/tickets/{id}` | viewer |
| GET | `/v1/threads/{id}` | viewer |
| GET | `/v1/events/tickets/{id}` | viewer（SSE） |
| POST | `/v1/approvals/{id}/decide` | reviewer |
| POST | `/v1/tickets/{id}/stop\|retry\|reevaluate` | operator |
| POST | `/v1/runtime/pause\|resume` | operator |
| GET | `/` | 静态注意力板 |

API **只经** Console / Services / Views；不直写 SQLite，不绕过 Policy/Outbox。

## SSE

- Query：`cursor`（上次事件 id）、`max_buffer`（背压上限）
- 事件 attrs 经脱敏；授权周期重检失败则截断流

## 租户路径

`stableagent.control.tenancy.tenant_path(root, tenant_id, *parts)` 拒绝 `..` 与绝对段。
