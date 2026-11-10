# 部署指南（P7 M6）

## 依赖

- Core：`pip install stableagent`
- API：`pip install 'stableagent[server]'`

## 配置

使用 [`DeployConfig`](../../stableagent/deploy_config.py) 校验部署字段：

- `tenant_id` / `state_dir` / `db_filename`（禁 `..`）
- `secret_refs`：只存引用名，**禁止**明文密码进配置
- 每 tenant 独立 `state_dir` 与 Runtime lock

## 容器

根目录 [`Dockerfile`](../../Dockerfile)：非 root、`HEALTHCHECK` 打 `/healthz`。
编排层注入 uvicorn + `create_app(console, store, tenant_id=...)`。

## Runbooks

见 [`docs/runbooks/`](../runbooks/)：backup-restore、schema-upgrade、worker-drain、
source-reconnect、incident-rollback、auto-rollback。

## Auto

默认关闭。开启与回滚见 [auto-rollback](../runbooks/auto-rollback.md) 与
`examples/07_auto_rule_offline.py`。
