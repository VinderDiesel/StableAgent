# Runbook：Source 重连

1. 查看 `source_health`（Console status / API）。
2. 失败分类：`failed` → 检查 cursor 与凭据引用；勿手工改 cursor 除非确认重复安全。
3. 修复 transport 后重启 poll；确认 bot 过滤与幂等仍生效。
4. Slack fake / GitHub Source 行为见 [sources-mcp](../guides/sources-mcp.md)。
