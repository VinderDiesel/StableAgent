# Runbook：Auto 回滚

Auto **默认关闭**。开启须经 `AutoGate.enable`（operator）且 Router `mode=auto`。

## 立即回滚

1. `AutoGate.disable(rule_id, operator=...)` —— inferred 路径立即回到 OBSERVE。
2. 可选：将 `RouterConfig.mode` 改回 `observe` / `off` 并重启。
3. 检查审计：`enable` / `disable` / `accept` 记录。

## 验证

离线：`tests/unit/test_router_auto.py`；示例 `examples/07_auto_rule_offline.py`。
