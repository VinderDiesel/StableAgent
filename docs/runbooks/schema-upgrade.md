# Runbook：Schema 升级

- 迁移策略：只加表/列 + `schema_migrations` marker；不做破坏性 DROP。
- 升级前备份（见 backup-restore）。
- 打开新版本进程即自动 `CREATE IF NOT EXISTS` / `ALTER` 加列。
- 回滚应用版本时：旧代码须容忍新列/新表（兼容扩展）；勿手工删 marker。
