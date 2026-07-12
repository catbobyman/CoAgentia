# M6a code-review 修复交接（HANDOFF · 修复进行中，未提交）

> 建立：2026-07-11。定位：T1（M6a 收口段）「统一修复 10 findings」阶段**进行到一半**，owner 打断要求先写交接。
> **工作树有未提交改动，勿盲目提交**——5 个 server 测试仍红（根因已诊断，见 §4）。接续先读本文。

## 0. 一句话状态

M6a 实机 verify 20/20 已收口提交（HEAD `bc70cd5`）。之后按 owner 拍板「#1 后台化 / #7 保持安全删死代码 / 其余全修 + 复验」**开始统一修复 10 个 code-review findings**：**10 条产品代码 + 契约已全部改完**，ruff 绿、`pnpm gen` 绿、daemon 全套 **139 passed**（含重写的 `test_worktree_handlers` 6/6）。**剩 5 个 server 测试红**（全因 #3 复验引入的握手期额外帧），加一批**待写的新增回归测试**、web vitest、pyright、m6a_verify 复验、收口提交尚未做。

## 1. owner 决策（本阶段执行依据）

- **#1**：后台化 worktree 处理器（采纳设计 workflow 的 **Design C**：保序后台化，ack 仍在 op 完成后发，server 侧零改动——非 checks 式「受理即 DONE」）。
- **#7**：保持投递语义不变（连续前缀防丢消息是必要权衡），仅删死代码 `_filter_gated`。
- **其余（#2/#3/#4/#5/#6/#8/#9/#10）**：全修 + 复验。

## 2. 设计规格来源（完整规格，接续可查）

10 条 finding 的精确修复规格由并行设计 workflow 产出（10 agent，approach_clear 全 true）：
- 完整 JSON：`journal.jsonl` = `C:\Users\18092\.claude\projects\D--Project4work-Agenthub-7-8\e2bc6f7b-483f-453d-bac4-2b74843b8c22\subagents\workflows\wf_1a7815e9-bed\journal.jsonl`（每行一 agent 的完整 target_changes/regression_tests/gotchas）
- 亦见任务输出：`...\tasks\w716o8j1b.output`（temp，可能被清；journal 更持久）
- **重跑复用**：`Workflow({scriptPath: "...\workflows\scripts\m6a-fix-design-wf_1a7815e9-bed.js", resumeFromRunId: "wf_1a7815e9-bed"})` 缓存命中即返回。
- findings 清单本身在 [CURRENT-HANDOFF.md §4a](CURRENT-HANDOFF.md)。

## 3. 已完成的修复（**产品代码 + 契约，未提交**）

未提交文件（`git status --porcelain`）：
```
 M apps/daemon/src/coagentia_daemon/client.py          # #1 后台通道
 M apps/daemon/src/coagentia_daemon/git.py             # #8 单进程 diff
 M apps/daemon/tests/test_worktree_handlers.py         # #1 测试重写（6/6 绿）
 M apps/server/src/coagentia_server/computers/__init__.py   # #5 导出 GitQueryError
 M apps/server/src/coagentia_server/computers/hub.py   # #2/#3/#5/#7/#10
 M apps/server/src/coagentia_server/messages/service.py     # #6 post_system_message card_kind 参数
 M apps/server/src/coagentia_server/routes/tasks.py   # #5 get_task_diff 分类
 M apps/server/src/coagentia_server/system_nodes/service.py # #4/#9/#6 后端
 M apps/server/src/coagentia_server/worktrees/service.py    # #3 revalidation_plans
 M apps/web/src/components/MessageFlow.tsx             # #6 前端按 card_kind 判定
 M packages/contracts/src/coagentia_contracts/enums.py      # #6 CardKind.MERGE_CONFLICT
 M packages/contracts-ts/src/generated/models.ts      # #6 pnpm gen 产物
 M packages/contracts-ts/src/generated/rest.ts        # #6 pnpm gen 产物
?? scratchpad/{m6a_keep,m6a_run,pytest_after_fix}.log # 临时日志，勿提交（可删）
```

