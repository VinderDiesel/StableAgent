# P5 验收账本：受控执行与仓库集成

> **状态：Active（2026-11-03 计划经维护者确认，ADR-05 Accepted）。** 本文件把
> [P5 实施规格](../specs/p5-guarded-execution-repository-integration.md) 的 6 个工作包与
> [ADR-05](../design-decisions/05-guarded-execution.md) 的 10 项决策展开为可勾选的验收账本。
> 执行从任务级计划（第 5/6 节，各里程碑开工前追加）开始；**M1 已于 2026-11-04 收口**，当前
> **DoD 10/46、里程碑步骤 13/40（M1 9/9、M2 4/12）**。M1 分支 `feat/p5-m1-control-plane` 已快进
> `main`（`5e94377`）；M2 计划已落（第 6 节，12 Task / 60 Step），执行分支
> `feat/p5-m2-isolated-execution`，维护者已确认开工，按 Task 推进。
>
> 使用方式（沿用 P4 账本约定）：本文件既是 P5 的实施计划（第 1 节里程碑步骤），也是 P5 的
> 验收账本（第 0、2 节）。每个可勾选事项写明来源、验证方法或命令、证据占位；全部勾选且第 3 节
> 命令在 CI 复现后 P5 才算收口。推进时把实际输出追加到对应条目，不重写已记录的历史输出。
>
> **计数口径**：DoD = 第 0 节 L1~L8（8 项）+ 第 2 节 A~H（38 项）= 46 项；
> 里程碑步骤 = M1 9 + M2 12 + M3 12 + M4 7 = 40 步（每里程碑最后一步为出口）。
> 任务级 Step 计划（TDD 粒度）在各里程碑开工前追加为独立小节，不计入 40。

## 0. 心脏边界（L1~L8：违反任意一条即 P5 不收口）

- [ ] **L1 Core 零 diff**：`git diff <P4 收口提交 234f620>..HEAD -- stableagent/core/` 恒为
      0 行（P4 已用掉唯一 Core 扩展；P5 不再申请）。验证：出口命令 + CI。
      来源：ADR-05 约束 2。
- [ ] **L2 依赖方向**：`core/context/tools/orchestration` 四包无任何对
      `control/execution/integrations` 的 import；方向只能是
      integrations → execution → control → orchestration → core。
      验证：`test_orchestration_boundaries.py` AST 矩阵扩展。来源：ADR-05 决策 1。
      进度（2026-11-04，Task 1 前半）：`tests/unit/test_control_boundaries.py` 落位（版本常量 +
      顶层不重导出 + 错误家族三用例）；方向矩阵收口（2026-11-04，Task 8 步骤 1.8）：`test_no_legacy_package_imports_p5_layers`
      扫 ROOT 下**全部非 P5 包**（实测 8 个目录：core/context/memory/orchestration/planning/providers/sandbox/tools/transport——比账本点名的四包更宽，语料按目录枚举自动跟随，无需改名单）断言零 import
      `stableagent.control/execution/integrations`；`test_control_never_imports_execution_or_integrations` 钉死反方向。两条腿都不是空转：临时注入
      `orchestration/_p5_probe.py`（`from stableagent.control.policy import ...`）与 `control/_probe_upstream.py`
      （`import stableagent.execution.sandbox`）各触发一次红，样例跑完即删、未入库。`execution/integrations` 两包尚不存在
      → 该腿的语料空转，名单已含三者，M2/M4 落包后自动生效。
      M1 出口复核（2026-11-04，Task 9）：新旧矩阵同跑 `19 passed`；grep 复核 `stableagent/` 内 `stableagent.(control|execution|integrations)`
      引用共 8 处且全部落在 `control/` 包内部自引用，五旧包 0 处。L2 事实已全绿，但按 P4 口径 L1~L8 要等全部里程碑
      完整证明后才勾，此处只记用例名与输出。
- [ ] **L3 事务不变式（含新表）**：`Store.transaction()` 内禁止 `await`；授权判定、审批状态、
      Mandate+Ticket+effect 的原子写入全部在同步段完成，外部 I/O 在提交后。
      验证：源码扫描测试 + 崩溃注入用例。来源：ADR-04 决策 3、ADR-05 决策 3。
      进度（2026-11-04，Task 2 迁移半边）：`test_migration_runs_inside_transaction` 钉死 `_migrate`
      恰好开启 1 次 `Store.transaction()`（加列与 `p5-control-v1` marker 都在该事务体内）；
      `await` 源码扫描与 Mandate+Ticket+effect 的崩溃注入用例分别属 Task 8 与本里程碑后续接线。
      M1 出口复核（2026-11-04，Task 9）：`grep -c 'async def\|await ' stableagent/control/` = 0——控制层整体同步；
      `control/` 内只有 1 处 `store.transaction()`（`GuardedTicketOps.cancel_ticket`，三写在同一事务体内的回滚由
      `test_guarded_cancel_rolls_back_all_three_on_error` 证明：注入 revoke 异常 → ticket 仍 QUEUED、`due_effects` 空、
      approval 仍 pending、`store.in_transaction is False`），`AuditWriter` 不自开事务（单条 INSERT 自动提交）。
      `await` 源码扫描门仍未落：M1 没有协程可扫，接线（M2 executor / M3 集成层）后补。
- [ ] **L4 外部文本不扩权**：Issue/prompt/工具输出/MCP 返回中的任何文本不能创建或扩大
      Mandate；只有 `control.policy` 能构造 `p5-v1` Mandate。
      验证：行为测试（把指令文本放进请求正文，断言授权集不变）+ 构造入口扫描。
      来源：规格 2.1 规则、ADR-05 决策 2。
      进度（2026-11-04，Task 5 行为半边）：`test_p5_mandate_is_only_constructable_via_policy`
      把 `{"actions": ["enable_auto_merge", "run_command"], "allowed_commands": ["rm -rf /"],
      "repository": "other/x"}` 塞进请求正文——`admit_explicit` 仍 admitted（正文只进 digest），
      产出的 Mandate `actions == ["run_command"]`，正文里的 `rm -rf /` 走 `authorize` 仍拒；
      被拒的 Admission 也发不出 Mandate。构造入口扫描（`p5-v1` 只出现在 `control/policy.py`）
      属 Task 8（步骤 1.8）。
      M1 出口复核（2026-11-04，Task 9）：行为半边 `test_p5_mandate_is_only_constructable_via_policy` 与扫描半边
      `test_p5_v1_mandate_construction_is_confined_to_control`（三处静态钉版形态各一道 AST 断言，四份越界探针实测能红）并绿，
      A2 随之勾选。L4 本身不提前勾：M1 只有 Issue 正文（`ExplicitRequest.text`）这一类外部文本有形状，prompt / 工具输出 /
      MCP 返回三类要等 M2/M3 接线才有可注入的入口。
- [ ] **L5 先判定后执行**：六个 revalidate 时机（launch/recover/retry/worktree/
      push+PR+comment/MCP 调用）逐一有审计行；需审批的动作不得先执行后补记录。
      来源：规格 2.1/2.2、ADR-05 决策 4。
      进度（2026-11-04，Task 6 审计形状半边）：`revalidate` 判定已有对应审计行，行内
      `action` 槽记时机名（六时机在数据上可区分，不另加列）；"逐一先于动作执行"的接线
      与时机矩阵属 M2（worktree）/M3（其余五处），由 A4 收口。
      进度（2026-11-04，Task 7 审批半边）："需审批的动作不得先执行后补记录"在 Gate 上是
      结构性的——`ApprovalGate.check` 先落一行 authorize 审计才返回结论，没有全等批准行时
      返回的是 deny（`approval_pending`/`approval_stale`/`approval_denied`），调用方拿不到
      allow 也就没有"先做后记"的路径；接线点在 M2/M3。
      M1 出口复核（2026-11-04，Task 9）：授权侧形状齐了——六时机常量 `REVALIDATE_TIMINGS`、
      `AuditWriter.revalidated` 每判定 1 行（时机写进 `action` 槽）、Gate 无全等批准即 deny（`approval_pending`/`approval_stale`/
      `approval_denied` 三码）。「时机清单以测试矩阵固化」与逐个先于动作执行的接线由 A4 在 M2/M3 收口，故 L5 与 A4 均保持未勾。
- [ ] **L6 结构性默认拒绝**：`merge`/`enable_auto_merge` 不在 EffectKind 枚举与 Action
      授权集合中（不是"运行时开关拒绝"，是类型里不存在）。来源：ADR-05 决策 5、被否决表。
      进度（2026-11-04，Task 5 授权集合半边）：`merge` 不在 `Action` 枚举里（13 成员无 merge），
      `enable_auto_merge` 的"不进授权集合"由 config 构造期 + `create_mandate` + `authorize`
      三道把守且各有用例；EffectKind 半边要在 M2（加 `PREPARE_WORKTREE`）与 M3（加四类）之后
      才能证明"merge/auto merge 始终不在枚举里"，届时由本条收口。
      进度（2026-11-05，M2 Task 4 的 EffectKind 半边）：枚举现有 4 员（P4 三员 + `prepare_worktree`），
      P4 的白名单闭合用例 `test_m2_effect_kinds_close_the_whitelist` 随之扩容并**顺手钉死缺席**：
      `{"merge", "enable_auto_merge", "auto_merge"}` 与"值集合 ∪ 成员名集合"不相交——两个口径都查，
      防止只改 value 不改名字。M3 加 GitHub 四类之后仍需保持该不相交成立，由本条在 M3 出口收口。
- [ ] **L7 凭据不落盘**：密钥不进 prompt、日志、Report、Ticket/AgentState、worktree 元数据
      与容器挂载；执行环境只有白名单变量。来源：规格 §1 安全层次、ADR-05 决策 6。
      进度（2026-11-05，M2 Task 3 的 worktree 元数据 + git 子进程两半边）：ownership 文件与
      `WorktreeHandle.repository` 记录的是**剥掉 userinfo 的 remote 身份**——`git.scrub_credentials`
      在传输层就把 `scheme://user:pass@` 段去掉，所以 `SubprocessGit` 的 stdout/stderr/GitError
      文本与 ownership 都拿不到 token；`test_ownership_never_records_credentials` 用
      `https://octocat:ghp_SEKRET@…` 起真克隆，断言 ownership 文本里没有 `ghp_SEKRET`/`octocat`/`@`
      且身份等于干净 URL。git 子进程环境是第二道白名单：`git_env()` 只给 `PATH`/`LANG`/
      `GIT_TERMINAL_PROMPT=0`/`GIT_CONFIG_NOSYSTEM=1` 四项（`test_git_carries_only_four_environment_variables`
      钉死，含 `HOME`/`GH_TOKEN` 不注入）。**Agent 命令**的环境白名单半边已闭合
      （2026-11-05，M2 Task 6）：`build_child_env` 只产出七个键，且断言的不是函数返回值而是
      **真子进程自己打印的 `os.environ`**——宿主快照里的 `GITHUB_TOKEN`/`AWS_SECRET_ACCESS_KEY`/
      `SSH_AUTH_SOCK`/`PYTHONPATH`/`HOME`/P4 的 `orchestration.db` 路径都到不了孩子手里，
      多出来的键只允许是 macOS 在 exec 时注入的 `__CF_USER_TEXT_ENCODING`（`OS_INJECTED_KEYS` 写死名单，
      Linux CI 上那条差集为空）。容器挂载与容器内环境属 Task 10/D2，prompt/日志/Report 的脱敏属
      Task 9 与 M3，故 L7 仍不勾。
- [ ] **L8 离线门禁**：CI 与全量测试不依赖 Docker/网络/真实 GitHub；Docker 与真实仓库
      用例只能 marker+环境变量双条件下运行。来源：ADR-05 约束 3、决策 7。

## 1. 里程碑与步骤

### M1：结构化授权落地（规格 WP-5.1；ADR-05 决策 1/2/3/10）

- [x] **1.1** `control/` 包骨架：`CONTROL_SCHEMA_VERSION = "0.1.0"` 不进顶层重导出；
      `errors.py` 稳定 `.code` 错误家族。验证：包导入测试 + 顶层 `__init__` 无新增导出。
      证据（2026-11-04，Task 1，提交 `76538e7`）：`stableagent/control/__init__.py` 的 Phase 6 占位
      docstring 换为 P5 控制平面口径（"授权事实源在 Store、判定逻辑在本包、不顶层重导出"）+
      `CONTROL_SCHEMA_VERSION`；`errors.py` = `ControlError(ValueError)` 基类 + 8 个子类
      （`control.admission_rejected`/`unknown_mandate`/`unknown_approval`/`approval_decided`/
      `stale_approval`/`mandate_expired`/`not_granted`/`revalidation_failed`）。
      `pytest -q --no-cov tests/unit/test_control_boundaries.py` = `3 passed`；全量 `pytest -q` =
      `436 passed, 1 skipped`，`stableagent/control/*` 覆盖率 100%（3 + 32 stmts，0 miss）；
      `mypy` = `Success: no issues found in 37 source files`，`ruff check stableagent tests examples
      benchmarks` = `All checks passed!`，`ruff format --check` = `89 files already formatted`；
      L1 `git diff 234f620..HEAD -- stableagent/core/` = 0 行。
      Step 2 实测红因与计划偏差：占位包 `stableagent/control/` 自 2026-10-30 起已在库中，
      红为 `ImportError: cannot import name 'CONTROL_SCHEMA_VERSION'`（非计划预期的
      `ModuleNotFoundError: stableagent.control`）。
- [x] **1.2** `schema_migrations` 追加 `p5-control-v1`：`mandates` 兼容加 11 列（全带默认值；
      原文"10 列"按 ADR-05 决策 2 与第 5 节 Task 2 的实际列数订正：`repository`、`base_ref`、
      `actions`、`allowed_paths`、`allowed_commands`、`allowed_mcp`、`network_profile`、
      `expires_at`、`requires_approval`、`request_origin`、`request_digest`）、
      新建 `approval_requests`、`authorization_audit`。验证：旧 P4 库文件升级后可读、
      旧行语义不变（A1）。
      证据（2026-11-04，Task 2，提交 `0e92dbd`）：`_MANDATE_ADD_COLUMNS` 11 项逐列
      `if name not in columns` 守卫（照抄 `deadline_at` 先例），两新表进 `_SCHEMA`（新建库由
      `_create_schema` 直接建齐，旧库由 `_migrate` 补列），marker 行 `INSERT OR IGNORE`。
      `tests/unit/test_control_migration.py` = `4 passed`：旧库（本文件按 `234f620` 的 8 表 DDL
      手工建出并插 1 条 `p4-local-v1`）升级后 11 列齐、两新表在、旧行 6 个原字段逐等且
      `policy_version` 未回写、新字段读为 `None`/`'[]'`/`0`；重复打开 3 次 marker 恰 1 行；
      `_migrate` 事务进入次数 +1。
      连带口径：`schema_version()` 的 `ORDER BY applied_at DESC` 补 `rowid DESC` 定尾——
      `FixedClock` 下两条迁移行同毫秒，原写法无法确定返回哪条，现返回最近插入的
      `p5-control-v1`。P4 的 3 处表集合期望按 10 表更新（`test_orchestration_store.py`：
      `TABLES` 加两表名、`test_all_eight_tables_exist_with_schema_version` →
      `test_all_tables_exist_with_schema_version` 改断言"版本行含 `0.1.0` 且
      `schema_version()` 为最新迁移"、L5 用例 docstring 去掉"8 张"字样）；P4 账本 B2
      的"八张表齐备"是 P4 收口时的历史事实，不改写。
      全量 `pytest -q` = `440 passed, 1 skipped`，`store.py` 覆盖率 100%（161 stmts，0 miss）；
      `mypy` / `ruff check` / `ruff format --check`（90 files）全绿；
      L1 `git diff 234f620..HEAD -- stableagent/core/` = 0 行。
- [x] **1.3** 模型：`Action`/`Resource`/`Admission`/`Decision`/`ApprovalRequest` +
      `resource_digest` 纯函数（规范化 JSON 排序哈希）。验证：digest 稳定性单测。
      证据（2026-11-04，Task 3，提交 `0d92da9`）：`control/model.py` = `Action(str, Enum)` 13 成员
      （顺序逐字等于规格 2.1 的列举，`ENABLE_AUTO_MERGE` 只作点名拒绝的载体）+ `Resource`
      （`kind` + 五个可选槽位 + `extra`）+ `Admission` + `Decision` + `ApprovalStatus` 4 值 +
      `ApprovalRequest`（与 `approval_requests` 十列一一对应，ID 与毫秒时间注入）+
      `resource_digest`（`sha256(json.dumps({"action","resource"}, sort_keys=True,
      ensure_ascii=False))`）。
      `tests/unit/test_control_model.py` = `5 passed`：extra 键乱序 → digest 相等且等于文档化口径
      （64 hex）；改 action / 改 path / 改 extra 值 / 改 `kind` 各得不同 digest；
      13 成员顺序钉死；缺 `ticket_id` 或缺 `resource_digest` → `ValidationError`；
      `Admission`/`Decision` 字段形状。`control/model.py` 覆盖率 100%（58 stmts，0 miss）。
- [x] **1.4** Mandate 读写：`p5-v1` 新字段全量落库与还原；`p4-local-v1` 旧行缺省读取。
      验证：往返单测。来源：ADR-05 决策 2。
      证据（2026-11-04，Task 4，提交 `9207146`）：`Mandate` 的 `origin_kind` 扩为
      `Literal["local", "github"]`（默认 `"local"`）+ 11 个新字段全带默认值；
      `insert_mandate` INSERT 参数 6 → 17 列，`_row_to_mandate` 读满 17 列。
      写入口径：四个列表列经 `_mandate_list` 规范化为**升序 + 规范化 JSON**（`sorted` 后再
      `json.dumps(sort_keys=True, ensure_ascii=False)`）——授权集来自 `frozenset`，不排序则同一
      Mandate 两次落库字节不同、审计比对失去意义；`test_mandate_list_columns_are_sorted_on_write`
      同时钉死库内原文 `actions == '["a", "b"]'`。`requires_approval` 写 `int`、读回 `bool`。
      `tests/unit/test_mandate_extension.py` = `4 passed`（p5 满配往返逐字段等、p4 旧 6 字段往返、
      旧库升级行经模型读取、列表列排序口径）；`tests/unit/test_orchestration_store.py` +
      `test_control_migration.py` 回归 `27 passed`；全量 `449 passed, 1 skipped`，
      `store.py` 覆盖率 100%（163 stmts，0 miss）；A1 同批勾选。
