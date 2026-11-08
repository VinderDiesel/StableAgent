# Supervisor 迁移与降级

P6 的 `SupervisorStore` 是 P5 `Store` 的增量扩展：同一 SQLite 文件、同一连接、只新增
表与索引。升级不重写任何 P4/P5 表，因此可以随时用 P5 二进制打开同一文件。

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