| # | 判 | 实现摘要（已落地） | 复验测试状态 |
| --- | --- | --- | --- |
| 1 | CONFIRMED | client.py：`_BACKGROUND_INSTRS`（worktree ensure/merge/cleanup 从 `_STATUS_REPLAY_INSTRS` 移出，后者仅留 CHECK_RUN）；`handle_instr` 顶部分流后台化；新增 `_spawn_worktree_instr`/`_run_worktree_instr`（单车道 `_worktree_lane` 锁 + handler 内先报 status 后发 ack，保序）/`_finish_worktree_task`/`_wait_worktrees_closed`；`stop()`/`shutdown()` 生命周期。handlers.py **不改**。 | ✅ test_worktree_handlers 重写 6/6 绿（含 reader-不阻塞/硬失败 ack FAILED/shutdown 取消回归） |
| 8 | CONFIRMED | git.py `diff()`：删逐文件 spawn，改一次全量 `git diff` + 新增 `_split_diff_sections`（按 `diff --git ` 头切分）+ 按位 zip metadata + `len(sections)!=len(metadata)` fail-closed 守卫。 | ⬜ 现有 test_git_diff 应仍绿（未单独确认）；待加「进程数=常数」计数测试 + 切分单测 |
| 4 | CONFIRMED | system_nodes `_create_conflict_task` 顶部幂等查：本 merge 节点已有未终态同树（project_id/path/branch）「解决冲突」派回任务则 `return` 复用；import 加 TaskStatus/CardKind。 | ⬜ test_system_nodes 全过（向后兼容）；待加同一冲突重复→无重复建、二次真冲突→建新 两个专项 |
| 9 | PLAUSIBLE | system_nodes `apply_merge_result` 加 `reconciled: set`；`_merge_step_succeeded` 加 `reconciled` 参数，alias 更新+WORKTREE_UPDATED 广播按 `worktree_row["id"]` 去重；进展消息/诊断 per-node 保留。 | ⬜ 向后兼容过；待加菱形广播一次专项 |
| 6 | CONFIRMED | enums CardKind 加 `MERGE_CONFLICT`；`pnpm gen` 已重生成 TS；messages `post_system_message` 加 `card_kind` 参数；system_nodes 冲突 anchor 传 `card_kind=CardKind.MERGE_CONFLICT`；前端 MessageFlow 第 82/95 行改按 `m.card_kind === 'merge_conflict'` 判定（保留 parseConflictFiles 拿文件清单）。 | ⬜ web vitest 未跑；待改 MessageFlow.conflict.test fixture 加 card_kind + 加假冲突卡负例；后端待断言 anchor card_kind |
| 2 | CONFIRMED | hub 常量 `_WORKTREE_ENSURE_ESCALATE_AFTER=3`；`_record_worktree_failure` 重写：归属 owner/channel/workspace（查 task）+ `.returning(seq)` + `DIAGNOSTIC_APPENDED` emit（owner 非空）+ ensure 失败严格 `==` 阈值触发 `_escalate_worktree_failure`（新增：主流系统消息 + `emit_activity(FAIL_CLOSED)` 给人类）。 | ⬜ 待改 test_ensure_failed_holds…（断言诊断归属 owner/channel）+ 加 DIAGNOSTIC_APPENDED WS 订阅测试 + 第 3 次升级一次性测试 |
| 3 | CONFIRMED | worktrees 新增 `revalidation_plans`（仅 active + 未终态，含 __all__）；hub `_ensure_worktree(revalidate=)` active 行走 revalidation_plans；`reconcile(revalidate_worktrees=)` + serve 握手传 True（周期 loop 不传）。 | ⬜ **是 5 个红测试的根因**（见 §4）；待加复验专项（reconnect 下发 ensure / 周期不下发 / conflicted 不复验 / plan 单测） |
| 5 | CONFIRMED | hub 新增 `class GitQueryError(Exception)`（**不继承** DaemonOffline）；`query_git_diff` error 分支改抛 GitQueryError；computers/__init__ 导出；routes/tasks `get_task_diff` 加 `except GitQueryError → 422 VALIDATION_FAILED`，DaemonOffline 分支收敛纯 503 删 prose 子串。 | ⬜ 待改 test_diff（见 §4） |
| 7 | 拍板保留语义 | hub 删 `_filter_gated`（零引用）；rationale 折进 `_filter_agent_delivery` docstring。`_filter_agent_delivery` 未动。 | ✅ 无新测试（死代码删除，全 grep 零引用） |
| 10 | PLAUSIBLE | hub `_report_worktree_status` merged-缺-commit 分支加 fail-closed 注释（仅注释，无行为变更；breadcrump 可选项未做）。 | ✅ 现有 test 应仍绿 |

