# Sources / Channel / MCP 指南（P7 M3）

## Source

- 契约：`supervisor.source.Source.poll() -> PollBatch`
- 实现：`supervisor.sources.github.GitHubSource`、`sources.slack.SlackFakeSource`
- Slack 为 fake fixture（无真实 HTTP）；cursor 键为 `slack:{tenant_id}`
- 自身 bot / `bot=True` 消息被过滤；`UNIQUE(source, external_id)` 去重

## Channel

- Slack 回写：`EffectKind.SLACK_MESSAGE` + `channels.slack.slack_message_effect`
- `SlackChannelWriter` 强制 `tenant_id` 匹配；跨租户永久失败

## IdentityLink

- 显式跨 Source 身份；`SupervisorStore.insert_identity_link` / `revoke` / `resolve_identity`
- **无**隐式映射：未链接则 `resolve_identity` 返回 `None`

## McpRegistry

```python
from stableagent.integrations.mcp_registry import McpRegistry, McpServerDescriptor

reg = McpRegistry(authorize=lambda op, action: op == "admin")
reg.register(descriptor, operator="admin")
reg.enable("srv-1", operator="admin")
# discovery ≠ invoke
reg.discover(tenant_id="tenant-a")
reg.assert_invokable("srv-1", tenant_id="tenant-a")  # 禁用或租户不匹配会抛错
```

远程 transport 安全字段在 `McpRemoteSecurity`（TLS、超时、大小、域名白名单、凭据引用）。