- [x] **1.5** `StaticPolicy`：单仓库 + actor allowlist + 明确 mention + 预定义 action 集合 +
      默认拒绝集（任意 shell、全网访问、未列入的 MCP server/tool 均拒）。验证：判定矩阵单测。
      证据（2026-11-04，Task 5，提交 `4d66db5`）：`control/policy.py` = `StaticPolicyConfig`
      （集合字段默认空 = deny-first）+ `StaticPolicy(config, *, clock, ids)` 的四方法
      `admit_explicit`（门序 actor → mention → repository，失败也带 requester/stable_id/
      `request_digest`，供审计追溯）/`create_mandate`（唯一 `p5-v1` 构造点）/`authorize`/
      `revalidate`（`REVALIDATE_TIMINGS` 六时机全集，未知时机抛 `RevalidationFailedError`）。
      `tests/unit/test_static_policy.py` = `9 passed`，`control/policy.py` 覆盖率 100%
      （97 stmts，0 miss）；全量 `458 passed, 1 skipped`；`mypy`（39 files）/`ruff check`/
      `ruff format --check`（95 files）全绿；L1 core diff 仍 0 行。
      三条实现口径（计划留白处，均已在测试里钉死）：
      ① `create_mandate` 的 `allowed_*` 缺省为**空集**而非继承 config——调用方漏传一个清单，
      产出的是"更少授权"，不是"operator 的全部授权"；同时校验请求集 ⊆ config
      （`action_not_grantable`/`command_outside_config`/`mcp_outside_config`/
      `network_profile_undefined`），保证 Mandate 恒为 config 子集。
      ② `ENABLE_AUTO_MERGE` 三道拒：config 构造期（`grantable_must_exclude_auto_merge`）、
      `create_mandate`（点名要就抛 `auto_merge_not_grantable`，不静默截断成"看起来满足了"）、
      `authorize`（即使 Mandate 被白盒改出该 action 仍拒）。计划写"剔除并拒绝"，实现取"拒绝优先、
      剔除兜底"：产出路径上任何一次调用都拿不到含 auto merge 的 Mandate。
      ③ 结构性拒绝的构造期一道走 `BaseModel.__init__` 覆写：pydantic v2 会把
      `model_validator`/`model_post_init` 抛出的 `ValueError` 子类包成 `ValidationError`
      （实测），拿不到 `AdmissionRejectedError`；`model_validate` 一类旁路 `__init__` 的路径
      由 ②的后两道兜住。
- [x] **1.6** 审计落库：admit/authorize/revalidate/approve/deny 每决策一行，含
      `policy_version` 快照。验证：决策序列 → 行数与字段断言（A3）。
      证据（2026-11-04，Task 6，提交 `e4be18a`）：`tests/unit/test_authorization_audit.py`
      4 用例全绿（`462 passed, 1 skipped`，总覆盖 99.1%，`store.py` 与 `control/*.py` 全 100%）。
      三处实现取向记为偏差：
      ① 快照来源是**决策当刻生效的策略版本**（`StaticPolicy.policy_version`），不是被判定
      Mandate 的自述版本——用例让 p5-v1 策略判定一份自称 `p4-local-v1` 的 Mandate，行里仍记
      `p5-v1`，否则版本漂移在审计上会表现为"两行版本一致"的假象。为此 `StaticPolicyConfig`
      新增 `policy_version`（默认 `p5-v1`，Task 5 默认路径逐字等价），`create_mandate` 打版与
      `revalidate` 漂移判定同读该值：版本标签必须由配置侧携带，否则计划用例里"两个不同 config
      实例"这一维度在实现中根本不存在。
      ② `authorization_audit` 的 `action` 槽按 kind 取值：authorize/approve 存动作名，
      revalidate 存 `REVALIDATE_TIMINGS` 之一，admit 留空。11 列契约由 Task 2 的
      `test_new_tables_have_the_authorization_columns` 按 ADR-05 决策 3 钉死，六时机的可区分性
      不加列解决。
      ③ Store 侧六个仓储方法收**列名 + 原语**、读侧返回行字典（不返回 `control.model` 类型）：
      `orchestration` 不得 import `control`（L2 方向锁），枚举取值与词汇表校验归 control 调用方。
      `AuditWriter` 四方法各写恰好 1 条 INSERT 且**不自开事务**（单语句由 sqlite3 自动提交保证
      原子，调用方已在 `store.transaction()` 内时不会重入——L3 的写入半边），并追加
      `*, ticket_id=None, assignment_id=None` 可选关键字（`decided` 从 approval 取），否则
      `list_audit(ticket_id=)` 是死过滤器。第 4 用例为计划 3 例之外的追加，覆盖 Task 7 依赖的
      审批行读写与过滤口径。
- [x] **1.7** `ApprovalService.decide()`：approved 绑定 `(action, resource_digest)`、参数变化
      即失效、denied → blocked/failed 不可绕过、Ticket 取消同事务撤 pending。验证：B1~B4 用例。
      证据（2026-11-04，Task 7，提交 `31cb377`）：`control/approval.py` 的
      `ApprovalService`/`ApprovalGate`/`GuardedTicketOps` 三件套，两个测试文件 10 用例全绿
      （`472 passed, 1 skipped`，总覆盖 99.15%，`approval.py` 94 stmts 100%）。
      ① B1/B3/B4 关闭；B2 只到 Gate 半边（denied 在同一 `(ticket, action)` 上粘连、重新申请
      换资源都不自愈），"使 Assignment `blocked|failed`"要有 Assignment 可管，归 M2/M3 接线。
      ② 高危集 `HIGH_RISK_ACTIONS` 由 M1 写死为 push_branch / create_pull_request / comment_issue
      / comment_pull_request / request_review（计划只写"高危集"未点名成员）：口径取"对外部系统
      可见或难以撤销的写入"，与 ADR-05 决策 4 的 `external_write` 复检时机同一批动作；它是 Gate 的
      第二道，`mandate.requires_approval=False` 关不掉。
      ③ 计划签名两处调整：`ApprovalGate(policy, approvals, audit)` 去掉没有消费点的 `store`/`clock`
      （审批行读取已由 `ApprovalService` 承担，时间口径归 `StaticPolicy` 的时钟；留着正是 ADR-05
      批评 `permission_hook` 那种"声明字段而非执行边界"）；`GuardedTicketOps(store, approvals, *,
      clock, ids)` 用实例方法代替计划里的静态 `cancel_ticket(store, service_ids, …)`，因为
      `ended_at`/`due_at`/撤销售 `decided_at` 都要 `now_ms()`，静态签名没有时钟来源。
      ④ `check(mandate, action, resource, *, ticket_id=None)` 追加票面关键字：审批行按 ticket
      归属（`approval_requests` 无 mandate_id 列，列集由 ADR-05 决策 3 钉死），Gate 拿不到票面就
      无法定位批准行，此时宁可 deny `approval_context_missing`，也不猜归属。
      ⑤ 撤销售写 `decided_at`=撤销时刻、`decided_by` 留空：`decided_*` 是终态元数据而非"人工
      裁决"专用字段；`decide()` 本身不写 approve 审计行（计划给 `ApprovalService` 的构造参数里没有
      audit），`AuditWriter.decided` 的调用点归接线方，形状已由 Task 6 用例证明。
      ⑥ P4 `OrchestrationService.cancel_ticket` 一字不动（约束 1）：
      `git diff 234f620..HEAD -- stableagent/orchestration/service.py | wc -l` = 0；守卫式取消自己
      声明未终态 Assignment 终态集（那份常量是 service 私有的），并用同一 `(kind, cancel:{id})` 幂等
      键与同一 payload 形状证明与 P4 同形。
- [x] **1.8** L2 边界测试扩展：三新包 × 四旧包 AST 方向矩阵。验证：故意 import 的红测 + 常驻。
      证据（2026-11-04，Task 8，无生产代码改动）：`tests/unit/test_control_boundaries.py` 追加 3 用例（AST 扫描器 `_imports_of`/`_mandate_calls`/`_pins_p5_version`/`_imports_version_constant`/`_version_constant_names` 全在本文件，扫 `stableagent/` 生产包）——① L2 反向（旧包零 import 三新包，语料取 ROOT 下全部非 P5 目录并断言覆盖账本点名的五包）；② L2 正向（`control/*.py` 零 import `execution/integrations`）；③ A2/L4 扫描半边：`p5-v1` 字面量的模块常量赋值全库唯一 holder = `control/policy.py:P5_POLICY_VERSION`、该常量的 import 方只允许 control 包、control 包外把 `policy_version` 静态钉成字面量或该常量的 `Mandate(...)` 构造点为零，末尾一条「A2 构造点必须被看见」断言防扫描器空转。红因实测：先跑真实语料 6 例全绿（计划写的是「第三例红——若字面量断言写法未收口」，实测已收口：Task 5 落 `P5_POLICY_VERSION`、Task 6 把默认值接进 `StaticPolicyConfig.policy_version`，生产代码里 `p5-v1` 只剩一处字面量），再按账本要求**人为构造越界样例进红**：三份一次性探针分别打到 ①（`orchestration/_p5_probe.py` 里 `LEAK_VERSION = "p5-v1"`）、②＋L2 反向（同文件 import `stableagent.control.policy`）、③（同文件 `Mandate(..., policy_version="p5-v1")` → `[('stableagent/orchestration/_p5_probe.py', 7)]`），外加 `control/_probe_upstream.py` 打到 ②方向腿——四腿各有独立红，探针已删。步骤 3「收敛为单一模块常量」本轮零改动（已在位），常量名沿用 Task 5 的 `P5_POLICY_VERSION`而非计划建议的 `POLICY_VERSION`：带层前缀的导入名在调用点自述来源，且第 1 节 1.5/1.6 的证据已按此名记录。绿：`pytest -q tests/unit/test_control_boundaries.py tests/unit/test_orchestration_boundaries.py` = `19 passed`（P4 旧矩阵与新矩阵并绿）；全量 `475 passed, 1 skipped`、总覆盖 99.15%、`stableagent/control/*` 六文件仍 100%；`mypy` Success（41 files）、`ruff check` All checks passed、`ruff format --check` 100 files already formatted；L1 `git diff 234f620..HEAD -- stableagent/core/` = 0 行、约束 1 `service.py` = 0 行。
- [x] **1.9** **M1 出口**：`pytest -q` / `mypy` / `ruff check` / `ruff format --check` /
      L1 diff 0 行；账本 M1 步骤与 A、B 组证据回填。
      证据（2026-11-04，Task 9）：出口六条命令实测——`.venv/bin/pytest -q` = `475 passed, 1 skipped`
      （唯一 skip 是 P2 遗留的 `tests/unit/test_checkpoint_storage.py:161` 真实 Redis 用例，`STABLEAGENT_REDIS_URL`
      未设置即跳过——L8 的 M1 半边：M1 新增 42 用例全部离线可跑）；总覆盖 99.15%（2708 stmts / 23 miss），`stableagent/control/*`
      六文件 100%（`__init__` 3、`errors` 32、`model` 64、`policy` 101、`audit` 25、`approval` 94），≥90% 门槛全线通过；
      `.venv/bin/mypy` = `Success: no issues found in 41 source files`；`ruff check stableagent tests examples benchmarks` =
      `All checks passed!`；`ruff format --check` = `100 files already formatted`；L1 `git diff 234f620..HEAD --stat -- stableagent/core/ | wc -l`
      = 0，约束 1 的 `orchestration/service.py` 同为 0。M1 共 18 个提交（`f640298`…`e5af6ea`，含计划与 ADR 两笔），新增 8 个测试
      文件 42 用例（control 侧七文件 38 例 + `test_control_migration.py` 4 例）。第 0 节 L2/L3/L4/L5 已写入出口复核，
      按 P4 口径不提前勾选；第 2 节 A2 勾选，A4/B2 记归属与偏差——**DoD 7/46（计划写 8，见 B2 偏差）**。

### M2：隔离执行（规格 WP-5.2 + WP-5.3；ADR-05 决策 5/6/7）

- [x] **2.1** `execution/` 包骨架 + `GitWorktreeManager`：参数数组调用 git、路径
      `Path.resolve()` 锁 root。验证：路径逃逸拒绝用例（C4）。
      收口（2026-11-05，M2 Task 3）：骨架半边见 Task 1（`__init__/errors/paths` + 方向锁），
      `GitWorktreeManager` 半边由 Task 3 补齐——git 一律经注入的 `Git` 协议、
      `SubprocessGit` 用 argv 数组 + `shell=False` + `timeout_s` + 输出上限，命令首元固定 `git`；
      路径侧 `prepare` 只把 `confine(root, workspace_name(ticket))` 的结果当目录用，克隆侧
      `Path(repository).resolve()`。C4 四例 + C3 十例 + 传输层两例同文件在绿。
      进度（2026-11-05，M2 Task 1 骨架 + C4 半边）：包落位 `__init__.py`
      （`EXECUTION_SCHEMA_VERSION = "0.1.0"`，不顶层重导出）、`errors.py`（`ExecutionError(ValueError)`
      + `path_escape`/`worktree_reuse`/`command_denied`/`cleanup_unconfirmed` 四个稳定码）、
      `paths.py`（`confine()`，C4 已收口并勾选）；`tests/unit/test_execution_boundaries.py` 把方向锁钉死
      （execution 只可 import 自身/control/orchestration/core，integrations 零引用）。参数数组调用 git 的
      `GitWorktreeManager` 本体属 Task 3（步骤 2.3），本步届时整体勾选。
- [x] **2.2** 分支/worktree 命名纯函数：`sa-<ticket短id>-<slug>`，slug 仅 `[a-z0-9-]`；
      request 文本永不直通。验证：命名往返 + 注入文本用例（C5）。
      证据（2026-11-05，M2 Task 2）：`execution/naming.py`（22 语句、覆盖 100%）四个出口
      `ticket_short_id`/`slugify`/`workspace_name`/`is_workspace_name` + `WORKSPACE_RE`
      （`^sa-[0-9a-f]{8}-[a-z0-9-]{1,40}$`）；`workspace_name` 的签名只吃 `Ticket`，读的字段只有
      `id` 与 `title`，**没有** request/Issue 原文参数位——直通在类型上就写不出来。
      `tests/unit/test_worktree.py` 的 C5 段 13 例（+13：484 → **497 passed, 1 skipped**，总覆盖
      99.17%）：8 条注入语料逐条 `re.fullmatch(WORKSPACE_RE)` 且不含 `/`、`;`、空格、反引号、
      `|`、`&`、`$`；`slugify`
      六例钉过滤口径（`"  a___b  "`→`"a-b"`、`"--x--"`→`"x"`、纯 CJK 与空串→`"ticket"`、
      `"a"*300`→40 个 `a`）；确定性 + 上限 `len("sa-01234567-") + 40`；
      `test_workspace_name_never_reads_the_task_or_request_text` 把 `请建分支 ../../etc/cron.d/x ;
      rm -rf /` 放进 `ticket.task` 后名字逐字节不变。门禁：`mypy` Success（45 source files，+1）、
      `ruff check` All checks passed、`ruff format --check` 106 files already formatted、L1 与约束 1
      diff 均 0 行。计划偏差两条见 §6 Task 2。
- [x] **2.3** 复用判定：仓库 remote / Ticket metadata / base ref / ownership 四要素全等复用，
      不一致拒绝并保留诊断。验证：临时 git 仓库逐要素破坏测试（C3）。
      证据（2026-11-05，M2 Task 3）：`execution/worktree.py`（67 语句、覆盖 100%）——
      `prepare(ticket, repository=本地克隆, base_ref=...)` 先读 `git remote get-url origin`
      （脱敏后即"仓库身份"），再 `confine` 出路径；目录已存在时逐项比四要素，全等才返回复用的
      `WorktreeHandle`（用例断言 `worktree add` 调用数仍为 1、ownership 的 `created_at` 未被改写、
      最后一次 git 调用只是 `remote get-url`）。破坏任一要素都得 `WorktreeReuseError`
      （`execution.worktree_reuse`）：`remote`（改克隆 origin）、`ticket_id`（ownership 指向别的
      Ticket）、`base_ref`（记录 main 要求 dev）、`ownership`（坏 JSON / 删文件 / 分支标记改成
      `main`）——六种破坏各一条参数化用例，`diagnostics` 固定六键
      （`element`/`reason`/`path`/`ownership_file`/`recorded`/`requested`，recorded 与 requested
      同键集），且断言 worktree 目录**仍在**（拒绝不等于清理）。真 git 装置：`_make_clone` 用
      参数数组 + 最小环境建临时仓库，全程离线（L8）。口径决定两条（记进 §6 Task 3）：ownership
      缺失/不可解析时**先按要素④拒绝**，不拿"remote 不符"顶罪；读不到 origin 直接 `GitError`
      且不创建 root（失败不留无主 ownership，也不留半成品 worktree）。
- [x] **2.4** `PREPARE_WORKTREE` EffectKind + outbox 驱动 prepare：重复 effect 幂等命中同一
      worktree。验证：崩溃注入重放（C2）。
      证据（2026-11-05，M2 Task 4）：`EffectKind` 加 `PREPARE_WORKTREE = "prepare_worktree"`
      （`model.py` 唯一改动，`store.py` 未碰——`kind` 是无 CHECK 的 TEXT，加成员是非破坏性扩展；
      `service.py` 一字未动，约束 1 diff 0）。`execution/effects.py`（16 语句、覆盖 100%）：
      `prepare_idempotency_key(assignment_id)` = `prepare:{assignment_id}`（**不带 attempt**：一个
      assignment 只有一个现场，重试换键就等于允许建第二个），`prepare_worktree_handler(manager, store)`
      按 P4 口径从库里取 Ticket（`scheduler._gate_launch` 同款读法）后调同步 `manager.prepare`。
      幂等两层都有用例：账上 `test_prepare_effect_key_is_scoped_to_the_assignment`（同键重复入队 →
      仍是 1 行、终态不复活）；现场 `test_prepare_effect_is_idempotent_on_replay`——真 `Store` +
      真临时仓库，prepare 的 git 副作用已发生而 effect 停在 `running`，随后 `store.close()` 换一条
      **全新连接**、`reclaim_running_effects` 退回 pending、新 `EffectDispatcher` 重放一次成功，
      断言 `worktree list` 仍是主目录 + 我们这一条、`branch --list` 计数 1、ownership 的
      `created_at` 仍是崩溃前那次、effect 行数 1 且 `attempts == 1`（reclaim 还掉了崩前的 claim）。
      重放走新连接是刻意的：C2 的"崩溃"要证的是事实源在库里不在内存里（同 B4 口径）。