**守门现状**：`uv run ruff check apps packages` = All checks passed；`pnpm gen` = diff 仅 CardKind；`uv run pytest apps/daemon -q` = **139 passed / 4 skipped**；pyright/vitest/build **未跑**。

## 4. 阻塞：5 个 server 测试红（根因 = #3 复验副作用，已诊断）

**根因**：#3 复验让 reconnect 握手 `reconcile(revalidate_worktrees=True)` 对**既有 active worktree 行**下发 `worktree.ensure`。凡「握手时已 seed active worktree 行」的测试，这个复验 ensure 帧插在它们期待的帧（git.diff query / agent.wake / ping-pong）**之前**，打乱帧序。已确证：
- `test_cleaned_report_emits_original_and_changed_alias_once`：`daemon.sync()` 的 pong 断言收到 `worktree.ensure`（daemon_helpers.py:375）。
- `test_diff_query_timeout_is_daemon_offline` / `test_diff_proxies_query_and_task_detail_derives_worktree` / `test_diff_query_failure_uses_existing_daemon_offline_family`：`daemon.recv()` 期待 `git.diff` query，先收到复验 `worktree.ensure`。
- `test_briefing_delivery_injects_copy_without_mutating_db`：`_ack_activation`（test_worktree_lifecycle.py:249）期待 `agent.wake`，先收到 `worktree.ensure`。

**修法（推荐 a）**：
- **(a)** 更新这 5 个测试：握手（`hello`+`recv_hello_ack`）后、任何原帧序动作前，先**消费并 ack 复验 ensure**（`recv_instr` → 若 `type=='worktree.ensure'` 则 `report worktree.status {status:'active', ...}` + `ack done`）。建议在 `daemon_helpers.py` 加一个 `drain_revalidation(daemon, *, count)` 辅助。**注意两个坑**：
  1. 复验 `send_instr(ensure)` 会等 ack，StubDaemon 不 ack 会 10s 超时（这也是全量套件曾显慢的原因）——测试必须 ack 复验帧。
  2. 复验是后台 `_spawn_on_conn(reconcile)`，与测试的 `client.get`/`report` 交织，帧到达时序可能不确定（flaky 风险）——接续者需确认复验帧在握手后、测试动作前确定性到达，或用「recv 循环容忍 ensure 在任意位置」的健壮消费。`_aliased_worktrees` 有 original+alias 两个 active 行（不同 task），复验可能下发**2 个** ensure，`drain` 需按行数消费。
- **(b) 备选**（若 (a) 的 flaky/侵入过大）：重审 #3 复验实现是否可更小侵入——但规格明确「reconnect 握手复验 active 行」，改保守可能达不到修复目的。**倾向 (a)**，(b) 仅在 (a) 证明不可控时上报 owner。
- **额外**：`test_diff_query_failure_uses_existing_daemon_offline_family` 除帧序外，还需 **#5 语义改**：断言从 `503 DAEMON_OFFLINE` 改 `422 VALIDATION_FAILED`（message 透传 git prose），建议重命名 `test_diff_query_failure_is_validation_error`。`test_diff_query_timeout`/`test_diff_without_daemon_connection` 保持 503。

