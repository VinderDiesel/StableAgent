# Runbook：事故回滚

1. `pause` Runtime；停止对外 Channel 投递（必要时禁用 MCP server）。
2. 对错误 Ticket：`stop` / 拒绝 pending approval。
3. 回滚应用版本或配置（含 AutoRule disable，见 auto-rollback）。
4. 从备份恢复 DB（如数据损坏）。
5. 审计：从 effect → decision → mandate 追溯；脱敏事件见 observability 指南。