- [x] **2.5** 生命周期：Ticket 结束默认保留；清理需显式调用 + 人工确认参数，无自动删除。
      验证：结束后目录仍在 + cleanup 需 confirm 参数。
      证据（2026-11-05，M2 Task 5）：`execution/worktree.py`（67 → 82 语句、覆盖仍 100%）加
      `cleanup(handle, *, confirm=False, delete_branch=False)`：`confirm=False` 抛
      `CleanupNotConfirmedError`（`execution.cleanup_unconfirmed`），消息带可执行的下一步
      （`manager.cleanup(handle, confirm=True)`，连分支一起删再加 `delete_branch=True`），
      `delete_branch=True` 不能代替确认（同一用例的第二道断言）。"无自动删除"按三种形态一起证：
      manager 无 `__del__`、无定时器、也不接在 Ticket 状态机上——`test_worktree_survives_ticket_end`
      用真 `Store` + 真 `OrchestrationService` 把 Ticket 推到 `COMPLETED`（OPEN→QUEUED→RUNNING→
      READY→COMPLETED 全走状态机），随后断言目录、ownership 文件、受控分支、`git worktree list`
      四项原样，且录制到的 git 调用里删除类 argv 为空（`_removal_calls` 只认 `worktree remove` 与
      `branch -D` 两种指纹）。
      口径决定（不传 `--force`）：ownership 标记是 prepare 自己写进工作区的唯一未跟踪文件，
      `git worktree remove` 会因为它而拒绝（实测 rc=128），所以 `cleanup` 先按字节读出标记、摘掉它、
      再 plain remove；remove 失败就意味着工作区里有**别人的**未提交内容，此时把标记按字节写回再抛
      `GitError`——未提交内容由 git 的脏检查守着，一次失败的清理也不会损坏复用四要素之④
      （`test_cleanup_refuses_uncommitted_work_and_keeps_the_marker`）。规格只要求"清理需显式调用 +
      人工确认"，没要求系统提供丢弃未提交内容的入口，故本轮不伪造 `discard`/`--force` 参数。
      接口扩展一处（计划未列，见 §6 Task 5）：`WorktreeHandle` 加 `clone` 字段——`worktree remove`
      与 `branch -D` 必须在一个仍然存在的 worktree 里执行，被删目录本身不行（实测 cwd 取主克隆）。
      另两条补测：`test_cleanup_deletes_branch_only_when_asked`（argv 序列固定 remove→`branch -D`、
      两次 cwd 都是主克隆）、`test_cleanup_of_a_worktree_removed_by_hand_still_deletes_the_branch`
      （目录已被人工删掉时 git 侧登记仍能清掉并按要求删分支）。
      绿：目标文件 **38 passed**（+6）、全量 **518 passed, 1 skipped**、总覆盖 99.14%、
      `execution/worktree.py` 与 `execution/errors.py` 均 100%；`mypy` Success（48 source files，
      本轮零新文件）、`ruff check` All checks passed、`ruff format --check` 109 files already formatted；
      L1 `git diff 234f620..HEAD -- stableagent/core/` = 0 行，约束 1 与整个 `orchestration/` 本轮 0 行。
- [ ] **2.6** `CommandRunner`：命令名精确 allowlist、cwd 锁 worktree、超时、输出上限、
      截断与脱敏入 Report。验证：C7 全断言。
      进度（2026-11-05，M2 Task 7）：**前四项 + 脱敏**五项齐（C7 已勾，见第 2 节）——
      `execution/command.py`（89 语句、覆盖 100%）的 `CommandRunner.run(request)` 按
      ①`confine(worktree_root, request.cwd)` ②空 argv ③`argv[0]` 逐字节命中 allowlist 三段短路，
      拒绝**不抛异常且不 spawn**（`denied_reason` 是稳定码 `cwd_escape`/`empty_argv`/
      `command_not_allowed`，Task 9 再加 `shell_syntax`），放行才 `subprocess.run(argv 数组,
      cwd=收口后路径, env=build_child_env(...), timeout, shell=False)`，超时给
      `timed_out=True` + `exit_code is None` 并保住已产出的部分输出，`max_output_bytes` 是
      **含尾标的总预算**且先脱敏后截断。
      剩最后半句"**截断与脱敏入 Report**"未证：`CommandResult` 现在没有任何生产调用方——落 Report
      的文本形状属 Task 8（`CommandResult.summary()`），真正写进 `Assignment Report` 的接线属
      M2 Task 12 / M3。本步不提前勾（C7 的验证栏只要求五项有测试，故 C7 勾、2.6 不勾）。
      计划外三处（详见 §6 Task 7）：`CommandRequest` 加 `context` 字段（`build_child_env` 需要
      ticket/assignment/仓库/base ref，而 `worktree_root` 是全 Ticket 共用的根，身份只能随请求走）、
      构造函数加 `clock`（`started_at`/`ended_at` 的来源，房子口径同 `Store`/`GitWorktreeManager`）、
      拒绝码表多一个 `empty_argv`。
- [x] **2.7** `environment.py` 变量白名单（TICKET_ID/ASSIGNMENT_ID/WORKTREE/仓库信息），
      宿主其余环境不注入。验证：子进程可见环境断言（C8、L7）。
      证据（2026-11-05，M2 Task 6）：`execution/environment.py`（8 语句、覆盖 100%）的
      `build_child_env(*, ticket_id, assignment_id, worktree, repository, base_ref, host=None)`
      产出恰好七键——五个 `STABLEAGENT_*` + `PATH` + `LANG`。取向是"没有第四个来源"而不是
      "过滤已知密钥名"：`source = os.environ if host is None else host`，只读它的 `PATH` 一项，
      所以宿主新增什么密钥都不需要改这里。`test_command_runner.py` 的 C8 段六例——
      ① `test_child_env_is_an_allowlist`：13 键宿主（`GITHUB_TOKEN`/`GH_TOKEN`/`OPENAI_API_KEY`/
      `AWS_SECRET_ACCESS_KEY`/`AWS_ACCESS_KEY_ID`/`SSH_AUTH_SOCK`/`PYTHONPATH`/`HOME`/
      `STABLEAGENT_DB_PATH`/`ENV`/`PAGER` + `PATH`/`LANG`）进来，键集合仍等于白名单，五个密钥字面量
      不出现在任何值里，`PATH` 取宿主值、`LANG` 固定 `C.UTF-8` 不吃宿主的 `zh_CN.GBK`；
      ② `test_child_env_leaves_the_database_and_host_home_behind`：值里找不到 `orchestration.db`、
      `/home/agent`、`/run/user`、`/opt/stableagent`——规格 2.3 R5 的"只给 worktree 路径与非敏感变量"
      连 P4 库位置和宿主代码路径一起挡住；③ `test_child_env_has_no_secrets_from_arguments`
      整份环境写死比对（五个参数按原值透传，无第三来源）；④ `host=None` 走真 `os.environ`，
      断言连必然存在的 `HOME` 都不带走；⑤ 宿主没有 `PATH` 时留空——不硬编码 `/usr/local/bin`
      当默认值（硬编码等于替宿主决定哪些二进制可信）；⑥
      `test_child_process_sees_only_the_allowlist` 补的是步骤 2.7 验证栏里"**子进程可见**环境断言"
      那句话——真起一个 `sys.executable -c` 进程让它自己打印 `os.environ`，断言
      `set(seen) >= CHILD_KEYS` 且 `set(seen) - CHILD_KEYS` 只允许是 `OS_INJECTED_KEYS`。
      实测有一处不能假装：macOS 的 libsystem 会在 exec 时注入 `__CF_USER_TEXT_ENCODING`
      （与我们传的 env 无关，Linux CI 上没有），所以"孩子没有第四个来源"这句必须带着这份名单说。
      与 `git_env()` 的口径差一条并写进 docstring：git 侧 `LANG=C`（只吐按行解析的 plumbing 输出），
      子进程侧 `LANG=C.UTF-8`（Agent 命令要吐中文标题与 diff，Report 需统一按 UTF-8 解释与脱敏）；
      两侧共同点是都不注入 `HOME`。计划偏差与补测见 §6 Task 6。
- [ ] **2.8** `LocalRestrictedRunner` + 叙事纪律：类文档、拒绝消息、Report summary 禁
      "sandbox" 一词。验证：文案断言测试（D1）。
- [ ] **2.9** shell 拒绝：`shell=True`、shell 语法字符（`;|&$\``）一律拒绝且审计。
      验证：注入语料表（C6）。
- [ ] **2.10** `DockerRunner` 参数装配断言：`--network none`、非特权、无 socket、CPU/内存/pid/
      超时、仅挂本 worktree、镜像可配置 + digest pin 文档。验证：不启动容器、只断言 argv（D2）。
- [ ] **2.11** Docker 测试条件：`docker` marker + `STABLEAGENT_DOCKER=1` 双条件，默认跳过且
      原因可见。验证：无 Docker 环境全量绿（D3、L8）。
- [ ] **2.12** **M2 出口**：全量门禁 + 并行 Ticket 目录互不污染集成用例（C1）+ 账本回填。

### M3：MCP 与 GitHub 闭环（规格 WP-5.4 + WP-5.5；ADR-05 决策 4/8/9）

- [ ] **3.1** `McpTransport`：参数数组启动、最小环境、请求 ID、超时、关闭。
      验证：fake transport 生命周期用例。
- [ ] **3.2** `McpSession`：initialize/tools/list/tools/call + JSON-RPC id 与错误校验。
      验证：E1 六类（error/超时/进程退出/schema 错误）。
- [ ] **3.3** `McpToolAdapter` → 既有 `Tool`；输出走 ToolResult→Evidence→ContextEngine，
      超长受 P3 压缩预算。验证：E4 预算断言。
- [ ] **3.4** 发现 ≠ 授权：Agent 可见集 = `tools/list ∩ Mandate.allowed_mcp`；未列入者不可见、
      不可启动调用。验证：E3 + 离线 stdio fixture（E2）。
- [ ] **3.5** `ExplicitGitHubRequest` 模型 + 准入四道门（allowlist→mention→单仓库→重复检测）；
      同稳定 ID 返回同 Ticket 或明确 duplicate。验证：F1 + 每门审计行。
- [ ] **3.6** `control` 入口事务：Mandate+Ticket+初始 Input+Assignment+launch effect 同一
      `Store.transaction()` 原子写。验证：F2 + 事务内禁 await 扫描（L3）。
- [ ] **3.7** `RepositoryWorkerExecutor`：launch/recover/deliver 前 revalidate，失败 park 不启动；
      遇 `p4-local-v1` Mandate 直接拒绝外部执行面。验证：A4 时机矩阵 + 拒绝用例。
- [ ] **3.8** `PolicyToolFilter` 接 P3 `tool_selector` 接缝：拒绝调用有审计。
      验证：L5 + 幻觉工具名用例。
- [ ] **3.9** `GitHubClient` 协议 + fake 实现；错误可重试/不可重试/需人工三分类，状态码保留。
      验证：F4 分类表驱动测试。
- [ ] **3.10** comment / create PR / request review 三个 effect handler：执行前 revalidate +
      approval 检查；idempotency key = 外部稳定 id + action/resource digest。验证：F3。
- [ ] **3.11** 恢复不重复写：重启/超时/重复 effect 下 PR、评论、push 各仅一次。
      验证：F5 集成用例（fake client 计调用次数）。
- [ ] **3.12** **M3 出口**：全量门禁 + L6 类型检查（枚举无 merge 成员）+ 账本回填。

### M4：端到端与收口（规格 WP-5.6；ADR-05"测试与证据"）

- [ ] **4.1** `examples/05_guarded_github_task.py`：本地 git fixture + fake GitHub 走通
      请求→授权→worktree→修改→PR effect，全离线。验证：示例即测试（G1）。
- [ ] **4.2** 拒绝示例五连：非 allowlist、无 mention、越权命令、无审批 PR 写入、auto merge
      ——均结构化拒绝 + 审计 + 零外部副作用。验证：G2。
- [ ] **4.3** CI FOCUS 扩容：`control/policy.py`、`control/approval.py`、
      `execution/worktree.py`、`execution/command.py`、`execution/sandbox.py`、
      `integrations/github.py`、`integrations/mcp.py` 进心脏闸门 + 契约测试同步。
      验证：本地同源复现 BAD/MISSING 均空。
- [ ] **4.4** 本地 Python 3.10 / 3.11 / 3.12 等价矩阵（沿用 `.matrix/`）。
- [ ] **4.5** ADR-05"测试与证据"实测回填 + 安全说明定稿（不夸大本地受限模式，G4）。
- [ ] **4.6** README / CHANGELOG / Roadmap / docs 索引 / 规格退出清单同步（状态翻转时机沿用
      P4：远端 CI 绿前不写"收口"）。
- [ ] **4.7** **M4 出口**：维护者授权 → 合并推送 `main` → 远端 CI 3.10/3.12 双绿 →
      run ID 回填账本（H3）。

### 1.4 里程碑与 DoD 归属

| 里程碑 | 心脏边界 | DoD 分组 |
|---|---|---|
| M1 | L3、L4、L5（授权侧） | A1~A3、A5、B1~B4 |
| M2 | L7、L8（部分） | C1~C8、D1~D3、A4（worktree 时机接线） |
| M3 | L5、L6、L7（凭据侧） | E1~E4、F1~F6、A4（launch/recover/retry/push/MCP 接线） |
| M4 | L1、L2、L8（收口） | G1~G4、H1~H4 |

> 归属修正（2026-11-03，M1 任务级计划起草时定）：A4 的六个 revalidate **时机接线**分布在
> M2（prepare_worktree）与 M3（其余五处），M1 只交付 `revalidate` 判定与审计本身。

## 2. DoD（A~H：38 项；与 L1~L8 合计 46）

### A 授权与 Mandate 持久化（M1）

- [x] **A1** 旧 P4 库升级后 `p4-local-v1` Mandate 行缺省可读、语义不变、不回写。
      证据（2026-11-04，Task 2 升级半边 + Task 4 往返半边，提交 `0e92dbd`/`9207146`）：
      `test_legacy_p4_database_upgrades_without_touching_rows` 断言旧行 6 个原字段逐等、
      `policy_version` 仍为 `p4-local-v1`、11 个新列取默认值；
      `test_upgraded_legacy_row_reads_through_the_model_with_defaults` 用同一旧库文件走
      `Store.get_mandate`，读回的对象与"只给 6 个原字段构造的 `Mandate`"逐字段相等
      （`actions=[]`、`requires_approval=False`、五个可空字段 `None`）——旧行不回写、语义不变；
      `test_p4_local_mandate_roundtrip_unchanged` 覆盖新建的本地 Mandate 路径。
      M1 出口后维护者确认（2026-11-04，M2 分支首笔）：`Mandate` 收紧 `extra="forbid"`——
      授权字段拼错（如 `allowed_command=`）从"静默丢字段"变为 `ValidationError`，`test_mandate_rejects_unknown_fields`
      钉死；P4 其余记录保持宽松，本行只记 Mandate 一处的例外与理由（静默丢字段等于悄悄改授权面）。
- [x] **A2** 只有 `control.policy` 能构造 `p5-v1` Mandate（构造入口扫描 + 行为用例）。
      进度（2026-11-04，Task 4 字段形状半边）：`p5-v1` 的 17 字段形状已由满配往返用例钉死；
      "唯一构造入口 = `control.policy.create_mandate`"的行为用例属 Task 5，构造入口扫描属 Task 8。
      进度（2026-11-04，Task 8 扫描半边）：三处静态钉版形态（字面量 holder / 常量 import 方 / 
      control 包外的 `Mandate(policy_version=p5-v1)` 构造点）各一道 AST 断言，越界样例注入实测能红；
      扫描范围是生产包 `stableagent/`，白盒用例（`test_mandate_extension.py` 直接构造 p5 Mandate 验往返）不计入越界
      ——账本口径是"生产代码里外部文本产不出授权"。
      证据（2026-11-04，Task 9 出口）：字段形状（Task 4）+ 行为用例（Task 5）+ 构造入口扫描（Task 8）三半边齐，
      M1 内可证的部分全部绿，故本条在 M1 出口勾选；M2/M3 若新增 `p5-v1` 钉版点，扫描门会先红。
- [x] **A3** admit/authorize/revalidate/approve/deny 全部结构化落审计表，含 policy_version。
      证据（2026-11-04，Task 6）：`AuditWriter.admitted/authorized/revalidated/decided` 各写
      1 行 `authorization_audit`，`kind ∈ {admit, authorize, revalidate, approve}`、
      `decision ∈ {allow, deny}`（deny 是 decision 取值，不是第五个 kind），11 列齐全且
      `created_at` 由注入时钟给出；`reason` 为稳定码——`test_audit_reason_is_stable_code_not_free_text`
      对四行 deny 逐一 `^[a-z][a-z0-9_]*$` 全匹配（`actor_not_allowlisted`/`not_granted`/
      `command_not_allowed`/`mandate_expired`），并断言请求正文里的自由文本不出现在表里（L4）；
      `policy_version` 写决策当刻的策略版本快照——`test_audit_snapshots_policy_version`：两个
      config 实例先后决策 → 两行版本不同，且后续换策略只新增行、不改已有行的快照。
      本条只证明落库面与形状：真实调用点（六时机 + 审批门）在 M2/M3 接线，见对照表第 6 行
      "审计先于接线"的口径。
- [ ] **A4** 六个 revalidate 时机逐个先于动作执行，时机清单以测试矩阵固化。
      归属修正（2026-11-04，Task 9）：M1 交出的是判定与审计的形状——`REVALIDATE_TIMINGS` 六值 + 
      `StaticPolicy.revalidate(when=...)` 对未知时机名报错 + 每判定 1 行审计；"逐个先于动作执行"
      需要动作真实存在，矩阵收口属 M2（worktree 时机）与 M3（launch/recover/retry/external_write/mcp_call 五处）。
- [x] **A5** 默认拒绝集生效：auto merge、任意 shell、全网访问、未列入 MCP server/tool。
      证据（2026-11-04，Task 5）：`test_default_deny_set_covers_the_four_spec_cases` 逐项断言——
      auto merge 恒 `not_granted`；任意 shell（`sh -c curl evil`）与带附加参数的同一命令
      （`pytest -q`、`pytest;echo`、空串）一律 `command_not_allowed`（命令是**整串精确匹配**，
      见 `test_mandate_commands_allowlist_is_exact_match`）；全网访问在无 `network_profile` 时
      整类拒，有 profile 时也只有精确 origin 过、`evil.example` 拒；MCP 未列入的 server 或 tool
      均拒（`context7:get-docs`、`evil:resolve-library-id`、缺 tool）。
      另加 `test_authorize_checks_expiry_and_unconstrained_kinds` 钉死过期与无 allowlist 维度
      的 kind 两条边界。

### B 审批（M1）

