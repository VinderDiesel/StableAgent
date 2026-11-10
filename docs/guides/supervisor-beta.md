# 单仓库 Super-Agent Beta runbook（M6）

## 定位与前置缺口

本 runbook 定义维护者在**专用仓库**上执行受控 Beta 的步骤与门禁。当前仓库只有
`GitHubReadClient` / `GitHubClient` 协议与 fake，**真实 GitHub HTTP 客户端尚未实现**
（P5 账本已记录该遗留）。因此：

- 现在可复现的是离线演练：`examples/06_superagent_beta.py`（见下）。
- 真实仓库试跑必须先接入一个实现 `GitHubReadClient.list_events` 的只读客户端，并把
  写侧 `GitHubClient` 接到真实 API；接入前不得宣称完成真实 Beta。
- 真实试跑、dry-run 数据与人工标注为可选；工程收口不依赖回填（2026-11-10 起总口径见
  [Roadmap](../Roadmap.md)；P6 见账本「开发收口裁定」）。

## 离线演练（现在即可跑）

```bash
python examples/06_superagent_beta.py --phase run --dir .demo-beta
python examples/06_superagent_beta.py --phase report --dir .demo-beta
```

`run` 末行 `BETA_RESULT tickets=3 resumed=1 external_comments=1 duplicate_tickets=0
bot_triggers=0`；`report` 末行 `BETA_REPORT <json>`。演练覆盖多 Issue/PR/CI Trigger、
两个并发 Ticket、一个 FAILED、一个 WAITING、一个 COMPLETED 后经 operator resume 恢复，
并断言同 Thread 不重复建票、外部评论恰好一次、bot 自事件被过滤。

## 安全边界（不可协商）

- 单仓库、专用 bot 账号；`actors` allowlist 只放维护者；`operators` 只放维护者。
- `mode=observe`：推断候选只记录、不执行；显式请求才建 Ticket。
- 不启用 auto merge；`grantable_actions` 排除 `enable_auto_merge`。
- 最小 GitHub token：只读阶段只需 repo 读权限；写阶段才加 issue comment / PR 创建权限。
- token 只留在客户端 headers，不进 prompt/日志/Report/effect payload（ADR-05 安全声明 5）。
- 所有外部消息先草拟成 `COMMENT_ISSUE` 审批，人工批准才发；拒绝即静默。

## 阶段一：record-only / dry-run

1. 备份状态库：`cp state.db state.db.bak-$(date +%Y%m%d)`。
2. 配置 `console.json`（`extra="forbid"`，无 token 字段）：

   ```json
   {
     "repository": "owner/repo",
     "operators": ["alice"],
     "actors": ["alice"],
     "actions": ["read_repository", "comment_issue"],
     "default_validity_ms": 86400000,
     "notifications": {"enabled": true, "max_chars": 400, "since_ms": 0}
   }
   ```

3. 以 `observe` 启动 Runtime，只读轮询，不派发任何写 effect；观察 Source 健康与 cursor。
4. 用只读命令巡检：

   ```bash
   python -m stableagent.supervisor.cli --db state.db --config console.json --operator alice status
   ```

5. 每日导出 Beta 报告并归档：

   ```bash
   python examples/06_superagent_beta.py --phase report --dir .demo-beta
   ```

   真实库的报告用同一 `beta_report(store)` 读取（示例 report phase 读 `.demo-beta/beta.db`；
   真实库可写一段只读脚本调用 `beta_report`，不得直接读 `_conn`）。

## 阶段二：explicit 请求创建 PR

1. 确认阶段一数据无误报泛滥、通知数克制后，才允许显式请求触发写链路。
2. 写命令一律带 `--confirm`，operator 门禁先于确认：

   ```bash
   python -m stableagent.supervisor.cli --db state.db --config console.json --operator alice \
     approve <approval-id> --confirm <approval-id>
   ```

3. PR 创建仍走 P5 审批门与 worktree 复检；不开放 auto merge。
4. 每个 Ticket 的确认/结果/attention/失败通知各最多一条；普通进度与 CI 更新静默。

## 数据记录表（维护者人工填写）

库可推导的字段由 `BetaReport` 给出；下表只记库无法推导的人工标注。

| 日期 | 候选数 | would-accept | 人工接受 | 人工拒绝 | 拒绝原因 | 误报 | 漏报 | 每 Ticket 通知数 | 恢复次数 | PR 重复数 |
|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | |

- `候选数 / would-accept / 每 Ticket 通知数 / 恢复次数` 同时抄录 `BetaReport` 的
  `decisions`、`would_accept`、`notification_counts`、`recovered_assignments` 以核对。
- **PR 重复数必须为 0**；任何非 0 立即停止写阶段并回滚。
- 误报/漏报与接受/拒绝原因只能人工标注，不从库推导。

## 回滚与降级

1. 停止 Runtime（优雅停止，不取消 Worker）：等待宽限期后释放运行锁。
2. 暂停 intake：`pause --confirm pause`；必要时 `stop <ticket-id> --confirm <ticket-id>`。
3. 恢复备份：`cp state.db.bak-<date> state.db`（先确认无进程持锁）。
4. 降级到 P5：P6 只新增表与索引，P5 二进制可直接打开 P6 库读旧表；步骤见
   [supervisor 迁移指南](supervisor-migration.md)。
5. 撤销 bot token，归档状态库与本报告。

## 发布门禁 / 工程收口

- **工程收口**只看代码完成与离线可靠性（[Roadmap 工程收口口径](../Roadmap.md)）；本
  runbook 的真实试跑与人工记录表为**可选运维附录**，不阻塞任何阶段或里程碑勾选。
- `auto` 默认关闭；打开与否是运维决策。若选择在真实仓库评估，可使用下方数据记录表，
  但仍非 DoD。