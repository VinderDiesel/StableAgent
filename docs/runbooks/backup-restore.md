# Runbook：SQLite 备份与恢复

## 备份

1. 暂停 Runtime（`Console.pause` 或 API `/v1/runtime/pause`）。
2. 复制 state 目录下的 `.db` 文件（含 `-wal`/`-shm` 时先 `PRAGMA wal_checkpoint(FULL)`）。
3. 校验备份可读：`sqlite3 backup.db 'SELECT version FROM schema_migrations LIMIT 1'`。
4. 恢复 Runtime。

## 恢复

1. 停止进程；将备份覆盖目标 db 路径。
2. 启动后确认 `schema_migrations` 含 `p7-observability-v1` / `p7-supervisor-v1` / `p7-memory-v1`。
3. 跑一次 `status` / `/v1/status` 确认队列与 source health。
