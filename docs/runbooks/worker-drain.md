# Runbook：Worker / Runtime drain

1. `pause` 停止新路由与调度。
2. 等待 due effects / running assignments 清空（`status` queue）。
3. 优雅停止 `Runtime.serve`（宽限期后释放锁）。
4. 确认无残留 worktree 锁；需要时按 execution runbook 清理。