- [x] **B1** `approved` 绑定 `(action, resource_digest)`；参数或资源变化后旧批准失效。
      进度（2026-11-04，Task 3 前提半边）：`resource_digest` 的纯函数性质已被单测钉死
      （乱序稳定 + 四种变化各得新值）。
      证据（2026-11-04，Task 7 收口）：`test_approved_binding_breaks_on_resource_change`——批准
      `push_branch + digest(BRANCH_A)` 后，Gate 对同一动作换 `BRANCH_B` 直接 deny `approval_stale`，
      换回 A 仍 allow（失效的是那份批准对新资源，不是批准被吃掉）；每次 check 都落一行 authorize，
      `resource_digest` 与当刻请求逐等，审计能看出"批准绑定的是哪一个资源"。
- [ ] **B2** `denied` 使 Assignment `blocked|failed`，不得换动作绕过。
      进度（2026-11-04，Task 7 Gate 半边）：`test_denied_never_self_heals`——denied 后重新 submit、
      推进时钟、连查三次都仍是 `approval_denied`（粘连在 `(ticket, action)` 上，先于批准匹配判定，
      所以换 digest 也走不到放行）；换动作只得到 `approval_pending`，不是自动放行。
      "使 Assignment `blocked|failed`"要有 Assignment 可管，属 M2/M3 接线，届时由本条收口。
      计划偏差（2026-11-04，Task 9）：第 5 节 Task 9 原写"M1 出口勾 8 项（含 B2）"，实测只勾 7 项——
      B2 的 Assignment 半边在 M1 没有可管对象（为对齐数字而勾会把未证事实写成已证），维持未勾并保留 Gate 半边记录。
- [x] **B3** Ticket 取消同事务撤销 pending approval。
      证据（2026-11-04，Task 7）：`test_guarded_cancel_rolls_back_all_three_on_error` 在
      `ApprovalService.revoke_pending` 处注入异常 → 票仍 `queued`、`due_effects` 为空、审批行仍
      `pending`、`store.in_transaction` 归 False（三件事真·同事务，L3 的事务体内只有同步库写与纯
      函数）；`test_guarded_cancel_rejects_terminal_ticket` 再用状态机违规（COMPLETED 票取消 →
      `IllegalTransitionError`）证明同一回滚从另一条路径成立。
      `test_guarded_cancel_revokes_pending_only`：pending → revoked，approved/denied 历史行原样保留
      （撤销只碰 pending，审计不删史），未终态 Assignment 各入队 `cancel:{assignment.id}`，已终态的
      `interrupted` 不再入队。
- [x] **B4** 需审批动作先记录后执行，且批准在进程重启（崩溃恢复）后仍可判定。
      证据（2026-11-04，Task 7）：`test_approval_survives_reopen`——批准写入后 `store.close()`，
      重开同一库文件、换新 `ApprovalService`/`ApprovalGate`，Mandate 也从库里 `get_mandate` 读回，
      Gate 仍判 `approved`（事实源是行而不是内存）；"先记录后执行"由 Gate 结构保证：
      `check` 先写 authorize 审计行才返回结论，没有全等批准行时返回的是 deny（见步骤 1.7 的 ⑤、L5
      审批半边进度），调用方拿不到 allow 就没有"先做后记"的路径。

### C worktree 与命令（M2）

- [ ] **C1** 多 Ticket 并行 worktree 目录与分支互不污染（并发集成用例）。
- [x] **C2** 重复 prepare 幂等复用同一 worktree（含崩溃重放）。
      证据（2026-11-05，M2 Task 4）：`test_prepare_effect_is_idempotent_on_replay`（换连接重放：
      一个 worktree、一个分支、ownership 不被改写、effect 一行且 `attempts == 1`）+
      `test_prepare_effect_key_is_scoped_to_the_assignment`（`prepare:{assignment_id}` 同键不新增行）
      + `test_prepare_reuses_the_identical_worktree`（同进程内第二次 prepare 不再 `worktree add`）。
      口径与用例名详见第 1 节步骤 2.4。
- [x] **C3** 复用四要素（remote/metadata/base ref/ownership）不一致时拒绝并保留诊断。
      证据（2026-11-05，M2 Task 3）：`test_reuse_rejects_each_element_and_keeps_the_scene` 六种破坏
      （remote / ticket_id / base_ref / ownership 坏档 / ownership 缺失 / ownership 分支标记指向
      他人）逐条断言 `WorktreeReuseError.code`、detail 里的人读标签、`diagnostics` 的六键与
      recorded/requested 同键集，外加 `handle.path` 目录仍在；`test_prepare_reuses_the_identical_worktree`
      证明全等时不重复 `worktree add`、不改写 ownership。用例名与详细口径见第 1 节步骤 2.3。
- [x] **C4** 路径逃逸拒绝：resolve 后必须在配置 root 内。
      证据（2026-11-05，M2 Task 1）：`execution/paths.py` 的 `confine(root, candidate)` 是全层
      唯一收口点——相对路径先拼 root、随后 `resolve()`，判定用 `is_relative_to(root.resolve())`，
      root 自身算在内；`tests/unit/test_worktree.py` 四例覆盖 `..` 穿越、root 外的绝对路径、
      `/etc/hosts`、以及**链接本身在 root 内而解析结果在外**的符号链接（macOS `/var` →
      `/private/var` 同型）；错误详情同时给出 root 与原始 candidate（`test_confine_error_names_the_root_and_the_candidate`）。
- [ ] **C5** request 文本不直通分支名、路径、命令参数；slug 仅 `[a-z0-9-]`。
      进度（2026-11-05，M2 Task 2 命名半边）：**分支名/worktree 目录名**半边已闭合——slug 只留
      `[a-z0-9-]` 且有 8 条注入语料 + `ticket.task` 正文用例（步骤 2.2 证据）；**路径**半边由 C4 的
      唯一收口点 `confine()` 守着，但"进入路径的字符串只能来自 `workspace_name`"要在 Task 3 的
      `prepare` 入口接线后才算证到——**该半边现已闭合**（2026-11-05，M2 Task 3：`prepare` 里进
      入路径的字符串只有 `confine(root, workspace_name(ticket))` 的产物，克隆侧只做
      `Path(repository).resolve()`，`worktree add` 的 argv 逐项来自 branch/路径/base_ref）。
      剩 **命令参数** 半边属 Task 7（`CommandRunner` argv 构造）与 Task 9（shell 语法拒绝），
      齐了才勾本条。
- [ ] **C6** `shell=True` 与 shell 语法字符一律拒绝且有审计。
- [x] **C7** 命令 allowlist、cwd 锁 worktree、超时、输出上限、脱敏五项全部有测试。
      证据（2026-11-05，M2 Task 7）：`tests/unit/test_command_runner.py` 的 C7 段 14 例，五项逐项对上——
      ① **allowlist**：`test_denied_command_has_no_side_effect`
      （空名单 + `sys.executable -c "open('pwned.txt','w')"` → `denied_reason="command_not_allowed"`
      且文件不存在、`started_at == ended_at` 即只有一次读数 = 没 spawn）+
      `test_allowlist_matches_argv_zero_byte_for_byte`（名单写 `python3` 不放行绝对路径、名单写绝对路径
      也不放行裸名：**逐字节精确、不取 basename、不做 PATH 归一化**，归一化猜错的方向是放行）；
      ② **cwd 锁 worktree**：`test_cwd_escape_denies_before_any_spawn`（root 外的 cwd 被拒且不在那里留痕）+
      `test_relative_cwd_is_confined_into_the_root`（相对 root 的写法由 runner 收口，孩子 `getcwd()`
      看到的是解析后的绝对路径）+ `test_allowed_command_runs_inside_its_worktree`；
      ③ **超时**：`test_timeout_has_no_exit_code_but_keeps_partial_output`（`sleep(2)` 配 `timeout_s=0.2`
      → `timed_out=True`、`exit_code is None`、已 flush 的 `partial` 仍在 stdout）+
      `test_default_timeout_applies_when_the_request_does_not_ask`（请求不填走 runner 默认）；
      ④ **输出上限**：`test_output_over_the_cap_is_truncated_within_budget`（200KB →
      `len(stdout.encode()) <= 64*1024`、尾标 `[truncated N bytes]`、未超限的 stderr 原样）+
      `test_tiny_cap_stays_within_its_own_budget`（上限可配到 200 字节，总长含尾标仍不越界）；
      ⑤ **脱敏**：`test_secrets_are_redacted_before_truncation`（**顺序可证**：100 A + `SEKRET` + 100 A
      配 128 字节预算，先截后脱敏会让 `SEKRET` 活下来）+ `test_empty_redaction_entries_are_ignored`。
      另两例钉形状：`test_denied_reasons_are_stable_codes_not_prose`（三条码 + 两病并发时报①，
      且码是 snake_case 标识符不是文案）、`test_nonzero_exit_is_a_result_not_a_denial`（退出码 3 是
      命令的结论，不设拒绝理由也不抛）。
      **本条不含"落 Report"半边**：`CommandResult` 目前无生产调用方，文本形状属 Task 8、
      写入 `Assignment Report` 的接线属 M2 Task 12/M3（第 1 节步骤 2.6 的进度记同一条界线）。
- [x] **C8** 执行环境仅白名单变量；宿主密钥、无关环境变量不注入。
      证据（2026-11-05，M2 Task 6）：`execution/environment.py` 的 `build_child_env` 产出恰好七键
      （五个 `STABLEAGENT_*` + `PATH` + `LANG`），宿主 13 键进来仍是那七键；`PATH` 取宿主值、
      `LANG` 固定 `C.UTF-8`，`GITHUB_TOKEN`/`AWS_SECRET_ACCESS_KEY`/`SSH_AUTH_SOCK`/`PYTHONPATH`/
      `HOME`/`STABLEAGENT_DB_PATH` 既不在键里也不在任何值里。六例：
      `test_child_env_is_an_allowlist`、`test_child_env_leaves_the_database_and_host_home_behind`、
      `test_child_env_has_no_secrets_from_arguments`、真 `os.environ` 那条、无 `PATH` 那条，以及
      直接证明"子进程可见"的 `test_child_process_sees_only_the_allowlist`（真起解释器打印自己的
      `os.environ`；多出的键只允许是 macOS 在 exec 时注入的 `__CF_USER_TEXT_ENCODING` 一类，
      名单写死在 `OS_INJECTED_KEYS` 里）。
      **本条只覆盖"Agent 命令环境"**：Docker 容器环境（D2）与 git 子进程环境（`git_env()` 四项，
      Task 3 已证）是另外两张名单，前者随 M2 Task 10/11 收口。
      接线口径一处留档：本 Task 证的是白名单函数与真子进程可见环境，`CommandRunner` 把它接成
      `env=build_child_env(...)` 的调用点在 Task 7（步骤 2.6 的实现里），届时的 C7 用例不应被读成
      C8 的第二次证明。

### D Sandbox 分层（M2）

- [ ] **D1** `LocalRestrictedRunner` 的文档、拒绝消息、Report 不称"sandbox"（文案断言）。
- [ ] **D2** `DockerRunner` 默认参数：network none、非特权、无 socket、资源上限、仅挂本
      worktree、镜像配置带 pin 指南（argv 构造断言）。
- [ ] **D3** Docker 不可用时测试跳过、核心门禁不受影响。

### E MCP（M3）

- [ ] **E1** fake transport 覆盖 initialize/list/call/JSON-RPC error/超时/进程退出/schema 错误。
- [ ] **E2** 本地 stdio fixture 验证真实 framing；全程离线。
- [ ] **E3** 发现 ≠ 授权：非 allowlist 的 server/tool 不进 capability view、调用被拒且不启动。
- [ ] **E4** MCP 输出走 ToolResult→Evidence→ContextEngine，超长受压缩预算。

### F GitHub 显式闭环（M3）

- [ ] **F1** 重复显式请求（同稳定 ID）返回同一 Ticket 或明确 duplicate。
- [ ] **F2** Mandate+Ticket+Input+Assignment+launch effect 同一事务原子创建。
- [ ] **F3** comment/PR/request-review 三个写 effect 执行前均验证授权与幂等键。
- [ ] **F4** GitHub 错误三分类（可重试/不可重试/需人工）入 Report，状态码不吞。
- [ ] **F5** 恢复与重试路径不重复 PR、评论、push（fake client 调用计数断言）。
- [ ] **F6** merge/auto-merge 在 EffectKind 与授权集合中结构性不存在。

### G 示例与文档（M4）

- [ ] **G1** 端到端示例离线跑通"请求→授权→worktree→修改→PR effect"，示例即测试。
- [ ] **G2** 五类拒绝示例：非 allowlist、无 mention、越权命令、无审批写、auto merge。
- [ ] **G3** ADR-05"测试与证据"由占位改为实测；安全说明定稿。
- [ ] **G4** README/CHANGELOG/Roadmap/docs 索引与规格退出清单同步，不夸大本地受限模式。

### H 质量与收口（M4 出口）

- [ ] **H1** P0~P4 全量用例零回归；`stableagent/core/` 对 P4 收口提交零 diff（L1 复核）。
- [ ] **H2** 全库门禁绿：`pytest -q`、`mypy`、`ruff check`、`ruff format --check`，数字记账本。
- [ ] **H3** CI FOCUS 扩容生效且远端 CI（3.10/3.12）双绿，run ID 记入本账本。
- [ ] **H4** 覆盖率不低于 P4 基线（全库 99.02%；新心脏文件逐个 ≥90%）。

## 3. 阶段验证命令（收口前须在 CI 复现）

```bash
.venv/bin/pytest -q tests/unit/test_policy.py tests/unit/test_approval.py tests/unit/test_worktree.py
.venv/bin/pytest -q tests/unit/test_command_runner.py tests/unit/test_sandbox.py tests/unit/test_mcp_client.py
.venv/bin/pytest -q tests/unit/test_github_integration.py tests/integration/test_mcp_stdio.py
.venv/bin/pytest -q tests/integration/test_guarded_ticket_recovery.py
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
.venv/bin/mypy
.venv/bin/pytest -q
git diff 234f620..HEAD --stat -- stableagent/core/   # 必须 0 行（L1）
```

Docker/真实 GitHub 用例只能在显式 marker 与环境变量存在时运行（L8）。

## 4. 任务级实施计划（占位）

沿用 P4 约定：每个里程碑开工前，把该里程碑的步骤展开为 TDD 粒度的 Task/Step 追加为独立小节
（M1 → 第 5 节，M2 → 第 6 节，M3 → 第 7 节，M4 → 第 8 节）。任务级 Step 不计入 40 个里程碑步骤。

## 5. M1 任务级实施计划（writing-plans 产出，2026-11-03）

> **状态：M1 收口（Task 1~9 全部完成，2026-11-04）；M2 待维护者确认开工。** 对应第 1 节 M1 的 9 个步骤（1.1~1.9），展开为
> **9 个 Task / 45 个 Step**。
> 执行分支 `feat/p5-m1-control-plane`（从 `main` 切出）。零语义改动约束沿用：M1 期间
> `git diff 234f620..HEAD -- stableagent/core/` 恒为 0 行（L1）；`orchestration` 包只允许
> **兼容性扩展**（加列迁移、Mandate 加默认字段、Store 加方法），Scheduler/Service/Outbox
> 既有方法签名与行为不动。
>
> 每个 Task 的固定节奏（沿用 P4）：Step 1 写失败测试 → Step 2 跑到红并记录命令与预期失败 →
> Step 3 实现 → Step 4 目标文件跑绿 + 全量 `pytest -q` → Step 5 提交。Step 4/5 前必须
> `mypy`、`ruff check stableagent tests examples benchmarks`、
> `ruff format --check stableagent tests examples benchmarks` 全绿。
> 证据写进第 1 节 M1 对应步骤与第 2 节 DoD 条目，不重写历史输出。

### Task ↔ 里程碑步骤与 DoD 对照

| Task | 步骤 | 本 Task 产生的 DoD 证据 |
|---|---|---|
| 1 | 1.1 | L2（前半：control 边界用例落位） |
| 2 | 1.2 | A1（旧库升级）、L3（迁移在事务内） |
| 3 | 1.3 | B1（digest 纯函数前提） |
| 4 | 1.4 | A1（往返）、A2（p5-v1 字段形状） |
| 5 | 1.5 | A5、L6、L4（行为半边） |
| 6 | 1.6 | A3（审计先于接线） |
| 7 | 1.7 | B1~B4 |
| 8 | 1.8 | L2（收口）、A2（扫描半边） |
| 9 | 1.9 | M1 出口 + 账本回填 |

---

### Task 1: `control/` 包骨架、版本常量与稳定错误家族（步骤 1.1）

**Files:**
- Create: `stableagent/control/__init__.py`、`stableagent/control/errors.py`
- Test: `tests/unit/test_control_boundaries.py`（新建，首批 3 用例）

**Interfaces:**
- Produces: `CONTROL_SCHEMA_VERSION = "0.1.0"`（只在本包定义，顶层 `stableagent/__init__.py`
  不重导出——沿用 ADR-05 决策 1）；`ControlError(ValueError)` 基类（`code = "control.error"`，
  构造风格与 `OrchestrationError` 相同：`super().__init__(f"[{code}] {message}")`、`self.detail`），
  8 个子类与稳定码：
  `AdmissionRejectedError(reason)`→`control.admission_rejected`、
  `UnknownMandateError(mandate_id)`→`control.unknown_mandate`、
  `UnknownApprovalError(approval_id)`→`control.unknown_approval`、
  `ApprovalDecidedError(approval_id, status)`→`control.approval_decided`、
  `StaleApprovalError(approval_id)`→`control.stale_approval`、
  `MandateExpiredError(mandate_id, expires_at, now)`→`control.mandate_expired`、
  `NotGrantedError(action, mandate_id)`→`control.not_granted`、
  `RevalidationFailedError(when, mandate_id)`→`control.revalidation_failed`。

