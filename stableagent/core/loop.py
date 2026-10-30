"""异步事件驱动 Agent Loop（Phase 1 交付）。

契约约束（P0 已钉死）：loop 以 AgentState 为唯一事实源、每步转移为纯函数，
为 P2 checkpoint/恢复与 P3 上下文组装位点预留接入点，见 00-contracts.md。
"""