## 5. 剩余待办（按序）

1. **修 5 个红测试**（§4）——先做，让基线回绿。
2. **新增回归测试**（设计 workflow 每条 finding 的 regression_tests，见 §2 journal）：
   - #2 诊断归属 + DIAGNOSTIC_APPENDED WS + 第 3 次升级一次性 + 无 owner/cleanup 不升级。
   - #3 reconnect 复验下发 ensure / 周期不下发 / conflicted 不复验 / `revalidation_plans` 单测。
   - #4 同冲突重复→不重复建 / 二次真冲突→建新。
   - #9 菱形 alias 广播一次（进展消息仍 per-node 两条）。
   - #5 坏 base ref → 422（已在 §4 的 test_diff 改造覆盖）。
   - #6 后端 anchor `card_kind=='merge_conflict'`；前端 vitest fixture 加 card_kind + 假冲突卡负例（标题含「冲突文件:」但无 card_kind → 渲染 TaskChip 不渲染冲突卡）。
   - #8 diff 子进程数=常数计数测试 + `_split_diff_sections` 单测（含内容行含 `+diff --git`、纯 rename、binary 段）。
3. **web vitest**：`pnpm -F @coagentia/web test`（#6 fixture 更新后）。
4. **pyright**：`pnpm typecheck`（**未跑，可能有类型债**——注意 hub 新增 `_escalate_worktree_failure`/`_record_worktree_failure` 的 `task` 是 RowMapping、`dict(task)`；`_merge_step_succeeded` 新参 `reconciled: set[str] | None`；client.py 后台 task dict）。
5. **build**：`pnpm -F @coagentia/web build`。
6. **实机复验**：重跑 `uv run python scratchpad/m6a_verify.py`（应仍 20/20；#1 后台化后可专门观察 reader 不阻塞；#5 可加坏 base ref → 422 探针）。
7. **/code-review high** 复审修复（可选，owner 已在 T1 跑过一轮）。
8. **收口提交**：全绿后一次提交（守门数字 813→新基线，vitest 194→新，pyright 0）；同步 CURRENT-HANDOFF/DEV-PLAN（#4a findings 标记 fixed、DEV-PLAN /code-review 行由 🔶 转 ✅）+ PROJECT-RECORD；本文可移 archive/ 或删。

## 6. 守门命令

```
uv run pytest -q                    # 目标：813 起点只增不减（修红 + 新增测试后）
pnpm -F @coagentia/web test         # vitest（#6 fixture 更新后）
pnpm typecheck                      # pyright 0（未跑，重点复查）
uv run ruff check apps packages     # 已绿
pnpm gen                            # 已绿（仅 CardKind）
pnpm -F @coagentia/web build
uv run python scratchpad/m6a_verify.py   # 实机复验 20/20
```

## 7. 注意事项

- **勿提交**直到 5 红回绿 + 新测试 + 全守门绿（owner 明确要先停）。scratchpad/*.log 勿入提交。
- #1 后台化的正确性关键：ack **仍在 op 完成后**发（保序），异常仍转 ack FAILED（server fail_dispatch 不变），仅 shutdown 取消在飞任务（断连不取消）——已被 test_worktree_handlers 6 个用例锁定，改动勿破坏这些不变量。
- #3 是本轮唯一有较大测试外溢的 finding；若 (a) 方案的 flaky 不可控，停下上报 owner 再定，勿硬压。
- 契约改动（#6 CardKind）：`messages.card_kind` 是 TEXT+CHECK（native_enum=False），fresh `alembic upgrade head` 自带新 CHECK（测试全新建库故通过，无需新迁移）；**已有持久生产库会拒新值**——本仓无 remote/无持久生产库，可接受，但收口文档需注明。