- [x] **Step 1: 写失败的测试**——`tests/unit/test_control_boundaries.py`：
      `test_control_version_is_independent_and_not_reexported`（常量等值 + `"ControlError" not in
      dir(stableagent)`）；`test_control_error_code_inventory_is_unique_and_prefixed`（对照 P4 同名
      用例：全部 `.code` 以 `control.` 开头、tail 为小写 snake_case、互不重复、类集合恰好 =
      基类 + 8 个子类）；`test_control_error_bases_valueerror`。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_control_boundaries.py`
      Expected: FAIL — `ModuleNotFoundError: stableagent.control`
      Actual: FAIL — `ImportError: cannot import name 'CONTROL_SCHEMA_VERSION' from
      'stableagent.control'`（占位包 2026-10-30 起已存在，见步骤 1.1 证据）
- [x] **Step 3: 实现**两个文件（docstring 写明"授权事实源在 Store，判定逻辑在本包"）。
- [x] **Step 4: 跑到绿**：同命令 PASS + `.venv/bin/pytest -q` 全量不红。
      Actual：目标文件 `3 passed`；全量 `436 passed, 1 skipped`；`mypy` / `ruff check` /
      `ruff format --check` 全绿。
- [x] **Step 5: 提交**：`git add stableagent/control/ tests/unit/test_control_boundaries.py`
      `&& git commit -m "feat: open the control package with its own version and stable codes"`
      Actual: `76538e7`（3 files, +147 −1）。

### Task 2: Store 迁移 `p5-control-v1`——加列与两新表（步骤 1.2）

**Files:**
- Modify: `stableagent/orchestration/store.py`（`_SCHEMA` 追加两表、`_migrate()` 加列）
- Test: `tests/unit/test_control_migration.py`

**Interfaces:**
- Produces: `mandates` 加 11 列（全部带默认值，可空或 `[]`）：`repository TEXT`、
  `base_ref TEXT`、`actions TEXT NOT NULL DEFAULT '[]'`、`allowed_paths TEXT NOT NULL
  DEFAULT '[]'`、`allowed_commands TEXT NOT NULL DEFAULT '[]'`、`allowed_mcp TEXT NOT NULL
  DEFAULT '[]'`、`network_profile TEXT`、`expires_at INTEGER`、`requires_approval INTEGER
  NOT NULL DEFAULT 0`、`request_origin TEXT`、`request_digest TEXT`。
  新表 `_SCHEMA["approval_requests"]`：`id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL,
  assignment_id TEXT, action TEXT NOT NULL, resource_digest TEXT NOT NULL, reason TEXT NOT
  NULL, requested_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', decided_by
  TEXT, decided_at INTEGER`；`_SCHEMA["authorization_audit"]`：`id TEXT PRIMARY KEY, kind
  TEXT NOT NULL, ticket_id TEXT, assignment_id TEXT, mandate_id TEXT, action TEXT,
  resource_digest TEXT, policy_version TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT,
  created_at INTEGER NOT NULL`。`_migrate()` 追加
  `INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES ('p5-control-v1', ?)`。
  迁移后 `Store.schema_version()` 返回 `"p5-control-v1"`（`ORDER BY applied_at DESC` 语义，
  既有 P4 测试若断言 `"0.1.0"` 需在 Step 2 变红处确认后**改测试期望为版本行存在**而非返回值）。

- [x] **Step 1: 写失败的测试**——`tests/unit/test_control_migration.py`：
      `test_legacy_p4_database_upgrades_without_touching_rows`：手工按 P4 原始 DDL 建一个临时库、
      插入 1 条 `p4-local-v1` mandate；`Store(db, clock=FixedClock())` 打开后断言：
      新列全在（PRAGMA table_info）、两新表存在、旧行 `policy_version` 仍为 `p4-local-v1` 且
      新字段读为默认值（A1）；升级前后该行的 6 个原字段值逐等。
      `test_migration_marker_is_idempotent`：重复打开同一库，`schema_migrations` 里
      `p5-control-v1` 恰 1 行。
      `test_migration_runs_inside_transaction`：monkeypatch `Store.transaction` 记录进入次数，
      断言 `_migrate` 期间计数 +1（L3 迁移半边）。
      追加第 4 用例 `test_new_tables_have_the_authorization_columns`：两新表列名与 ADR-05 决策 3
      逐一对应（Task 6/7 的仓储方法依赖这套列名，先钉死）。旧库 DDL 由本测试文件逐字抄录
      `234f620` 的 8 表 `_SCHEMA`，不依赖当前 `_SCHEMA`（否则升级路径无从证明）。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_control_migration.py`
      Expected: FAIL — 旧库打开后 PRAGMA 里没有 `repository` 列（AssertionError）
      Actual: FAIL — `3 failed, 1 passed`（三处同为缺列/缺表 AssertionError；
      `test_migration_runs_inside_transaction` 天然先绿，它是事务口径的回归钉而非迁移红项）
- [x] **Step 3: 实现**：`_migrate()` 内 `PRAGMA table_info(mandates)` 取现有列名，逐列
      `ALTER TABLE mandates ADD COLUMN ...`（每列 `if name not in columns` 守卫，照抄
      `deadline_at` 先例）；`_SCHEMA` 补两表 DDL（新建库由 `_create_schema` 直接建齐）；
      追加 `schema_migrations` 标记行。列清单落为模块常量 `_MANDATE_ADD_COLUMNS`（11 项），
      `schema_version()` 补 `rowid DESC` 定尾（`FixedClock` 下同毫秒并列）。
- [x] **Step 4: 跑到绿**：目标文件 PASS + 全量 `pytest -q`（重点看 P4 的 store 测试无回归）。
      Actual：目标文件 `4 passed`；全量首跑 `3 failed, 437 passed`——红项正是 P4 的表集合断言
      （`TABLES` 精确等、`schema_version() == "0.1.0"`、`set(ORCHESTRATION_TABLES) == TABLES`），
      按计划口径改为 10 表 + "版本行含 `0.1.0`、返回值取最新迁移"后 `440 passed, 1 skipped`。
- [x] **Step 5: 提交**：`git commit -am "feat: migrate the store to p5-control-v1 additively"`
      Actual: `0e92dbd`（3 files：`store.py` + 新测试 + P4 表集合期望更新）。

### Task 3: `control/model.py`——Action/Resource/Admission/Decision/ApprovalRequest + digest（步骤 1.3）

**Files:**
- Create: `stableagent/control/model.py`
- Test: `tests/unit/test_control_model.py`

**Interfaces:**
- Produces:
  - `Action(str, Enum)`：规格 2.1 的 13 个成员（`prepare_worktree` … `enable_auto_merge`）。
    **注意**：`ENABLE_AUTO_MERGE` 存在于枚举是为了"点名拒绝"可测（规格原文），但 ADR-05
    决策 5 与 L6 约束的是它**不进入任何可授权集合与 EffectKind**（Task 5 落地）。
  - `Resource(BaseModel)`：`kind: str` + 可选 `path/command/server/tool/url: str | None` +
    `extra: dict[str, Any] = {}`。
  - `Admission(BaseModel)`：`admitted: bool`、`reason_code: str`、`requester: str`、
    `repository: str`、`stable_id: str`、`request_digest: str`。
  - `Decision(BaseModel)`：`allowed: bool`、`reason: str`、`policy_version: str`。
  - `ApprovalStatus(str, Enum)`：`PENDING/APPROVED/DENIED/REVOKED`；`ApprovalRequest`
    与表列一一对应（毫秒整数、ID 注入生成，沿用 P4 记录风格）。
  - `resource_digest(action: Action, resource: Resource) -> str`：
    `sha256(json.dumps({"action": action.value, "resource": resource.model_dump()},
    sort_keys=True, ensure_ascii=False).encode())` 的 hexdigest。

- [x] **Step 1: 写失败的测试**——关键用例：
      `test_resource_digest_is_stable_under_key_order`（extra 键乱序 → digest 相等）；
      `test_resource_digest_changes_on_action_or_resource`（改 action、改 path、改 extra 值
      各得不同 digest）；`test_action_enum_has_exactly_spec_thirteen_members`；
      `test_approval_request_requires_ticket_and_digest`（缺字段 pydantic ValidationError）。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_control_model.py`
      Expected: FAIL — `ModuleNotFoundError`
      Actual: FAIL — `ModuleNotFoundError: No module named 'stableagent.control.model'`
- [x] **Step 3: 实现** `control/model.py`（文件头 docstring 写明"纯数据 + 纯函数，不 import
      orchestration 之外的执行/集成层"）。
- [x] **Step 4: 跑到绿**：目标文件 + 全量。
      Actual：目标文件 `5 passed`；全量 `445 passed, 1 skipped`；`mypy`（38 files）/
      `ruff check` / `ruff format --check`（92 files）全绿；L1 core diff 仍 0 行。
- [x] **Step 5: 提交**：`git commit -am "feat: describe actions, admissions and approvals as data"`
      Actual: `0d92da9`（新文件需 `git add`：`control/model.py` + `test_control_model.py`）。

### Task 4: Mandate 兼容扩展与 Store 往返（步骤 1.4）

**Files:**
- Modify: `stableagent/orchestration/model.py`（`Mandate` 加带默认值的字段）、
  `stableagent/orchestration/store.py`（`insert_mandate`/`_row_to_mandate` 扩到 17 列）
- Test: `tests/unit/test_mandate_extension.py`

**Interfaces:**
- Produces: `Mandate` 新字段（默认值 = "P4 本地授权无外部面"语义）：`repository/base_ref/
  network_profile/request_origin/request_digest: str | None = None`、
  `actions/allowed_paths/allowed_commands/allowed_mcp: list[str] = Field(default_factory=list)`、
  `expires_at: int | None = None`、`requires_approval: bool = False`。
  `origin_kind` 的 Literal 扩为 `Literal["local", "github"]`（默认仍 `"local"`）。
  Store 写读 JSON 列表列（`sort_keys=True, ensure_ascii=False`，沿用 report details 先例）。

- [x] **Step 1: 写失败的测试**——
      `test_p5_mandate_roundtrip_keeps_every_field`（含 `policy_version="p5-v1"`、11 个新字段
      的满配构造 → 落库 → 读回逐字段相等）；
      `test_p4_local_mandate_roundtrip_unchanged`（旧 6 字段构造，新字段读为默认——A1 往返半边）；
      `test_mandate_ids_list_is_sorted_on_write`（写 `["b","a"]` 读回 `["a","b"]` 或原样——
      以实现口径为准，钉死其一并断言）——定为**写入前升序**，测试名 `test_mandate_list_columns_are_sorted_on_write`。
      追加第 4 用例 `test_upgraded_legacy_row_reads_through_the_model_with_defaults`：复用 Task 2 的旧库构造经 `Store.get_mandate` 读回，A1 就此收口。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_mandate_extension.py`
      Expected: FAIL — `TypeError`/`ValidationError`（Mandate 无新字段）
      Actual: FAIL — `3 failed, 1 passed`，红为 `AttributeError: 'Mandate' object has no
      attribute 'actions'`：pydantic v2 默认 `extra='ignore'`，满配构造里的新字段被静默丢弃，
      红暴露在读取侧而非构造侧。第 4 用例（旧库经模型读默认值）天然先绿。
- [x] **Step 3: 实现**：model 加字段 + store 两个读写函数扩列（INSERT 参数从 6 → 17）。
- [x] **Step 4: 跑到绿**：目标文件 + `tests/unit/test_orchestration_store.py` 回归。
      Actual：目标文件 `4 passed`；连同 `test_orchestration_store.py` +
      `test_control_migration.py` = `27 passed`；全量 `449 passed, 1 skipped`；
      `mypy` / `ruff check` / `ruff format --check`（93 files）全绿；L1 core diff 仍 0 行。
- [x] **Step 5: 提交**：`git commit -am "feat: carry p5 authorization fields on mandates"`
      Actual: `9207146`（`orchestration/model.py` + `orchestration/store.py` + 新测试）。

### Task 5: `StaticPolicy` 与默认拒绝集（步骤 1.5）

**Files:**
- Create: `stableagent/control/policy.py`
- Test: `tests/unit/test_static_policy.py`

**Interfaces:**
- Produces:
  - `StaticPolicyConfig(BaseModel)`：`repository: str`、`actors: frozenset[str]`、
    `grantable_actions: frozenset[Action]`、`allowed_commands: list[str]`、
    `allowed_mcp: list[str]`、`network_profiles: dict[str, list[str]]`、
    `default_validity_ms: int`。构造校验：`Action.ENABLE_AUTO_MERGE in grantable_actions`
    → `AdmissionRejectedError("grantable_must_exclude_auto_merge")`（结构性拒绝的**构造期**
    一道；运行期还有 Task 5.1.5 断言）。
  - `StaticPolicy(config, *, clock, ids)`：
    `admit_explicit(request: ExplicitRequest) -> Admission`——四道门顺序：
    actor ∈ allowlist → 明确 mention → repository 精确匹配 → 生成 request_digest
    （每道失败返回 `admitted=False` + 稳定 `reason_code`：`actor_not_allowlisted` /
    `mention_required` / `repository_mismatch`）；**重复检测归 M3 Task 3.5**（稳定 ID 语义
    依赖 GitHub 请求模型，本任务不实现，账本已注明）。
    `create_mandate(admission, *, actions, allowed_paths, ...) -> Mandate`——**唯一**
    `p5-v1` Mandate 构造点（A2），`expires_at = clock.now_ms() + default_validity_ms`，
    内部把 `Action.ENABLE_AUTO_MERGE` 从传入 actions 里**剔除**并拒绝（双保险）。
    `authorize(mandate, action, resource) -> Decision`——action ∈ mandate.actions ∧
    resource 命中 allowlist（command 精确匹配 / path 前缀 / mcp server/tool ∉ allowed_mcp 拒）
    ∧ 未过期；拒绝 reason 用稳定码。
    `revalidate(mandate, *, when: str) -> Decision`——过期 / `mandate.policy_version !=
    config 派生版本` → deny（when ∈ {"launch","recover","retry","worktree","external_write",
    "mcp_call"} 全集校验，未知 when → `RevalidationFailedError`）。
- [x] **Step 1: 写失败的测试**——判定矩阵用例：
      `test_admit_rejects_in_gate_order`（非 allowlist + 无 mention + 仓库不符同时踩 →
      reason 按第一道门）；`test_grantable_excludes_auto_merge_by_construction`（构造期抛）；
      `test_authorize_denies_every_action_not_granted`（含 `ENABLE_AUTO_MERGE` 恒 deny——
      A5、L6 运行期半边）；`test_mandate_commands_allowlist_is_exact_match`（`ls` 过、
      `ls -la /etc` 与 `rm` 拒）；`test_revalidate_flags_expiry_and_drift`；
      `test_p5_mandate_is_only_constructable_via_policy`（`create_mandate` 是唯一返回
      `policy_version=="p5-v1"` 的入口：对 `admit→create` 后直接 `Mandate(policy_version="p5-v1")`
      的裸构造在本包外无引用——AST 断言放 Task 8，此处先行为半边：L4 用例，
      请求文本携带 `"actions": ["enable_auto_merge"]` 字样不改变产出 Mandate 的 actions）。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q tests/unit/test_static_policy.py`
      Expected: FAIL — `ModuleNotFoundError: stableagent.control.policy`
      Actual: FAIL — `ImportError: cannot import name 'ExplicitRequest' from
      'stableagent.control.model'`（Step 3 要求 `ExplicitRequest` 落在 `control/model.py`，
      收集期先在该处失败）
- [x] **Step 3: 实现** `policy.py`（不 import `execution/integrations`；`ExplicitRequest`
  放 `control/model.py` 作最小形状：`repository/requester/mentioned/stable_id/text`，
  GitHub 富字段归 M3 Task 3.5 扩展——偏差届时记入账本 F1 条目）。
      实际落位：`ExplicitRequest(repository/requester/mentioned/stable_id/text)` 进
      `control/model.py`，`request_digest(text)` 用与 `resource_digest` 一致的规范化口径
      （`sha256(json.dumps(text, ensure_ascii=False))`）。
- [x] **Step 4: 跑到绿**：目标文件 + 全量。
- [x] **Step 5: 提交**：`git commit -am "feat: admit explicit requests through a static deny-first policy"`

### Task 6: 审计落库——每次决策一行、含 policy_version（步骤 1.6）

**Files:**
- Modify: `stableagent/orchestration/store.py`（`insert_audit`、`list_audit(*, ticket_id=None, kind=None)`、
  `insert_approval`、`update_approval`、`get_approval`、`list_approvals(ticket_id, *, status=None)`）
- Create: `stableagent/control/audit.py`
- Test: `tests/unit/test_authorization_audit.py`

**Interfaces:**
- Produces: `AuditWriter(policy, store, *, clock, ids)`——方法 `admitted(admission)` /
  `authorized(mandate, action, resource, decision)` / `revalidated(mandate, when, decision)` /
  `decided(approval, decision)`，每个恰好写 1 行 `authorization_audit`，`kind ∈
  {"admit","authorize","revalidate","approve"}`，`decision ∈ {"allow","deny"}`，
  `policy_version` 写**决策当刻**的策略版本快照；`reason` 存稳定码，非自由文本。
- [x] **Step 1: 写失败的测试**——
      `test_each_decision_kind_writes_exactly_one_row`（四类各一次 → 4 行，字段齐全，A3）；
      `test_audit_snapshots_policy_version`（用两个不同 config 实例先后决策 → 两行版本不同）；
      `test_audit_reason_is_stable_code_not_free_text`（deny 行 reason 匹配
      `^[a-z][a-z0-9_]*$`）。追加第 4 用例 `test_approval_rows_roundtrip_and_audit_filters`：
      审批行 insert/get/update（含未知 id → 行数 0）+ `list_approvals` 状态过滤 +
      `list_audit` 的 ticket_id/kind 过滤——Task 7 的仓储前提，也是 `store.py` 不掉出 100% 的前提。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_authorization_audit.py`
      Expected: FAIL — `AttributeError: 'Store' object has no attribute 'insert_audit'`
      Actual: FAIL — 收集期 `ModuleNotFoundError: No module named 'stableagent.control.audit'`
      （测试模块顶层 import `AuditWriter`，模块缺失时 `insert_audit` 的 AttributeError 无从观测；
      与 Task 1/4/5 同型的"计划预期红在更上游"）
- [x] **Step 3: 实现** store 方法 + `audit.py`。
      实际落位：`Store.insert_audit/list_audit/insert_approval/update_approval/get_approval/`
      `list_approvals`（原语入、行字典出，沿用 `clauses, params` + `ORDER BY … ASC, id ASC`
      的 P4 写法）+ `control/audit.py` 的 `AuditWriter`；`StaticPolicyConfig.policy_version`
      与 `StaticPolicy.policy_version` 属性为本 Task 追加（三处取向与理由见第 1 节步骤 1.6）。
      首轮测试修正一次构造：同一进程内多个 writer 共用一个 `SequenceIds`，否则审计行 id 撞
      `UNIQUE`（`sqlite3.IntegrityError`）——生产代码未改。
- [x] **Step 4: 跑到绿**：目标文件 `4 passed` + 全量 `.venv/bin/pytest -q` →
      `462 passed, 1 skipped`，总覆盖 99.1%（`store.py` 192 stmts、`control/audit.py` 25 stmts、
      `control/policy.py` 101 stmts 均 100%）；`.venv/bin/mypy` Success（40 files）、
      `ruff check` All checks passed、`ruff format --check` 97 files already formatted；
      `git diff 234f620..HEAD -- stableagent/core/ | wc -l` = 0（L1）。
      出口命令同第 3 节，M1 出口（Task 9）再整体跑一遍。
- [x] **Step 5: 提交**：`e4be18a feat: persist every authorization decision with its version`

### Task 7: ApprovalService、Gate 与守卫式取消（步骤 1.7）

**Files:**
- Create: `stableagent/control/approval.py`
- Test: `tests/unit/test_approval_service.py`、`tests/unit/test_guarded_cancel.py`

**Interfaces:**
- Produces:
  - `ApprovalService(store, *, clock, ids)`：`submit(ticket_id, action, resource_digest,
    reason, assignment_id=None) -> ApprovalRequest`（pending 唯一键 `(ticket_id, action,
    resource_digest)` 已存在 pending 则返回既有行，不双开）；`decide(approval_id, approve:
    bool, by)`——终态后再 decide → `ApprovalDecidedError`；`revoke_pending(ticket_id) -> int`。
  - `ApprovalGate(policy, approvals, audit, store, *, clock)`：
    `check(mandate, action, resource) -> Decision`——先 `policy.authorize`；要求审批的
    （`mandate.requires_approval` 或 action ∈ 高危集）必须存在 `approved` 且
    `(action, resource_digest)` 全等；digest 变了 → deny `approval_stale`（B1）；
    同 action 有 `denied` 记录 → deny `approval_denied`，**不再自动放行**（B2 的 Gate 半边）。
  - `GuardedTicketOps.cancel_ticket(store, service_ids, ticket_id, reason=None) -> Ticket`：
    在**单个** `store.transaction()` 内完成：ticket `CANCEL` 转换（纯函数
    `next_ticket_status`）+ 未终态 Assignment 各入队 `CANCEL_ASSIGNMENT` effect（键
    `cancel:{assignment.id}`，与 P4 同形）+ `revoke_pending(ticket_id)`——真·同事务（B3），
    P4 `OrchestrationService.cancel_ticket` 一字不动（约束 1）。
- [x] **Step 1: 写失败的测试**——
      `test_approved_binding_breaks_on_resource_change`（approve 后同 action 换 path →
      Gate deny `approval_stale`，B1）；
      `test_denied_never_self_heals`（denied 后重新 submit+check → 仍 deny，B2）；
      `test_decide_twice_is_rejected`；
      `test_approval_survives_reopen`（关店重开 Store 后 Gate 仍判 approved，B4）；
      `test_guarded_cancel_rolls_back_all_three_on_error`（在 revoke 处注入异常 →
      ticket 未取消、effect 未入队、approval 仍 pending，证明原子性，B3 + L3）；
      `test_guarded_cancel_revokes_pending_only`（approved 历史行保留——审计不删史）。
      实际另加三个：`test_submit_is_idempotent_on_the_pending_key`（pending 键去重、终态不占键、
      撤销计数与 `decided_by` 留空）、`test_high_risk_actions_gate_without_the_flag`（高危集钉死成员
      + 低危直通 + `approval_context_missing`）、`test_gate_passes_policy_denials_straight_through`
      （策略先拒：`not_granted`/`mandate_expired` 到不了审批表，reason 保持策略侧码）。
      两个文件从 `test_authorization_audit.py` 复用 `_policy`/`_request`（沿用 Task 4 起
      `test_mandate_extension.py` 跨文件借用测试资产的先例）。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_approval_service.py
      tests/unit/test_guarded_cancel.py`
      Expected: FAIL — `ModuleNotFoundError`
      Actual: FAIL — `ModuleNotFoundError: No module named 'stableagent.control.approval'`
      （两个文件各自在收集期失败；M1 至此唯一一次计划预期红命中）
- [x] **Step 3: 实现** `approval.py`。
      实际落位：`ApprovalService.submit/get/for_ticket/decide/revoke_pending`、
      `HIGH_RISK_ACTIONS`、`ApprovalGate.check`（deny 优先级 denied → 全等批准 → stale → pending）、
      `GuardedTicketOps.cancel_ticket`。签名与本 Task 的五处取向见步骤 1.7 的 ②~⑥。
      两处测试构造修正（生产代码未改）：一个进程内共享同一个 `SequenceIds`（否则审计行/审批行/effect
      行各撞一次 `UNIQUE`）；同票第二个 Assignment 要递增 `attempt`（P4 的
      `UNIQUE (ticket_id, attempt)`）。
- [x] **Step 4: 跑到绿**：目标两文件 `10 passed` + 全量 `.venv/bin/pytest -q` →
      `472 passed, 1 skipped`，总覆盖 99.15%（`control/approval.py` 94 stmts、`store.py` 192 stmts
      均 100%）；`.venv/bin/mypy` Success（41 files）、`ruff check` All checks passed、
      `ruff format --check` 100 files already formatted；心脏边界复测：L1
      `git diff 234f620..HEAD -- stableagent/core/ | wc -l` = 0，约束 1
      `git diff 234f620..HEAD -- stableagent/orchestration/service.py` = 0；L2 反向检查
      `grep -rn stableagent.control stableagent/orchestration/` = 0 处（旧包不认识新包）。
- [x] **Step 5: 提交**：`31cb377 feat: gate high-risk actions on digest-bound approvals`

### Task 8: 依赖方向矩阵——三新包 × 五旧包（步骤 1.8）

**Files:**
- Modify: `tests/unit/test_control_boundaries.py`
- Test: 同文件（本 Task 无生产代码）

**Interfaces:**
- Produces: AST 矩阵测试 `test_no_legacy_package_imports_p5_layers`——
  扫描 `core/context/providers/tools/orchestration` 五包全部 `.py`，断言不 import
  `stableagent.control/execution/integrations`（后两包尚不存在 → `rglob` 空转但断言
  常量表已含三者，M2/M3 落包后自动生效）；
  `test_control_never_imports_execution_or_integrations`（方向锁：
  `stableagent/control/*.py` 不得 import `stableagent.execution/integrations`）；
  `test_p5_v1_mandate_construction_is_confined_to_control`——全库 AST 找
  `Mandate(...)` 构造调用点，`policy_version="p5-v1"` 字面量只允许出现在
  `stableagent/control/`（A2/L4 扫描半边；`orchestration/model.py` 默认值路径不含该字面量）。
- [x] **Step 1: 写失败的测试**：上述三个用例（第三个先对 `control/policy.py` 放行、
      人为在 docstring 外构造一个越界样例进红）。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_control_boundaries.py`
      Expected: 前两例 PASS（现状即满足），第三例红——若字面量断言写法未收口，先记红再收。
      Actual: 真实语料 `6 passed`（第三例没红：字面量写法在 Task 5/6 已收口，计划的条件分支走绿）；
      改以越界样例外挂证明第三例不是空断言——四份探针各自的红：
      `AssertionError: ['stableagent/control/policy.py', 'stableagent/orchestration/_p5_probe.py']`（holder 腿）、
      `FAILED :: test_no_legacy_package_imports_p5_layers` + `assert ['stableagent/orchestration/_p5_probe.py'] == []`（import 腿）、
      `FAILED :: test_control_never_imports_execution_or_integrations`（方向腿）、
      `AssertionError: [('stableagent/orchestration/_p5_probe.py', 7)]`（钉版构造腿）；探针随后删除、未入库。
- [x] **Step 3: 实现**：把 `policy.py` 的 `p5-v1` 收敛为单一模块常量
      `POLICY_VERSION = "p5-v1"`，扫描按"该常量的 import 方只允许 control 包"断言。
      实际落位：本 Task 无生产代码改动——单一常量在 Task 5 已是 `P5_POLICY_VERSION = "p5-v1"`
      （名字带层前缀，理由见第 1 节步骤 1.8），`grep -rn '"p5-v1"' stableagent/` 只剩该赋值与
      `orchestration/model.py` 的一句 docstring（docstring 不是代码，AST 扫描不读它）；
      import 方断言按计划的口径落地为 `_imports_version_constant`。
- [x] **Step 4: 跑到绿**：目标文件 + `test_orchestration_boundaries.py` 同跑（新旧矩阵并绿）。
      `19 passed`；全量 `475 passed, 1 skipped`、99.15%、mypy/ruff 三口绿；L1 与约束 1 各 0 行。
- [x] **Step 5: 提交**：`git commit -am "test: lock the p5 dependency direction across packages"`

### Task 9: M1 出口——门禁、账本回填与归属修正（步骤 1.9）

**Files:**
- Modify: `docs/acceptance/p5-guarded-execution.md`（1.1~1.9 勾选 + A1~A3、A5、B1~B4、L3、L4、
  L5（授权侧）证据回填）

- [x] **Step 1: 跑出口命令并收集输出**：
      Actual: 六条命令全绿，输出已抄进第 1 节步骤 1.9——`475 passed, 1 skipped` / mypy Success(41 files) /
      ruff check 全通过 / format 100 files already formatted / L1 与约束 1 各 0 行 / control 六文件 100%。

```bash
.venv/bin/pytest -q
.venv/bin/mypy
.venv/bin/ruff check stableagent tests examples benchmarks
.venv/bin/ruff format --check stableagent tests examples benchmarks
git diff 234f620..HEAD --stat -- stableagent/core/        # 必须 0 行（L1）
.venv/bin/pytest --cov=stableagent --cov-report=term-missing  # control 各文件 ≥90%
```

- [x] **Step 2: 复核心脏边界**：L2/L3/L4/L5 授权侧用例名与输出记进第 0 节对应条目。
      Actual: 四条 L 项各加一段「M1 出口复核」——L2（新旧矩阵 `19 passed` + grep 8 处引用全在 `control/` 内部）；
      L3（`control/` 零协程、事务只有 `cancel_ticket` 一处且回滚用例在场）；L4（行为 + 扫描两半边并绿）；
      L5（六时机常量 + 每判定 1 行 + Gate 三码 deny）。四条均未勾选，理由按 P4 口径写进条目本身。
- [x] **Step 3: 回填账本**：第 1 节 M1 九步全勾 + 实测；第 2 节 A1~A3、A5、B1~B4 勾选共
      **8 项**（A4 保持未勾——接线在 M2/M3，归属修正备注已有），计数改为 **DoD 8/46、
      里程碑 9/40**。口径（沿用 P4）：L1~L8 只有在其事实被**全部里程碑**完整证明后才勾，
      M1 只把边界用例名与输出写进对应条目，不提前勾选。
      Actual: 九步全勾；第 2 节本轮新勾 A2（三半边齐），A4 补归属修正备注，B2 因 Assignment 半边无处可管
      维持未勾 → 实勾 **7 项**而非计划的 8 项，计数 **DoD 7/46、里程碑 9/40**；L2/L3/L4/L5 只写复核不勾选。
- [x] **Step 4: 提交**：`git add -A && git commit -m "docs: close p5 m1 with measured evidence"`
- [x] **Step 5: 汇报**：向维护者给出 M2 开工分支与是否继续的确认点（M2 属新里程碑，
      按 P4 惯例出口后另行确认开工）。

## 6. M2 任务级实施计划（writing-plans 产出，2026-11-04）

> **状态：执行中（M2 Task 1~7 已收口，2026-11-05）。** 对应第 1 节 M2 的 12 个步骤（2.1~2.12），
> 展开为 **12 个 Task / 60 个 Step**。
> 执行分支 `feat/p5-m2-isolated-execution`（自 `main` 的 M1 收口提交 `5e94377` 切出）。
> 零语义改动约束沿用 M1：`stableagent/core/` 恒 0 行（L1）；`orchestration` 只允许**兼容性扩展**
> （`EffectKind` 加成员属 ADR-05 决策 5 点名允许，`service.py` 一字不动——约束 1）；`control/`
> 在 M2 不动（revalidate 接线在 M3）。
>
> 每个 Task 的固定节奏同第 5 节：Step 1 写失败测试 → Step 2 跑到红（命令 + 预期失败 + 实测
> Actual）→ Step 3 实现 → Step 4 目标文件 + 边界矩阵 + 全量跑绿 → Step 5 提交。Step 4/5 前
> `mypy`、`ruff check stableagent tests examples benchmarks`、`ruff format --check` 必须全绿。
>
> 写计划时已核实的现状（2026-11-04，`main@5e94377`）：`stableagent/execution/` 与
> `stableagent/integrations/` **不存在**；`stableagent/sandbox/` 只有一行占位 docstring；
> `EffectKind` 三成员（`launch_assignment`/`deliver_input`/`cancel_assignment`）在
> `orchestration/model.py:64-67`，`effects.kind` 是无 CHECK 的 TEXT；effect handler 类型是
> **async-only**（`outbox.py:18`），`EffectDispatcher` 断言不在事务内（L3 天然满足）；
> `pyproject.toml` **没有** `markers` 键；`stableagent/` 内 **零** `subprocess` 调用；仓库内
> 没有 slug / 短 id / 路径收口助手。
>
> 六处取向（先写理由，避免执行期再讨论）：
> ① `git` 经注入协议 `Git` 调用，真实实现用 argv 数组 + `shell=False`；测试用**真实临时仓库**
> （本机 git，离线，不违反 L8），不 mock git 输出。
> ② 路径收口只有一个实现 `execution.paths.confine()`，所有吃路径的 API 入口先过它（C4）。
> ③ 命名是纯函数、只吃 `Ticket`，签名里没有 request 文本，结构上做不到直通（C5）。
> ④ prepare 的幂等键 `prepare:{assignment_id}`，与 P4 的 `launch:{assignment_id}:{attempt}`、
> `cancel:{assignment_id}` 同形。
> ⑤ 任务顺序把 2.7（environment）提到 2.6（CommandRunner）之前：后者的 `env=` 参数要用前者。
> ⑥ 子进程环境在白名单四变量外只放 `PATH`（解析可执行文件必需）与固定 `LANG`，其余宿主变量
> 一律不继承——这是对规格 2.4 的一处细化，理由与实测写进 Task 6 证据。

### Task ↔ 里程碑步骤与 DoD 对照

| Task | 步骤 | 本 Task 产生的 DoD 证据 |
|---|---|---|
| 1 | 2.1 | C4、L2（execution 侧边界落位） |
| 2 | 2.2 | C5 |
| 3 | 2.3 | C3 |
| 4 | 2.4 | C2 |
| 5 | 2.5 | 生命周期口径（保留 + 显式清理） |
| 6 | 2.7 | C8、L7（环境半边） |
| 7 | 2.6 | C7 |
| 8 | 2.8 | D1 |
| 9 | 2.9 | C6 |
| 10 | 2.10 | D2 |
| 11 | 2.11 | D3、L8（Docker 半边） |
| 12 | 2.12 | C1、M2 出口 |

---

### Task 1: `execution/` 包骨架、独立版本与路径收口（步骤 2.1 前半）

**Files:**
- Create: `stableagent/execution/__init__.py`、`stableagent/execution/errors.py`、
  `stableagent/execution/paths.py`
- Test: `tests/unit/test_execution_boundaries.py`（新建）、`tests/unit/test_worktree.py`（新建，
  C4 首例）

**Interfaces:**
- Produces: `EXECUTION_SCHEMA_VERSION = "0.1.0"`（不进 `stableagent` 顶层重导出，同 control 口径）；
  `confine(root: Path, candidate: str | Path) -> Path`——`candidate.resolve()` 必须
  `is_relative_to(root.resolve())`，否则 `PathEscapeError`；`errors.py` 稳定 `.code` 家族
  （`execution.path_escape`/`worktree_reuse`/`command_denied`/`cleanup_unconfirmed`）。
- 边界：`execution/` 只允许 import `stableagent.control`/`orchestration`/`core`（方向
  execution → control → orchestration → core）；不得 import `stableagent.integrations`
  （尚不存在，名单先含）；`test_control_boundaries.py` 的 L2 语料按目录枚举自动把新包纳入
  旧包侧扫描，无需改名单。
- [x] **Step 1: 写失败的测试**：`test_execution_version_is_independent_and_not_reexported`；
  `test_execution_never_imports_integrations`；`test_confine_rejects_escape`
  （`../x`、绝对路径外部、指向外部的 symlink）、`test_confine_accepts_inside`。
  实际落位：另加 `test_execution_imports_only_the_layers_below_it`（把"只可 import 自身/control/
  orchestration/core"整条方向锁钉住，integrations 用例是它的子集）与
  `test_execution_error_code_inventory_is_unique_and_prefixed`（沿 M1 Task 1 先例：错误家族四个 code
  一次落地，用库存测试撑住 `errors.py` 的 100% 覆盖，消费者在 Task 3/5/10 各自补上）；
  路径用例拆成 C4 四例（穿越+外部绝对路径+`/etc/hosts` 同例、symlink 单例、错误详情单例），
  共 8 例分放两文件：`test_execution_boundaries.py` 4 例 + `test_worktree.py` 4 例。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_execution_boundaries.py
  tests/unit/test_worktree.py` Expected: FAIL — `ModuleNotFoundError: No module named
  'stableagent.execution'`（若收集期在更上游失败，按实测记 Actual）。
  Actual: FAIL — 两文件各自收集期 `ModuleNotFoundError: No module named 'stableagent.execution'`，
  `2 errors during collection`——M2 第一次计划预期红直接命中（M1 只命中过一次）。
- [x] **Step 3: 实现** `__init__.py`（docstring 写明"执行层不认识集成层"）、`errors.py`、
  `paths.py`；symlink 用例先 `Path.symlink_to` 造出真实链接再断言。
  实际落位：三个新文件共 37 stmts（`__init__` 3、`errors` 22、`paths` 12），全部 100%；
  `confine()` 对 root 自身放行（prepare 要在 root 下建目录）；symlink 用例是真造链接、不是 mock。
- [x] **Step 4: 跑到绿**：目标两文件 + `tests/unit/test_control_boundaries.py`（新包入矩阵后
  仍绿）+ 全量 `pytest -q`；`mypy`/`ruff check`/`ruff format --check` 全绿。
  首轮自红一处（测试侧，生产代码未改）：`test_execution_imports_only_the_layers_below_it` 把包内
  自引用 `stableagent.execution.errors` 当成越界——白名单补 `stableagent.execution` 前缀后绿。
  复跑：目标两文件 + control/orchestration 两矩阵 = `27 passed`；全量 `484 passed, 1 skipped`
  （+8）、总覆盖 99.16%；`mypy` Success（44 source files，+3）；`ruff check` All checks passed；
  `ruff format --check` 105 files already formatted；L1 `git diff 234f620..HEAD -- stableagent/core/`
  = 0 行、约束 1 `orchestration/service.py` = 0 行。
- [x] **Step 5: 提交**：`git commit -am "feat: open the execution package with path confinement"`

### Task 2: 命名纯函数（步骤 2.2）

**Files:**
- Create: `stableagent/execution/naming.py`
- Test: `tests/unit/test_worktree.py`（追加 C5 用例）

**Interfaces:**
- Produces: `ticket_short_id(ticket_id: str) -> str`（去掉 `tk-` 前缀后的前 8 个十六进制字符）；
  `slugify(title: str) -> str`（只留 `[a-z0-9-]`，折叠连字符、截到 40、空则 `ticket`）；
  `workspace_name(ticket: Ticket) -> str` = `sa-{short}-{slug}`；`WORKSPACE_RE` 常量形如
  `^sa-[0-9a-f]{8}-[a-z0-9-]{1,40}$`。三个函数都是纯函数、签名里没有 request/Issue 原文。
- [x] **Step 1: 写失败的测试**：注入语料 `["main; rm -rf /", "../../etc/passwd",
  "修复登录 bug", "🎉", "A"*200, "  ", "-leading-dash"]` → 每个结果都匹配 `WORKSPACE_RE`、
  不含语料里的 `/`/`;`/空格、不空；同 title 两次调用逐字节相同（确定性）；长度上限生效。
  （实际语料在计划 7 条之外补了第 8 条 `ISSUE-42: Push … pipe|grep $HOME`——反引号、管道、`&`、
  `$HOME` 一并进 banned 集合。）
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_worktree.py`
  Expected: FAIL — `ImportError: cannot import name 'workspace_name'`。
  Actual: `ModuleNotFoundError: No module named 'stableagent.execution.naming'`（收集期即红，比计划
  早一层——模块还不存在，谈不上挑名字）。
- [x] **Step 3: 实现** `naming.py`；CJK/emoji 全部被过滤后回落到 `ticket`（不许为空）。
- [x] **Step 4: 跑到绿**：目标文件 + 全量 + 三口径门禁。
  Actual: 第一次跑目标文件是 **11 failed / 10 passed**，根因在本 Task 的测试辅助函数——`Ticket.mandate_id`
  在 P4 是必填字段，`_ticket()` 漏传 → `pydantic_core.ValidationError: mandate_id Field required`；
  实现侧无过错。补 `mandate_id="md-naming"` 后目标文件 17 passed；全量 **497 passed, 1 skipped**、
  覆盖 99.17%、`naming.py` 100%；`mypy` Success（45 files）/`ruff check` 通过/`ruff format --check`
  106 files。偏差：`ticket_short_id` 对 `"tk-abc"` 这类不满 8 位十六进制的 id 抛 `ValueError` 而非
  静默截断（截断会产出不匹配 `WORKSPACE_RE` 的名字）；另新增计划接口外的 `is_workspace_name`，供
  Task 3 的 `prepare` 入口二次校验入参名字。
- [x] **Step 5: 提交**：`git commit -am "feat: derive branch and worktree names from the ticket alone"`
  → `6468c03`。

### Task 3: `Git` 协议 + `prepare` 与复用四要素（步骤 2.3）

**Files:**
- Create: `stableagent/execution/git.py`、`stableagent/execution/worktree.py`
- Test: `tests/unit/test_worktree.py`（追加 C3 用例，用真实临时仓库）

**Interfaces:**
- Produces: `Git` 协议（`run(args: Sequence[str], *, cwd: Path) -> GitResult`）+
  `SubprocessGit(timeout_s=30.0)`（argv 数组、`shell=False`、失败抛 `GitError` 带 returncode）；
  `WorktreeHandle`（`path`/`branch`/`ticket_id`/`base_ref`/`repository`/`ownership_file`）；
  `GitWorktreeManager(root: Path, *, git: Git, clock: Clock)`；
  `prepare(ticket: Ticket, *, repository: str, base_ref: str) -> WorktreeHandle`——`git worktree
  add -b <branch> <path> <base_ref>`，并在 worktree 根写 `.stableagent-worktree.json`
  （`ticket_id`/`branch`/`base_ref`/`repository`/`created_at`，**不含**任何凭据或宿主变量，L7）；
  复用判定读 ownership 文件 + `git remote get-url`，四要素（remote / ticket_id / base_ref /
  ownership 文件存在且可解析）全等才复用，否则 `WorktreeReuseError(diagnostics: dict)` 且
  **不清理**现场。
- [x] **Step 1: 写失败的测试**：`test_prepare_creates_worktree_and_ownership`；
  `test_prepare_reuses_identical_worktree`；`test_reuse_rejects_each_of_the_four_elements`
  （逐要素破坏：改 remote、改 ownership 的 ticket_id、改 base_ref、删/坏 ownership 文件
  → 四种拒绝 + 诊断字段齐全 + 目录仍在）。
  Actual: 用例名与本 Task 落地的略有出入，等价对应为
  `test_prepare_creates_worktree_branch_and_ownership`（多了分支名与起点断言：`HEAD` 等于新建受控
  分支、其 commit 等于 `release-2` 的 commit，并断言 git argv 逐项
  `("worktree","add","-b",branch,path,base_ref)`）、`test_prepare_reuses_the_identical_worktree`、
  `test_reuse_rejects_each_element_and_keeps_the_scene`（参数化 **六种**破坏：四种之外再加
  "ownership 缺失"与"ownership 分支标记改成 `main`"两种 ownership 形态）。诊断齐全口径钉死为
  六键 `{element, reason, path, ownership_file, recorded, requested}` 且 recorded/requested 同键集。
  计划外补三条：`test_ownership_never_records_credentials`（L7）、
  `test_prepare_rejects_a_repository_without_origin_remote`（读不到 origin 就拒绝、不留半成品）、
  `test_git_carries_only_four_environment_variables` + `test_scrub_credentials_*`（本 Task 自带的
  两个取向）。测试装置：真临时仓库（`_make_clone`）+ `RecordingGit` 包装真 git 记录 argv，
  全程离线。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_worktree.py`
  Expected: FAIL — `ImportError: cannot import name 'GitWorktreeManager'`。
  Actual: `ModuleNotFoundError: No module named 'stableagent.execution.git'`（收集期即红，同 Task 2
  的形态——模块还不存在）。
- [x] **Step 3: 实现** `git.py` + `worktree.py`；prepare 入口先 `confine(root, path)`；
  所有路径参数化（root 由调用方给临时目录）。
  五处口径决定（计划没写死，本 Task 定下并写进模块文档 string）：
  ① `GitError` **不进** `ExecutionError` 家族——放 `git.py`、继承 `RuntimeError`、带
  `returncode/stdout/stderr`：Task 1 的四个稳定码是"策略拒绝"的目录，git 进程失败不是；且
  `test_execution_error_code_inventory_is_unique_and_prefixed` 钉死了家族成员清单，加类等于改
  别人 Task 的用例。
  ② `repository` 参数 = **本地克隆根目录**；写进 ownership 与 `WorktreeHandle.repository` 的是该
  克隆 `git remote get-url origin` 的**脱敏身份**。复用比较的是"记录在案的身份 vs 当前身份"，
  这样 `set-url` 才真的能破坏要素①（worktree 与克隆共享 config，比"两边的 remote"是恒等式）。
  ③ 脱敏在传输层做：`scrub_credentials` 剥掉 `scheme://user:pass@` 段，`SubprocessGit` 的
  stdout/stderr 与 `GitError` 文本一律先过它——ownership 因此拿不到 token（L7）。
  ④ `SubprocessGit` 自带四项最小环境 `git_env()`（`PATH`/`LANG`/`GIT_TERMINAL_PROMPT=0`/
  `GIT_CONFIG_NOSYSTEM=1`，不含 `HOME`）：账本 L7 的白名单原本只约束 Agent 命令（Task 6），
  本层给自己的 git 调用也钉上，防凭据助手被拉进来。
  ⑤ ownership 缺失/不可解析时**先按要素④拒绝**：首次实现让它去跟"remote 不符"顶罪，跑出
  2 failed 后才改成现在的归因顺序。
- [x] **Step 4: 跑到绿**：目标文件 + 全量 + 三口径门禁；`git diff 234f620..HEAD --
  stableagent/core/` = 0（L1）。
  Actual: 归因顺序修正后目标文件 **29 passed**（+12）；全量 **509 passed, 1 skipped**、总覆盖
  99.13%；`worktree.py` 67 语句 **100%**、`git.py` 46 语句 96%——未覆盖的是超时分支
  `git.py:103-104`（真实超时无法确定性注入，留给后续 Task 以可注入 executable 的用例补，本 Task
  如实记为未覆盖面）；`mypy` Success（47 source files，+2）、`ruff check` All checks passed、
  `ruff format --check` 108 files already formatted；L1 与约束 1 diff 均 0 行。
- [x] **Step 5: 提交**：`git commit -am "feat: prepare reusable worktrees with four-element reuse checks"`
  → `250c990`（`git.py`/`worktree.py` 为新增文件，先 `git add` 再提交）。

### Task 4: `PREPARE_WORKTREE` effect 与崩溃重放（步骤 2.4）

**Files:**
- Modify: `stableagent/orchestration/model.py`（`EffectKind` 加 `PREPARE_WORKTREE = "prepare_worktree"`）
- Create: `stableagent/execution/effects.py`
- Test: `tests/unit/test_worktree.py`（追加 C2 用例）

**Interfaces:**
- Produces: `prepare_worktree_handler(manager: GitWorktreeManager) -> EffectHandler`——payload
  形状 `{"ticket_id", "assignment_id", "repository", "base_ref"}`，幂等键
  `prepare:{assignment_id}`；handler 是 `async def`（P4 handler 类型如此），内部直接调同步
  `manager.prepare`（dispatcher 保证不在事务内，L3 不受影响）。
- [x] **Step 1: 写失败的测试**：`test_prepare_effect_is_idempotent_on_replay`——真实 `Store` +
  真实临时仓库，同一 effect 入队、dispatch 两次（模拟崩溃后重放）→ 账上只有一条
  `prepare:` effect、`git worktree list` 只有一行、目录/分支名不变。
  Actual: 三条口径把计划里的"dispatch 两次"改硬了——① 重放走 `store.close()` 后的**全新连接**
  （"两次 dispatch"在同进程里可以由内存缓存作弊，换连接才证到事实源在库里）；② `git worktree list`
  只有一行的说法不准（主 worktree 也在名单里），改为断言路径集合 == `[clone, 我们的路径]`；
  ③ 补 `test_prepare_effect_key_is_scoped_to_the_assignment`（账上半边）与
  `test_prepare_effect_without_ticket_row_fails_loudly`（Ticket 行不在库里 → handler 抛
  `ValueError`，由 dispatcher 记 `last_error` 重试，不猜标题也不静默建现场）。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_worktree.py`
  Expected: FAIL — `AttributeError: PREPARE_WORKTREE`（枚举缺成员）。
  Actual: `ModuleNotFoundError: No module named 'stableagent.execution.effects'`（收集期即红，
  同 Task 2/3 的形态——枚举成员的红还没轮到就被 import 挡住了）。
- [x] **Step 3: 实现** 枚举成员 + `effects.py`；不碰 `store.py`（`kind` 是无 CHECK 的 TEXT）
  与 `service.py`（约束 1）。
  计划偏差一条（接口）：计划写的工厂是 `prepare_worktree_handler(manager)`，落地是
  `prepare_worktree_handler(manager, store)`——payload 里只有 `ticket_id`，而 `prepare` 需要
  `Ticket`（取 title 生成 slug），必须有读库入口；注入 `Store` 而不是塞一个 `Callable`，是因为
  P4 的 `scheduler._gate_launch` 已经是同款读法，跟着房子口径走。`model.py` 的改动只有枚举成员
  与其来源注释；`store.py`/`service.py` 零改动（约束 1 的 diff 复核 0 行）。
- [x] **Step 4: 跑到绿**：目标文件 + `tests/unit/test_outbox.py` / `test_scheduler.py`（既有
  effect 语义不受影响）+ 全量 + 三口径门禁。
  Actual: 全量第一次跑出一个**预期外的真红**——P4 的 `test_m2_effect_kinds_close_the_whitelist`
  钉的是"EffectKind 恰好三员"（P4 C1 白名单闭合），加成员必红。按计划 ADR-05 决策 5 这是点名
  允许的兼容性扩展，于是把该用例的断言集合扩到四员，并顺手加 L6 的"merge/auto_merge 不在值集合
  也不在成员名集合"不相交断言（见第 0 节 L6 进度）。改完：目标文件 32 passed（+3）、
  `test_worktree + test_orchestration_model + test_outbox + test_scheduler` = 98 passed、
  全量 **512 passed, 1 skipped**、覆盖 99.14%、`effects.py` 16 语句 100%；`mypy` Success
  （48 source files，+1）、`ruff check` 通过、`ruff format --check` 109 files；L1 与约束 1 均 0 行。
- [x] **Step 5: 提交**：`git commit -am "feat: drive worktree preparation through an outbox effect"`
  → `3682aba`；重放用例换连接的那次加固是提交后才做的（Step 1 的口径 ①），单独提交
  `0af9425 test: replay the prepare effect across a fresh connection`，不混进实现提交。

### Task 5: 生命周期——默认保留、显式清理（步骤 2.5）

**Files:**
- Modify: `stableagent/execution/worktree.py`
- Test: `tests/unit/test_worktree.py`（追加用例）

**Interfaces:**
- Produces: `GitWorktreeManager.cleanup(handle, *, confirm: bool = False,
  delete_branch: bool = False) -> None`——`confirm=False` 抛 `CleanupNotConfirmedError`
  （code `execution.cleanup_unconfirmed`）；`confirm=True` 走 `git worktree remove`，分支默认
  保留供审查，`delete_branch=True` 才 `git branch -D`。manager 无析构、无定时清理。
- [x] **Step 1: 写失败的测试**：`test_worktree_survives_ticket_end`（把一个 Ticket 推到处终态后
  目录仍在）；`test_cleanup_requires_confirmation`；`test_cleanup_keeps_branch_by_default`。
  三条按计划落位，另补三条同口径用例（`test_cleanup_deletes_branch_only_when_asked`、
  `test_cleanup_refuses_uncommitted_work_and_keeps_the_marker`、
  `test_cleanup_of_a_worktree_removed_by_hand_still_deletes_the_branch`）。终态那条不 mock：
  真 `Store` + 真 `OrchestrationService` 走 `queue → START → DELIVER → COMPLETE`，断言里除"目录仍在"
  还加了"录制不到任何删除 argv"——默认保留 ≠ 只有目录检查，还得证明没有别的路径会删。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_worktree.py`
  Expected: FAIL — `AttributeError: 'GitWorktreeManager' object has no attribute 'cleanup'`。
  Actual: **4 failed / 33 passed**，四条 cleanup 用例各一次 `AttributeError: 'GitWorktreeManager'
  object has no attribute 'cleanup'`——与预期同形。补测的第三条（脏内容被拒）与另两条也红在同一处。
  如实记一笔：`test_worktree_survives_ticket_end` **实现前就是绿的**，因为它是"没有第三条删除路径"
  的守卫断言而非新行为的红；这类用例的红不在 Step 2，而在"哪天有人加了自动删除它就会红"。
- [x] **Step 3: 实现** `cleanup`；拒绝消息带可执行的下一步（显式命令 + `confirm=True`）。
  `CleanupNotConfirmedError` 的消息由 `未确认清理 {path}：需要 confirm=True` 扩成带调用式的一句
  （类只吃 `path`，构造签名不动——`test_execution_boundaries.py` 的探针表钉着四元的错误家族）。
  计划偏差三处，都记在这儿：
  ① **不传 `--force`**：计划只写"`confirm=True` 走 `git worktree remove`"，实测 plain remove 会因
  我们自己的 ownership 标记（未跟踪文件）拒绝，所以实现是"按字节读出标记 → 摘掉 → plain remove →
  失败则写回标记再抛 `GitError`"。这样 git 的脏检查成了第二道锁：未提交内容不会被一次清理抹掉，
  失败的清理也不损坏复用四要素之④。
  ② **`WorktreeHandle` 加 `clone` 字段**（计划 Interfaces 未列）：`worktree remove`/`branch -D` 需要
  一个仍然存在的 worktree 当 cwd，被删目录自身不行；跟着 handle 走比给 `cleanup` 加 `repository`
  参数更好——计划的签名就此原样保留。
  ③ **无 `--force` 也不加 `discard` 参数**：规格 2.5 只要"显式调用 + 人工确认"，丢弃未提交内容的
  能力不在本步，需要时另开口径，不在此伪造。
- [x] **Step 4: 跑到绿**：目标文件 + 全量 + 三口径门禁。
  Actual: `tests/unit/test_worktree.py` **38 passed**（+6）；全量 **518 passed, 1 skipped**（+6）、
  总覆盖 99.14%、`execution/worktree.py` 82 语句 **100%**、`execution/errors.py` 22 语句 100%；
  `mypy` Success（48 source files）、`ruff check` All checks passed、`ruff format --check`
  109 files already formatted（`_removal_calls` 那条推导式被 ruff 重排过一次）；
  L1 与约束 1 diff 均 0 行，本轮 `orchestration/` 全包未碰。
- [x] **Step 5: 提交**：`git commit -am "feat: keep worktrees after tickets and require explicit cleanup"`
  → `cb7325f`（一笔含 `worktree.py` + `errors.py` 文案 + 六条用例；账本回填随后单独提交）。

### Task 6: 环境变量白名单（步骤 2.7）

**Files:**
- Create: `stableagent/execution/environment.py`
- Test: `tests/unit/test_command_runner.py`（新建，C8 用例）

**Interfaces:**
- Produces: `build_child_env(*, ticket_id, assignment_id, worktree: Path, repository: str,
  base_ref: str, host: Mapping[str, str] | None = None) -> dict[str, str]`——只产出
  `STABLEAGENT_TICKET_ID`/`STABLEAGENT_ASSIGNMENT_ID`/`STABLEAGENT_WORKTREE`/
  `STABLEAGENT_REPOSITORY`/`STABLEAGENT_BASE_REF` + `PATH`（取自宿主，非敏感、解析可执行文件
  必需）+ 固定 `LANG=C.UTF-8`；`host` 注入只为测试断言"其余变量一个不漏"。
- [x] **Step 1: 写失败的测试**：`test_child_env_is_an_allowlist`——宿主环境塞
  `GITHUB_TOKEN`/`AWS_SECRET_ACCESS_KEY`/`PYTHONPATH`/`SSH_AUTH_SOCK` → 子进程环境里一个都
  没有，只剩五加二；`test_child_env_has_no_secrets_from_arguments`（ticket_id 等按原值透传）。
  两条按计划落位，另补四条：`test_child_env_leaves_the_database_and_host_home_behind`
  （规格 2.3 R5 的"不给 P4 库位置与其他目录"）、`test_child_env_without_host_reads_only_path_from_the_process_environment`
  （生产路径：真 `os.environ` 里也只取 `PATH`，连必然存在的 `HOME` 都不带走）、
  `test_child_env_does_not_invent_a_path_when_the_host_has_none`（宿主没 `PATH` 就留空，不硬编码目录）、
  `test_child_process_sees_only_the_allowlist`（**真起子进程**读自己的环境——步骤 2.7 的验证栏写的是
  "子进程可见环境断言"，只断言函数返回值不够）。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_command_runner.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'stableagent.execution.environment'`。
  Actual: 同预期，**收集期即红**（`1 error during collection`，`tests/unit/test_command_runner.py:13`
  的 import 行），与 Task 2/3/4 的红形态一致。
- [x] **Step 3: 实现** `environment.py`；`PATH` 与 `LANG` 的取舍写进 docstring（对规格 2.4 的
  细化，账本 Task 6 证据里同样记录）。
  `source = os.environ if host is None else host`——`host` 参数只用于注入断言口径，生产路径读真环境但
  仍只取 `PATH` 一项；取向是"**没有第四个来源**"而不是"过滤已知密钥名"，所以宿主以后新增什么密钥
  都不需要回来改这里。细化三条（规格 2.4 只说"仓库信息等安全变量"，未点名 `PATH`/`LANG`）：
  ① `PATH` 必须给且取自宿主——没有它 `pytest`/`python` 这类命令名解析不了，硬编码目录列表等于替宿主
  决定哪些二进制可信，宿主没有 `PATH` 时留空不猜；② `LANG` 固定 `C.UTF-8` 而不继承宿主（`zh_CN.GBK`
  会改变子进程字节输出，Report 无法统一按 UTF-8 解释与脱敏）；③ 与 `git.py:git_env()` 的 `LANG=C`
  **刻意不同**并在 docstring 里写明理由——git 只吐我们按行解析的 plumbing 输出，Agent 命令要吐中文
  标题与 diff。两处计划外的补充：新模块常量 `CHILD_LANG`（把固定值从字面量里提出来，docstring 与
  实现同一处说话）、测试里的 `OS_INJECTED_KEYS`（见 Step 4）。
- [x] **Step 4: 跑到绿**：目标文件 + 全量 + 三口径门禁。
  Actual: 真子进程那条**第一次跑就红在实测细节上**——`set(seen) == CHILD_KEYS` 收到
  `Extra items in the left set: '__CF_USER_TEXT_ENCODING'`：macOS 的 libsystem 在 exec 时自己往环境里
  加这一键，与我们传的 `env` 无关，Linux CI 上不存在。没有把它加进白名单放宽断言，而是把"孩子没有第
  四个来源"改成带名单的说法（`set(seen) >= CHILD_KEYS` + 差集 ⊆ `OS_INJECTED_KEYS`，名单只有那一键并
  注明 Linux 上差集为空），红的那次输出如实留在账上；`ruff` 的 `SIM300` 又把 `CHILD_KEYS <= set(seen)`
  判成 Yoda 条件，改成 `set(seen) >= CHILD_KEYS`。绿：目标文件 **6 passed**、全量
  **524 passed, 1 skipped**（+6）、总覆盖 99.14%、`execution/environment.py` 8 语句 **100%**；
  `mypy` Success（49 source files，+1）、`ruff check` All checks passed、`ruff format --check`
  111 files already formatted；L1 与约束 1 diff 均 0 行（本轮只新增 `execution/` 一个文件与一个新测试文件）。
- [x] **Step 5: 提交**：`git commit -am "feat: hand the child process an allowlisted environment"`
  → `150e403`；真子进程那条探针是提交之后才补的（Step 1 的"子进程可见"半边），单独提交
  `5b54451 test: prove the child process itself sees only the allowlist`，不混进实现提交——同 Task 4 的口径。

### Task 7: `CommandRunner`——allowlist、cwd、超时、输出上限、脱敏（步骤 2.6）

**Files:**
- Create: `stableagent/execution/command.py`
- Test: `tests/unit/test_command_runner.py`（追加 C7 用例）

**Interfaces:**
- Produces: `CommandRequest(argv: tuple[str, ...], cwd: Path, timeout_s: float | None = None)`；
  `CommandResult`（`argv`/`exit_code: int | None`/`stdout`/`stderr`/`started_at`/`ended_at`/
  `truncated`/`timed_out`/`denied_reason: str | None`）；
  `CommandRunner(allowlist: frozenset[str], *, worktree_root: Path, max_output_bytes=64*1024,
  timeout_s=60.0, redact: tuple[str, ...] = ())`，`run(request) -> CommandResult`：
  ① `confine(worktree_root, request.cwd)`；② `argv[0]` 精确命中 allowlist；③
  `subprocess.run(list, cwd, env=build_child_env(...), timeout, capture_output=True,
  shell=False)`；④ stdout/stderr 先按 `redact` 逐个替换为 `***` 再截断（保留头部 +
  `...[truncated N bytes]` 尾标）。拒绝**不抛异常**：返回 `denied_reason` 非空的
  `CommandResult`（规格 2.4 要求被拒绝原因在结果里），且不 spawn。
- [x] **Step 1: 写失败的测试**（C7 五项各一）：allowlist 外的命令名被拒且无副作用；
  cwd 越出 worktree root 被拒（复用 Task 1 的 confine）；`sys.executable -c "import time;
  time.sleep(2)"` 配 `timeout_s=0.2` → `timed_out=True` 且 `exit_code is None`；打印 200KB
  → `truncated=True` 且长度 ≤ 上限；`redact=("tk-secret-value",)` → 输出里是 `***`。
  五项按计划各落一例，另补九例把口径钉住（相对 cwd、精确匹配的双向拒绝、默认超时、200 字节的小上限、
  空 redact 项、非零退出码、拒绝原因码表、放行路径的形状、以及 `build_child_env` 真被 spawn 用上的
  接线例 `test_spawned_child_sees_the_request_context`）。两处口径先定在测试里：
  ① **拒绝必须"无副作用"可证**——被拒的命令写成会在 worktree 里建文件的脚本，断言文件不存在，
  再加 `started_at == ended_at`（没 spawn 就只有一次时钟读数）；② **脱敏先于截断可证**——
  `100×'A' + 'SEKRET' + 100×'A'` 配 128 字节预算，先截后脱敏会让 `SEKRET` 活在结果里。
- [x] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_command_runner.py`
  Expected: FAIL — `ImportError: cannot import name 'CommandRunner'`。
  Actual: `ModuleNotFoundError: No module named 'stableagent.execution.command'`（收集期即红，比计划
  早一层：整个模块还不存在，还没轮到"名字导不到"；`ModuleNotFoundError` 是 `ImportError` 的子类）。
  实现后第一轮另有 **13 例红在测试装置自身**——`NameError: name 'FixedClock' is not defined`：
  Task 6 那一段没用过 `FixedClock`，我追加 C7 段时用了它却没补 import。补 `from tests.support import
  FixedClock` 后 20 passed，与实现无关，如实记在这儿。
- [x] **Step 3: 实现** `command.py`；不提供 `shell` 参数（结构性禁 `shell=True`）。
  `run(self, request)` 只有一个形参，签名里没有 `shell`；`subprocess.run` 那一项显式写 `shell=False`。
  计划偏差四处：
  ① **`CommandRequest` 加 `context: CommandContext` 字段**（计划的 dataclass 只有
  `argv/cwd/timeout_s`）：计划的 ③ 要求 `env=build_child_env(...)`，而它需要
  ticket_id/assignment_id/repository/base_ref，除了随请求走没有别的来源。放 request 不放 runner 的
  理由是 `worktree_root` 是**全部 Ticket 共用的根**，一个 runner 服务多个 Ticket；`worktree` 故意不进
  context——它是 `run()` 收口后的 cwd，由 runner 填，调用方没有"报一个别的目录"的余地。字段位置在
  `timeout_s` 之前（dataclass 不允许无默认值的字段排在有默认值的之后）。
  ② **构造函数加 `clock`**（计划未列）：`started_at`/`ended_at` 要有来源，房子口径是注入 `Clock`
  （`Store`、`GitWorktreeManager` 同形），测试绝不读墙钟；配套在测试里加了 `AdvancingClock`
  来证 `ended_at > started_at`（真跑过＝两次读数），拒绝路径断言两次读数相等。
  ③ **拒绝码表多一个 `empty_argv`**：空 argv 没有可判定的命令名，不挡就会 `IndexError`；
  码表写进 `command.py` 的 docstring（Task 9 的 `shell_syntax` 追加进同一张表）。
  ④ **`max_output_bytes` 语义定为"含尾标的总预算"**：Step 1 写的是"长度 ≤ 上限"，而尾标本身要占位——
  用 `dropped=len(data)` 那一份尾标形态预留（真实尾标的位数只会更少），于是"正文 + 尾标 ≤ 预算"恒成立；
  切口落在半个 UTF-8 字符上时 `decode(..., "ignore")` 丢掉那半个。附带一条：`subprocess.run` 显式
  带 `encoding="utf-8", errors="replace"`，不靠宿主 locale 猜孩子吐出的字节怎么解码（与孩子拿到的
  `LANG=C.UTF-8` 同源），因此这里的上限确实是**字节**数——与 `git.py` 的 `max_output_chars`
  （字符数）是两套口径，名字沿用计划不改。
  另记一条 spawn 语义：allowlist 里的裸名由 `execvp` 按**父进程** `PATH` 解析，孩子环境里那份 `PATH`
  只影响它以后再 spawn 的进程；名单里的名字不存在时 `FileNotFoundError` 原样抛出——那是部署配置错误，
  不是策略拒绝，吞掉它只会让"为什么什么都没跑"无处可查（本轮不为它加拒绝码，避免造计划外的策略）。
- [x] **Step 4: 跑到绿**：目标文件 + 全量 + 三口径门禁。
  Actual: 目标文件 **20 passed**（C7 段 14 例 + Task 6 的 6 例）、全量 **538 passed, 1 skipped**（+14）、
  总覆盖 99.17%、`execution/command.py` 89 语句 **100%**（单跑目标文件即 100%，不靠别的文件凑）；
  `mypy` Success（50 source files，+1）、`ruff check` All checks passed、`ruff format --check`
  112 files already formatted（一处 docstring 超 100 列被 `E501` 挡过，改行重写）；
  L1 与约束 1 diff 均 0 行，本轮 `orchestration/` 只被 import `Clock`，零改动。
- [x] **Step 5: 提交**：`git commit -am "feat: run allowlisted commands with timeouts and redaction"`
  → `e915dd3`。账本回填单独提交；步骤 2.6 的"**截断与脱敏入 Report**"半边本轮不勾（`CommandResult`
  还没有生产调用方），C7 的验证栏只要求五项有测试，故 C7 勾、2.6 记进度不勾。

### Task 8: `LocalRestrictedRunner` 与叙事纪律（步骤 2.8）

**Files:**
- Create: `stableagent/execution/sandbox.py`
- Test: `tests/unit/test_sandbox.py`（新建，D1 用例）

**Interfaces:**
- Produces: `SandboxRunner` 协议（`async def run(self, request: CommandRequest) ->
  CommandResult`，规格 2.4 原样）；`LocalRestrictedRunner(runner: CommandRunner)`——类文档
  明确"local-restricted 不是安全沙箱，DockerRunner 才是边界"；`CommandResult.summary() ->
  str` 生成 Report 用的一句话（含 `local-restricted`、退出码、是否截断，不含 stdout 原文、
  不含 "sandbox"）。
- [ ] **Step 1: 写失败的测试**：`test_local_restricted_never_says_sandbox`——`inspect.getdoc`
  取类文档、拒绝消息语料（allowlist 拒绝、cwd 拒绝）、`summary()` 三处都断言小写化后不含
  `"sandbox"` 且含 `"local-restricted"`；`test_runner_delegates_to_command_runner`。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_sandbox.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'stableagent.execution.sandbox'`。
- [ ] **Step 3: 实现** `sandbox.py`。
- [ ] **Step 4: 跑到绿**：目标文件 + 全量 + 三口径门禁。
- [ ] **Step 5: 提交**：`git commit -am "feat: add the local-restricted runner without sandbox claims"`

### Task 9: shell 语法拒绝与审计（步骤 2.9）

**Files:**
- Modify: `stableagent/execution/command.py`
- Test: `tests/unit/test_command_runner.py`（追加 C6 用例）

**Interfaces:**
- Produces: `SHELL_METACHARACTERS`（`;`、`&`、`|`、`$`、反引号、换行、回车）；任一 argv 元素包含其一
  → `denied_reason="shell_syntax"`、不 spawn、并经可注入的 `audit(request, reason)` 回调落
  一次拒绝记录（默认 no-op；M3 接到 Report/审计）。`inspect.signature(CommandRunner.run)`
  里没有 `shell` 形参（结构性）。
- [ ] **Step 1: 写失败的测试**：表驱动注入语料 `["echo; rm -rf /", "a|b", "a&b", "a$HOME",
  "`id`", "line\nbreak"]` 全部拒绝；audit 回调收到 6 次
  `(request, "shell_syntax")`；`test_run_has_no_shell_switch`。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_command_runner.py`
  Expected: FAIL — 首个语料 `echo; rm -rf /` 未被拒（`denied_reason is None`）。
- [ ] **Step 3: 实现** 字符表 + 审计回调；`shell_syntax` 进 `errors.py` 的稳定码家族说明
  （拒绝原因码表写进 `command.py` docstring）。
- [ ] **Step 4: 跑到绿**：目标文件 + 全量 + 三口径门禁。
- [ ] **Step 5: 提交**：`git commit -am "feat: reject shell syntax at the command boundary"`

### Task 10: `DockerRunner` 参数装配（步骤 2.10）

**Files:**
- Create: `stableagent/execution/docker.py`
- Test: `tests/unit/test_sandbox.py`（追加 D2 用例）

**Interfaces:**
- Produces: `DockerRunner(image, *, allowlist, worktree_root, network_profiles: Mapping[str,
  Sequence[str]], cpus=1.0, memory="512m", pids_limit=128, timeout_s=300.0)`；
  `build_argv(request, *, network_profile: str | None = None) -> list[str]` 为纯函数：
  `docker run --rm --network <none|profile 名> --read-only --tmpfs /tmp --pids-limit N
  --memory M --cpus C --security-opt no-new-privileges --cap-drop ALL -v <worktree>:/workspace
  -w /workspace -e ... <image> <argv...>`；结构性不出现 `--privileged`、docker socket 挂载、
  `--net=host`。类 docstring 给 digest pin 指南（`image@sha256:...`）。
- [ ] **Step 1: 写失败的测试**：`test_docker_argv_defaults_are_locked_down`（逐项断言
  `--network none`、`--read-only`、`--pids-limit`、`--memory`、`--cpus`、
  `no-new-privileges`、`--cap-drop ALL`、只挂本 worktree）；`test_docker_argv_never_contains_dangerous_flags`
  （argv 全文不含 `--privileged`、`docker.sock`、`host` 网络）；`test_network_profile_only_when_defined`
  （未定义 profile 抛 `CommandRejectedError`，定义后取 profile 名）。全部**不启动容器**。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_sandbox.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'stableagent.execution.docker'`。
- [ ] **Step 3: 实现** `docker.py`；镜像 pin 指南进 docstring（D2 的证据之一）。
- [ ] **Step 4: 跑到绿**：目标文件 + 全量 + 三口径门禁。
- [ ] **Step 5: 提交**：`git commit -am "feat: assemble locked-down docker run arguments"`

### Task 11: Docker 测试双条件与 marker（步骤 2.11）

**Files:**
- Modify: `pyproject.toml`（`[tool.pytest.ini_options]` 增加 `markers`）
- Create: `tests/integration/test_docker_runner.py`
- Test: `tests/unit/test_sandbox.py`（追加 D3 用例）

**Interfaces:**
- Produces: marker `docker: 需要真实 Docker 守护进程（同时设置 STABLEAGENT_DOCKER=1 才运行）`；
  `execution.docker.docker_tests_enabled(env: Mapping | None = None) -> bool`——两个条件
  （`STABLEAGENT_DOCKER == "1"`）就位才返回真；集成用例 `pytestmark = [pytest.mark.docker,
  pytest.mark.skipif(...)]`，skip 原因写明环境变量名。
- [ ] **Step 1: 写失败的测试**：`test_docker_marker_is_registered`（读 `pyproject.toml` 断言
  marker 文本在位——注册缺失时 `--strict-markers` 之外不报错，只能这样钉）；
  `test_docker_tests_require_both_conditions`（`{}` → False、`{"STABLEAGENT_DOCKER": "1"}` →
  True）；集成文件在默认环境下被 skip。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/unit/test_sandbox.py`
  Expected: FAIL — marker 未注册 / `docker_tests_enabled` 不存在。
- [ ] **Step 3: 实现** marker 与开关函数。
- [ ] **Step 4: 跑到绿**：`pytest -q -rs` 输出 skip 原因可见（`STABLEAGENT_DOCKER`），全量绿
  （L8：核心门禁不受 Docker 可用性影响）+ 三口径门禁。
- [ ] **Step 5: 提交**：`git commit -am "test: gate docker cases on a marker and an env var"`

### Task 12: M2 出口——并发目录互不污染与账本回填（步骤 2.12）

**Files:**
- Create: `tests/integration/test_worktree_concurrency.py`
- Modify: `docs/acceptance/p5-guarded-execution.md`（2.1~2.12 勾选 + C1~C8、D1~D3、L3/L7/L8
  对应证据；第 3 节命令表补并发用例）
- Test: 同文件

**Interfaces:** 无新生产接口。
- [ ] **Step 1: 写失败的测试**：`test_parallel_tickets_get_disjoint_worktrees`——同一仓库同一
  base ref 的两个 Ticket 用 `asyncio.gather` 并发 prepare：目录名/分支名不同、
  `git worktree list` 恰两行、两份 ownership 文件各自正确、互不覆盖；再并发跑两条命令，
  stdout 各自只在自 worktree。
- [ ] **Step 2: 跑到红**：`.venv/bin/pytest -q --no-cov tests/integration/test_worktree_concurrency.py`
  Expected: FAIL — 首次运行暴露的共享状态（若无共享态则记为"一次即绿"，如实记录）。
- [ ] **Step 3: 实现**：按红因修 `GitWorktreeManager`；若一次即绿，不改生产代码。
- [ ] **Step 4: 跑到绿**：出口命令全套（第 3 节 + `pytest -q`、`mypy`、ruff 两口、L1 diff 0、
  `service.py` diff 0、`execution/` 覆盖率逐个 ≥90%）。
- [ ] **Step 5: 提交**：`git add -A && git commit -m "feat: close p5 m2 with parallel worktree evidence"`
